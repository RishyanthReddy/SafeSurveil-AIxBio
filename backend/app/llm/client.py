from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from http.client import IncompleteRead
import json
import math
from numbers import Real
import re
from typing import Any, Callable, Generic, Mapping, Sequence, TypeVar
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from pydantic import ValidationError

from app.contracts import CopilotResponse, QueueItem, SemanticUIObject
from app.settings import AppSettings, LLMSettings

_RETRYABLE_HTTP_STATUS_CODES = {408, 429, 500, 502, 503, 504}
_RETRYABLE_TRANSPORT_EXCEPTIONS = (IncompleteRead, TimeoutError, ConnectionError)
_NUMERIC_FIELD_TOKEN_PATTERN = re.compile(r"[^a-z0-9]+")
_SIGNAL_TEXT_TOKEN_PATTERN = re.compile(r"[^a-z0-9]+")
_SEMANTIC_UI_NUMERIC_FIELD_ALIASES = {
    "probability": "probability",
    "prediction_probability": "probability",
    "actionability": "actionability_score",
    "actionability_score": "actionability_score",
    "qc": "qc_risk",
    "qc_risk": "qc_risk",
    "quality_risk": "qc_risk",
    "quality_control_risk": "qc_risk",
    "novelty": "novelty_score",
    "novelty_score": "novelty_score",
    "novelty_percentile": "novelty_percentile",
    "novelty_risk": "novelty_risk",
    "metadata": "metadata_completeness",
    "metadata_completeness": "metadata_completeness",
    "uncertainty": "uncertainty_score",
    "uncertainty_score": "uncertainty_score",
    "entropy": "prediction_entropy",
    "prediction_entropy": "prediction_entropy",
    "nearest_neighbor": "nearest_neighbor_distance",
    "neighbor_distance": "nearest_neighbor_distance",
    "nearest_neighbor_distance": "nearest_neighbor_distance",
    "queue_priority": "queue_priority",
    "priority": "queue_priority",
    "ambiguous_base_fraction": "ambiguous_base_fraction",
    "ambiguous_fraction": "ambiguous_base_fraction",
    "sequence_count": "sequence_count",
    "total_sequences": "sequence_count",
    "total_bases": "total_bases",
    "base_count": "total_bases",
}
_QUEUE_ITEM_MODEL_VISIBLE_FIELDS = {
    "job_id",
    "sample_id",
    "target_drug",
    "triage",
    "severity",
    "status",
    "queue_priority",
    "headline",
    "rationale_codes",
}


class LLMClientError(RuntimeError):
    """Base error for provider-agnostic LLM client failures."""


class LLMClientConfigurationError(LLMClientError):
    """Raised when LLM configuration is missing or unsupported."""


class LLMResponseValidationError(LLMClientError):
    """Raised when a model response cannot be validated against a contract."""


class LLMMessageRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class LLMOutputFormat(str, Enum):
    TEXT = "text"
    JSON = "json"


@dataclass(frozen=True)
class LLMMessage:
    role: LLMMessageRole | str
    content: str
    name: str | None = None
    tool_call_id: str | None = None

    def __post_init__(self) -> None:
        normalized_role = (
            self.role
            if isinstance(self.role, LLMMessageRole)
            else LLMMessageRole(str(self.role).strip().lower())
        )
        normalized_content = self.content.strip()
        if not normalized_content:
            raise ValueError("LLMMessage content must not be empty.")
        normalized_name = self.name.strip() if self.name is not None else None
        if normalized_name == "":
            normalized_name = None
        normalized_tool_call_id = (
            self.tool_call_id.strip() if self.tool_call_id is not None else None
        )
        if normalized_tool_call_id == "":
            normalized_tool_call_id = None
        if normalized_role is LLMMessageRole.TOOL and normalized_tool_call_id is None:
            raise ValueError("Tool messages require tool_call_id.")
        object.__setattr__(self, "role", normalized_role)
        object.__setattr__(self, "content", normalized_content)
        object.__setattr__(self, "name", normalized_name)
        object.__setattr__(self, "tool_call_id", normalized_tool_call_id)

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "role": self.role.value if isinstance(self.role, LLMMessageRole) else str(self.role),
            "content": self.content,
        }
        if self.name:
            payload["name"] = self.name
        if self.tool_call_id:
            payload["tool_call_id"] = self.tool_call_id
        return payload


@dataclass(frozen=True)
class LLMRequest:
    operation: str
    messages: Sequence[LLMMessage | Mapping[str, Any]]
    metadata: Mapping[str, str] = field(default_factory=dict)
    output_format: LLMOutputFormat | str = LLMOutputFormat.JSON
    timeout_seconds: int = 30
    retry_count: int = 2
    model: str | None = None
    max_output_tokens: int | None = None
    reasoning_enabled: bool | None = None

    def __post_init__(self) -> None:
        normalized_operation = self.operation.strip().lower().replace(" ", "_")
        if not normalized_operation:
            raise ValueError("LLMRequest operation must not be empty.")
        normalized_messages = tuple(
            item
            if isinstance(item, LLMMessage)
            else LLMMessage(**dict(item))
            for item in self.messages
        )
        if not normalized_messages:
            raise ValueError("LLMRequest requires at least one message.")
        if self.timeout_seconds <= 0:
            raise ValueError("LLMRequest timeout_seconds must be positive.")
        if self.retry_count < 0:
            raise ValueError("LLMRequest retry_count must be zero or greater.")
        if self.max_output_tokens is not None and self.max_output_tokens <= 0:
            raise ValueError("LLMRequest max_output_tokens must be positive when provided.")
        normalized_metadata = {
            str(key).strip().lower().replace(" ", "_"): str(value).strip()
            for key, value in self.metadata.items()
            if str(key).strip() and str(value).strip()
        }
        normalized_model = self.model.strip() if self.model is not None else None
        if normalized_model == "":
            normalized_model = None
        normalized_output_format = (
            self.output_format
            if isinstance(self.output_format, LLMOutputFormat)
            else LLMOutputFormat(str(self.output_format).strip().lower())
        )
        object.__setattr__(self, "operation", normalized_operation)
        object.__setattr__(self, "messages", normalized_messages)
        object.__setattr__(self, "metadata", normalized_metadata)
        object.__setattr__(self, "output_format", normalized_output_format)
        object.__setattr__(self, "model", normalized_model)


@dataclass(frozen=True)
class LLMUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True)
class LLMClientResponse:
    provider: str
    model: str | None
    output_text: str | None = None
    output_json: Mapping[str, Any] | None = None
    raw_payload: Any | None = None
    response_id: str | None = None
    finish_reason: str | None = None
    usage: LLMUsage | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)
    attempt_count: int = 1

    def __post_init__(self) -> None:
        normalized_provider = self.provider.strip().lower()
        if not normalized_provider:
            raise ValueError("LLMClientResponse provider must not be empty.")
        normalized_model = self.model.strip() if self.model is not None else None
        if normalized_model == "":
            normalized_model = None
        normalized_text = self.output_text.strip() if self.output_text is not None else None
        if normalized_text == "":
            normalized_text = None
        if normalized_text is None and self.output_json is None:
            raise ValueError("LLMClientResponse requires output_text or output_json.")
        if self.attempt_count <= 0:
            raise ValueError("LLMClientResponse attempt_count must be positive.")
        object.__setattr__(self, "provider", normalized_provider)
        object.__setattr__(self, "model", normalized_model)
        object.__setattr__(self, "output_text", normalized_text)


ModelT = TypeVar("ModelT")


@dataclass(frozen=True)
class ValidatedLLMResponse(Generic[ModelT]):
    response: LLMClientResponse
    parsed: ModelT


@dataclass(frozen=True)
class OpenRouterTransportRequest:
    method: str
    url: str
    headers: dict[str, str]
    body: bytes
    timeout_seconds: int


@dataclass(frozen=True)
class OpenRouterTransportResponse:
    status_code: int
    body: bytes
    content_type: str | None = None


OpenRouterTransport = Callable[[OpenRouterTransportRequest], OpenRouterTransportResponse]


def _default_transport(request: OpenRouterTransportRequest) -> OpenRouterTransportResponse:
    urllib_request = Request(
        request.url,
        data=request.body,
        headers=request.headers,
        method=request.method,
    )
    try:
        with urlopen(urllib_request, timeout=request.timeout_seconds) as response:
            return OpenRouterTransportResponse(
                status_code=response.status,
                body=response.read(),
                content_type=response.headers.get("Content-Type"),
            )
    except HTTPError as exc:
        return OpenRouterTransportResponse(
            status_code=exc.code,
            body=exc.read(),
            content_type=exc.headers.get("Content-Type") if exc.headers else None,
        )
    except URLError as exc:
        raise LLMClientError(f"LLM request failed: {exc.reason}") from exc


def _openrouter_chat_completions_url(base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    if not normalized:
        raise LLMClientConfigurationError("LLM base_url must be configured for OpenRouter.")
    path = urlsplit(normalized).path.rstrip("/")
    if path.endswith("/chat/completions"):
        return normalized
    return f"{normalized}/chat/completions"


def _extract_message_text(message: Mapping[str, Any]) -> str | None:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts: list[str] = []
        for item in content:
            if not isinstance(item, Mapping):
                continue
            candidate = item.get("text")
            if isinstance(candidate, str) and candidate.strip():
                text_parts.append(candidate)
        if text_parts:
            return "".join(text_parts)
    refusal = message.get("refusal")
    if isinstance(refusal, str) and refusal.strip():
        return refusal
    return None


def _normalize_semantic_ui_payload(payload: Any, *, queue_item_json: str | None = None) -> Any:
    if not isinstance(payload, Mapping):
        return payload

    normalized_payload = dict(payload)
    explicit_null_semantic_ui = (
        "semantic_ui" in normalized_payload and normalized_payload.get("semantic_ui") is None
    )
    existing_semantic_ui = normalized_payload.get("semantic_ui")
    semantic_ui: dict[str, Any] | None
    if isinstance(existing_semantic_ui, Mapping):
        semantic_ui = dict(existing_semantic_ui)
    elif explicit_null_semantic_ui:
        semantic_ui = None
    else:
        semantic_ui = {}

    if semantic_ui is None:
        return normalized_payload

    for key in ("decision_card", "evidence_table", "risk_charts", "safety_profile", "queue_block", "notes"):
        if key in normalized_payload and key not in semantic_ui:
            semantic_ui[key] = normalized_payload.pop(key)

    evidence_table = semantic_ui.get("evidence_table")
    if isinstance(evidence_table, Mapping):
        normalized_table = dict(evidence_table)
        rows = normalized_table.get("rows")
        if isinstance(rows, list):
            normalized_rows: list[Any] = []
            for row in rows:
                if not isinstance(row, Mapping):
                    normalized_rows.append(row)
                    continue
                normalized_row = dict(row)
                if "cells" not in normalized_row:
                    cells = {
                        key: value
                        for key, value in normalized_row.items()
                        if key not in {"row_id", "label", "evidence_id"}
                    }
                    if cells:
                        normalized_row["cells"] = cells
                        for key in cells:
                            normalized_row.pop(key, None)
                normalized_rows.append(normalized_row)
            normalized_table["rows"] = normalized_rows
        semantic_ui["evidence_table"] = normalized_table

    risk_charts = semantic_ui.get("risk_charts")
    if isinstance(risk_charts, Mapping):
        risk_chart_items: list[Any] | None = [risk_charts]
    elif isinstance(risk_charts, list):
        risk_chart_items = risk_charts
    else:
        risk_chart_items = None
    if risk_chart_items is not None:
        normalized_charts: list[Any] = []
        for chart in risk_chart_items:
            if not isinstance(chart, Mapping):
                continue
            normalized_chart = dict(chart)
            chart_type = normalized_chart.get("chart_type")
            if chart_type not in {"bar", "line", "area", "radial"}:
                continue
            points = normalized_chart.get("points")
            if isinstance(points, list):
                normalized_points: list[Any] = []
                for point in points:
                    if not isinstance(point, Mapping):
                        continue
                    normalized_point = dict(point)
                    if "value" not in normalized_point:
                        if "y" in normalized_point:
                            if "label" not in normalized_point and "x" in normalized_point:
                                normalized_point["label"] = str(normalized_point["x"])
                            normalized_point["value"] = normalized_point["y"]
                        else:
                            continue
                    normalized_point.pop("x", None)
                    normalized_point.pop("y", None)
                    normalized_points.append(normalized_point)
                normalized_chart["points"] = normalized_points
            normalized_charts.append(normalized_chart)
        semantic_ui["risk_charts"] = normalized_charts

    if semantic_ui.get("queue_block") is None and queue_item_json:
        try:
            queue_item_payload = json.loads(queue_item_json)
        except json.JSONDecodeError:
            queue_item_payload = None
        if isinstance(queue_item_payload, Mapping):
            semantic_ui["queue_block"] = {
                "title": "Analyst Queue",
                "items": [dict(queue_item_payload)],
            }

    if semantic_ui or "semantic_ui" in normalized_payload:
        normalized_payload["semantic_ui"] = semantic_ui
    return normalized_payload


def _normalize_citation_arrays_payload(payload: Any) -> Any:
    if not isinstance(payload, Mapping):
        return payload
    normalized_payload = dict(payload)
    if normalized_payload.get("cited_evidence_ids") is None:
        normalized_payload["cited_evidence_ids"] = []
    answer_blocks = normalized_payload.get("answer_blocks")
    if isinstance(answer_blocks, list):
        normalized_blocks: list[Any] = []
        for block in answer_blocks:
            if not isinstance(block, Mapping):
                normalized_blocks.append(block)
                continue
            normalized_block = dict(block)
            if normalized_block.get("cited_evidence_ids") is None:
                normalized_block["cited_evidence_ids"] = []
            normalized_blocks.append(normalized_block)
        normalized_payload["answer_blocks"] = normalized_blocks
    return normalized_payload


def _normalize_payload_for_validation(request: LLMRequest, payload: Any) -> Any:
    if request.operation == "semantic_ui_payload":
        payload = _normalize_semantic_ui_payload(
            payload,
            queue_item_json=request.metadata.get("queue_item_json"),
        )
    payload = _normalize_citation_arrays_payload(payload)
    return _normalize_citation_aliases_payload(request, payload)


def _load_json_metadata(request: LLMRequest, key: str) -> Any | None:
    raw_value = request.metadata.get(key)
    if raw_value is None or not raw_value.strip():
        return None
    try:
        return json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise LLMResponseValidationError(f"{key} metadata must be valid JSON.") from exc


def _allowed_evidence_ids(request: LLMRequest) -> tuple[str, ...] | None:
    payload = _load_json_metadata(request, "allowed_evidence_ids_json")
    if payload is None:
        return None
    if not isinstance(payload, list) or any(not isinstance(item, str) for item in payload):
        raise LLMResponseValidationError("allowed_evidence_ids_json metadata must be a JSON array of strings.")
    deduped_ids: list[str] = []
    for item in payload:
        normalized = item.strip()
        if normalized and normalized not in deduped_ids:
            deduped_ids.append(normalized)
    return tuple(deduped_ids)


def _citation_aliases(request: LLMRequest) -> dict[str, str]:
    payload = _load_json_metadata(request, "citation_aliases_json")
    if payload is None:
        return {}
    if not isinstance(payload, Mapping):
        raise LLMResponseValidationError("citation_aliases_json metadata must be a JSON object.")
    allowed_ids = _allowed_evidence_ids(request)
    allowed_id_set = set(allowed_ids or ())
    aliases: dict[str, str] = {}
    for alias, target in payload.items():
        if not isinstance(alias, str) or not isinstance(target, str):
            raise LLMResponseValidationError(
                "citation_aliases_json metadata must map strings to strings."
            )
        normalized_alias = _normalize_numeric_field_token(alias)
        normalized_target = target.strip()
        if normalized_alias and normalized_target in allowed_id_set:
            aliases[normalized_alias] = normalized_target
    return aliases


def _replace_citation_alias(value: Any, aliases: Mapping[str, str]) -> Any:
    if not isinstance(value, str):
        return value
    return aliases.get(_normalize_numeric_field_token(value), value)


def _replace_citation_alias_list(value: Any, aliases: Mapping[str, str]) -> Any:
    if not isinstance(value, list):
        return value
    return [_replace_citation_alias(item, aliases) for item in value]


def _normalize_citation_aliases_payload(request: LLMRequest, payload: Any) -> Any:
    aliases = _citation_aliases(request)
    if not aliases or not isinstance(payload, Mapping):
        return payload

    normalized_payload = dict(payload)
    normalized_payload["cited_evidence_ids"] = _replace_citation_alias_list(
        normalized_payload.get("cited_evidence_ids"),
        aliases,
    )

    answer_blocks = normalized_payload.get("answer_blocks")
    if isinstance(answer_blocks, list):
        normalized_blocks: list[Any] = []
        for block in answer_blocks:
            if not isinstance(block, Mapping):
                normalized_blocks.append(block)
                continue
            normalized_block = dict(block)
            normalized_block["cited_evidence_ids"] = _replace_citation_alias_list(
                normalized_block.get("cited_evidence_ids"),
                aliases,
            )
            normalized_blocks.append(normalized_block)
        normalized_payload["answer_blocks"] = normalized_blocks

    semantic_ui = normalized_payload.get("semantic_ui")
    if isinstance(semantic_ui, Mapping):
        normalized_semantic_ui = dict(semantic_ui)
        evidence_table = normalized_semantic_ui.get("evidence_table")
        if isinstance(evidence_table, Mapping):
            normalized_table = dict(evidence_table)
            rows = normalized_table.get("rows")
            if isinstance(rows, list):
                normalized_rows: list[Any] = []
                for row in rows:
                    if not isinstance(row, Mapping):
                        normalized_rows.append(row)
                        continue
                    normalized_row = dict(row)
                    normalized_row["evidence_id"] = _replace_citation_alias(
                        normalized_row.get("evidence_id"),
                        aliases,
                    )
                    normalized_rows.append(normalized_row)
                normalized_table["rows"] = normalized_rows
            normalized_semantic_ui["evidence_table"] = normalized_table
        risk_charts = normalized_semantic_ui.get("risk_charts")
        if isinstance(risk_charts, list):
            normalized_charts: list[Any] = []
            for chart in risk_charts:
                if not isinstance(chart, Mapping):
                    normalized_charts.append(chart)
                    continue
                normalized_chart = dict(chart)
                points = normalized_chart.get("points")
                if isinstance(points, list):
                    normalized_points: list[Any] = []
                    for point in points:
                        if not isinstance(point, Mapping):
                            normalized_points.append(point)
                            continue
                        normalized_point = dict(point)
                        normalized_point["evidence_id"] = _replace_citation_alias(
                            normalized_point.get("evidence_id"),
                            aliases,
                        )
                        normalized_points.append(normalized_point)
                    normalized_chart["points"] = normalized_points
                normalized_charts.append(normalized_chart)
            normalized_semantic_ui["risk_charts"] = normalized_charts
        normalized_payload["semantic_ui"] = normalized_semantic_ui
    return normalized_payload


def _queue_item_expected_snapshot(request: LLMRequest) -> dict[str, Any] | None:
    raw_payload = _load_json_metadata(request, "queue_item_json")
    if raw_payload is None:
        return None
    if not isinstance(raw_payload, Mapping):
        raise LLMResponseValidationError("queue_item_json metadata must be a JSON object.")
    try:
        queue_item = QueueItem.model_validate(raw_payload)
    except ValidationError as exc:
        raise LLMResponseValidationError("queue_item_json metadata must match QueueItem.") from exc

    snapshot = queue_item.model_dump(mode="json")
    present_keys = {
        key
        for key in raw_payload
        if key in snapshot and key in _QUEUE_ITEM_MODEL_VISIBLE_FIELDS
    }
    return {
        key: value
        for key, value in snapshot.items()
        if key in present_keys
    }


def _collect_cited_evidence_ids(response: CopilotResponse) -> list[tuple[str, str]]:
    cited_ids: list[tuple[str, str]] = []
    for index, evidence_id in enumerate(response.cited_evidence_ids):
        cited_ids.append((f"cited_evidence_ids[{index}]", evidence_id))
    for block_index, block in enumerate(response.answer_blocks):
        for evidence_index, evidence_id in enumerate(block.cited_evidence_ids):
            cited_ids.append(
                (
                    f"answer_blocks[{block_index}].cited_evidence_ids[{evidence_index}]",
                    evidence_id,
                )
            )
    semantic_ui = response.semantic_ui
    if semantic_ui is not None:
        if semantic_ui.evidence_table is not None:
            for row_index, row in enumerate(semantic_ui.evidence_table.rows):
                if row.evidence_id:
                    cited_ids.append(
                        (
                            f"semantic_ui.evidence_table.rows[{row_index}].evidence_id",
                            row.evidence_id,
                        )
                    )
        for chart_index, chart in enumerate(semantic_ui.risk_charts):
            for point_index, point in enumerate(chart.points):
                if point.evidence_id:
                    cited_ids.append(
                        (
                            f"semantic_ui.risk_charts[{chart_index}].points[{point_index}].evidence_id",
                            point.evidence_id,
                        )
                    )
    return cited_ids


def _validate_grounded_evidence_ids(request: LLMRequest, response: CopilotResponse) -> None:
    allowed_ids = _allowed_evidence_ids(request)
    if allowed_ids is None:
        return
    allowed_id_set = set(allowed_ids)
    invalid_ids = [
        f"{location}={evidence_id}"
        for location, evidence_id in _collect_cited_evidence_ids(response)
        if evidence_id not in allowed_id_set
    ]
    if invalid_ids:
        joined = ", ".join(invalid_ids[:5])
        raise LLMResponseValidationError(
            f"{request.operation} cited evidence IDs outside the grounded context: {joined}."
        )


def _normalize_numeric_field_token(value: str) -> str:
    normalized = _NUMERIC_FIELD_TOKEN_PATTERN.sub("_", value.strip().lower()).strip("_")
    return normalized


def _normalize_signal_text(value: str) -> str:
    return _SIGNAL_TEXT_TOKEN_PATTERN.sub("", value.strip().lower())


def _grounded_mechanistic_evidence(
    request: LLMRequest,
) -> dict[str, tuple[str, ...]] | None:
    payload = _load_json_metadata(request, "grounded_mechanistic_evidence_json")
    if payload is None:
        return None
    if not isinstance(payload, Mapping):
        raise LLMResponseValidationError(
            "grounded_mechanistic_evidence_json metadata must be a JSON object."
        )
    grounded_evidence: dict[str, tuple[str, ...]] = {}
    for evidence_id, signals in payload.items():
        if not isinstance(evidence_id, str):
            raise LLMResponseValidationError(
                "grounded_mechanistic_evidence_json metadata keys must be strings."
            )
        if not isinstance(signals, Sequence) or isinstance(signals, str | bytes):
            raise LLMResponseValidationError(
                "grounded_mechanistic_evidence_json metadata values must be arrays of strings."
            )
        normalized_signals = tuple(
            normalized
            for signal in signals
            if isinstance(signal, str)
            and (normalized := _normalize_signal_text(signal))
        )
        if normalized_signals:
            grounded_evidence[evidence_id] = normalized_signals
    return grounded_evidence


def _grounded_numeric_values(request: LLMRequest) -> dict[str, float] | None:
    payload = _load_json_metadata(request, "grounded_numeric_values_json")
    if payload is None:
        return None
    if not isinstance(payload, Mapping):
        raise LLMResponseValidationError(
            "grounded_numeric_values_json metadata must be a JSON object."
        )
    numeric_values: dict[str, float] = {}
    for key, value in payload.items():
        if not isinstance(key, str):
            raise LLMResponseValidationError(
                "grounded_numeric_values_json metadata keys must be strings."
            )
        if isinstance(value, bool) or not isinstance(value, Real):
            continue
        normalized_key = _normalize_numeric_field_token(key)
        if normalized_key:
            numeric_values[normalized_key] = float(value)
    return numeric_values


def _resolve_grounded_numeric_key(
    grounded_values: Mapping[str, float],
    *candidates: str,
) -> str | None:
    for candidate in candidates:
        normalized_candidate = _normalize_numeric_field_token(candidate)
        if not normalized_candidate:
            continue
        if normalized_candidate in grounded_values:
            return normalized_candidate
        alias_target = _SEMANTIC_UI_NUMERIC_FIELD_ALIASES.get(normalized_candidate)
        if alias_target and alias_target in grounded_values:
            return alias_target
    return None


def _semantic_ui_numeric_values(
    semantic_ui: SemanticUIObject,
    grounded_values: Mapping[str, float],
) -> list[tuple[str, float, str | None, bool]]:
    numeric_values: list[tuple[str, float, str | None, bool]] = []
    if semantic_ui.decision_card is not None:
        for metric_index, metric in enumerate(semantic_ui.decision_card.metrics):
            if isinstance(metric.value, bool) or not isinstance(metric.value, Real):
                continue
            numeric_values.append(
                (
                    f"semantic_ui.decision_card.metrics[{metric_index}].value",
                    float(metric.value),
                    _resolve_grounded_numeric_key(grounded_values, metric.key, metric.label),
                    True,
                )
            )
    if semantic_ui.evidence_table is not None:
        for row_index, row in enumerate(semantic_ui.evidence_table.rows):
            for cell_key, cell_value in row.cells.items():
                if isinstance(cell_value, bool) or not isinstance(cell_value, Real):
                    continue
                numeric_values.append(
                    (
                        f"semantic_ui.evidence_table.rows[{row_index}].cells.{cell_key}",
                        float(cell_value),
                        _resolve_grounded_numeric_key(grounded_values, cell_key, row.label),
                        False,
                    )
                )
    for chart_index, chart in enumerate(semantic_ui.risk_charts):
        for point_index, point in enumerate(chart.points):
            numeric_values.append(
                (
                    f"semantic_ui.risk_charts[{chart_index}].points[{point_index}].value",
                    float(point.value),
                    _resolve_grounded_numeric_key(grounded_values, point.label),
                    True,
                )
            )
    if semantic_ui.safety_profile is not None:
        for axis_index, axis in enumerate(semantic_ui.safety_profile.axes):
            numeric_values.append(
                (
                    f"semantic_ui.safety_profile.axes[{axis_index}].value",
                    float(axis.value),
                    _resolve_grounded_numeric_key(grounded_values, axis.label),
                    True,
                )
            )
    return numeric_values


def _validate_grounded_semantic_ui_numbers(
    request: LLMRequest,
    semantic_ui: SemanticUIObject | None,
) -> None:
    if semantic_ui is None:
        return
    grounded_values = _grounded_numeric_values(request)
    if grounded_values is None:
        return

    invalid_values: list[str] = []
    grounded_value_set = tuple(grounded_values.values())
    for location, value, expected_key, binding_required in _semantic_ui_numeric_values(
        semantic_ui,
        grounded_values,
    ):
        if expected_key is not None:
            expected_value = grounded_values[expected_key]
            if not math.isclose(value, expected_value, rel_tol=1e-9, abs_tol=1e-9):
                invalid_values.append(
                    f"{location}={value} expected {expected_key}={expected_value}"
                )
            continue
        if binding_required:
            invalid_values.append(f"{location}={value} did not match a grounded numeric field")
            continue
        if not any(
            math.isclose(value, grounded_value, rel_tol=1e-9, abs_tol=1e-9)
            for grounded_value in grounded_value_set
        ):
            invalid_values.append(f"{location}={value}")
    if invalid_values:
        joined = ", ".join(invalid_values[:5])
        raise LLMResponseValidationError(
            f"{request.operation} included numeric values outside the grounded context: {joined}."
        )


def _validate_grounded_semantic_ui_evidence_rows(
    request: LLMRequest,
    semantic_ui: SemanticUIObject | None,
) -> None:
    if semantic_ui is None or semantic_ui.evidence_table is None:
        return
    grounded_evidence = _grounded_mechanistic_evidence(request)
    if not grounded_evidence:
        return

    invalid_rows: list[str] = []
    for row_index, row in enumerate(semantic_ui.evidence_table.rows):
        if row.evidence_id not in grounded_evidence:
            continue
        rendered_text = " ".join(
            str(value)
            for value in (row.label, *row.cells.values())
            if value is not None
        )
        normalized_rendered_text = _normalize_signal_text(rendered_text)
        expected_signals = grounded_evidence[row.evidence_id]
        if not any(signal in normalized_rendered_text for signal in expected_signals):
            invalid_rows.append(
                f"semantic_ui.evidence_table.rows[{row_index}]={row.evidence_id} "
                f"expected one of {', '.join(expected_signals)}"
            )
    if invalid_rows:
        joined = ", ".join(invalid_rows[:5])
        raise LLMResponseValidationError(
            f"{request.operation} rendered mechanistic evidence rows that do not match "
            f"their cited evidence IDs: {joined}."
        )


def _queue_item_grounded_snapshot(queue_item: QueueItem) -> dict[str, Any]:
    return queue_item.model_dump(mode="json")


def _enum_token(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _validate_response_identity_matches_request(
    request: LLMRequest,
    response: CopilotResponse,
) -> None:
    expected = {
        key: value
        for key in ("job_id", "sample_id", "target_drug")
        if (value := request.metadata.get(key)) is not None
    }
    drifted_fields = [
        key
        for key, expected_value in expected.items()
        if getattr(response, key) != expected_value
    ]
    if drifted_fields:
        joined = ", ".join(drifted_fields)
        raise LLMResponseValidationError(
            f"{request.operation} identity drifted from the grounded request: {joined}."
        )


def _validate_semantic_decision_card_matches_request(
    request: LLMRequest,
    semantic_ui: SemanticUIObject | None,
) -> None:
    if semantic_ui is None or semantic_ui.decision_card is None:
        return
    queue_snapshot = _queue_item_expected_snapshot(request) or {}
    expected = {
        key: value
        for key, value in {
            "triage_decision": request.metadata.get("triage") or queue_snapshot.get("triage"),
            "severity": request.metadata.get("severity") or queue_snapshot.get("severity"),
        }.items()
        if value is not None
    }
    actual = {
        "triage_decision": _enum_token(semantic_ui.decision_card.triage_decision),
        "severity": _enum_token(semantic_ui.decision_card.severity),
    }
    drifted_fields = [
        key
        for key, expected_value in expected.items()
        if actual.get(key) != expected_value
    ]
    if drifted_fields:
        joined = ", ".join(drifted_fields)
        raise LLMResponseValidationError(
            f"{request.operation} decision_card drifted from the grounded request: {joined}."
        )


def _validate_queue_block_matches_request(
    request: LLMRequest,
    semantic_ui: SemanticUIObject | None,
) -> None:
    if semantic_ui is None or semantic_ui.queue_block is None:
        return
    expected_snapshot = _queue_item_expected_snapshot(request)
    if expected_snapshot is None:
        return
    queue_items = semantic_ui.queue_block.items
    if len(queue_items) != 1:
        raise LLMResponseValidationError(
            f"{request.operation} queue_block must preserve exactly one grounded queue item."
        )
    actual_snapshot = _queue_item_grounded_snapshot(queue_items[0])
    actual_snapshot = {
        key: actual_snapshot[key]
        for key in expected_snapshot
        if key in actual_snapshot
    }
    if actual_snapshot != expected_snapshot:
        drifted_fields = [
            field_name
            for field_name in expected_snapshot
            if actual_snapshot.get(field_name) != expected_snapshot[field_name]
        ]
        joined = ", ".join(drifted_fields)
        raise LLMResponseValidationError(
            f"{request.operation} queue_block drifted from the grounded queue item: {joined}."
        )


def _validate_grounded_output(request: LLMRequest, parsed: object) -> None:
    if not isinstance(parsed, CopilotResponse):
        return
    _validate_response_identity_matches_request(request, parsed)
    _validate_grounded_evidence_ids(request, parsed)
    _validate_grounded_semantic_ui_evidence_rows(request, parsed.semantic_ui)
    _validate_grounded_semantic_ui_numbers(request, parsed.semantic_ui)
    _validate_semantic_decision_card_matches_request(request, parsed.semantic_ui)
    _validate_queue_block_matches_request(request, parsed.semantic_ui)


class LLMClient(ABC):
    def __init__(self, settings: LLMSettings) -> None:
        self.settings = settings

    @abstractmethod
    def generate(self, request: LLMRequest) -> LLMClientResponse:
        raise NotImplementedError

    def generate_validated(
        self,
        request: LLMRequest,
        response_model: type[ModelT],
    ) -> ValidatedLLMResponse[ModelT]:
        attempts = request.retry_count + 1
        last_error: LLMResponseValidationError | None = None
        for attempt_index in range(attempts):
            response = self.generate(request)
            try:
                return self._validate_response(request, response_model, response)
            except LLMResponseValidationError as exc:
                last_error = exc
                if (
                    self._is_retryable_validation_error(
                        exc, request=request, response_model=response_model
                    )
                    and attempt_index + 1 < attempts
                ):
                    continue
                raise
        if last_error is not None:
            raise last_error
        raise LLMResponseValidationError(f"{request.operation} did not return valid output.")

    def _is_retryable_validation_error(
        self,
        error: LLMResponseValidationError,
        *,
        request: LLMRequest,
        response_model: type[ModelT],
    ) -> bool:
        message = str(error)
        return message in {
            f"{request.operation} did not return valid JSON output.",
            f"{request.operation} did not match {response_model.__name__}.",
        }

    def _validate_response(
        self,
        request: LLMRequest,
        response_model: type[ModelT],
        response: LLMClientResponse,
    ) -> ValidatedLLMResponse[ModelT]:
        payload = response.output_json
        if payload is None:
            try:
                payload = json.loads(response.output_text or "")
            except json.JSONDecodeError as exc:
                raise LLMResponseValidationError(
                    f"{request.operation} did not return valid JSON output."
                ) from exc
        payload = _normalize_payload_for_validation(request, payload)
        try:
            parsed = response_model.model_validate(payload)
        except ValidationError as exc:
            raise LLMResponseValidationError(
                f"{request.operation} did not match {response_model.__name__}."
            ) from exc
        _validate_grounded_output(request, parsed)
        return ValidatedLLMResponse(response=response, parsed=parsed)


class OpenRouterLLMClient(LLMClient):
    def __init__(
        self,
        settings: LLMSettings,
        *,
        transport: OpenRouterTransport | None = None,
    ) -> None:
        super().__init__(settings)
        if not settings.api_key:
            raise LLMClientConfigurationError("LLM_API_KEY must be configured for OpenRouter.")
        self._endpoint = _openrouter_chat_completions_url(settings.base_url or "")
        self._transport = transport or _default_transport

    def generate(self, request: LLMRequest) -> LLMClientResponse:
        candidate_models = self._candidate_models(request)
        last_error: LLMClientError | None = None
        for model_index, candidate_model in enumerate(candidate_models):
            payload = self._build_payload(request, model=candidate_model)
            transport_request = OpenRouterTransportRequest(
                method="POST",
                url=self._endpoint,
                headers={
                    "Authorization": f"Bearer {self.settings.api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": "SafeSurveil-AIxBio/0.1",
                },
                body=json.dumps(payload).encode("utf-8"),
                timeout_seconds=request.timeout_seconds,
            )
            attempts = request.retry_count + 1
            for attempt_index in range(attempts):
                try:
                    response = self._transport(transport_request)
                except LLMClientError as exc:
                    last_error = exc
                    if attempt_index + 1 < attempts:
                        continue
                    break
                except _RETRYABLE_TRANSPORT_EXCEPTIONS as exc:
                    last_error = LLMClientError(f"LLM request failed: {exc}")
                    if attempt_index + 1 < attempts:
                        continue
                    break
                if 200 <= response.status_code < 300:
                    return self._parse_response(
                        response,
                        request=request,
                        attempt_count=attempt_index + 1,
                    )
                if response.status_code == 429 and model_index + 1 < len(candidate_models):
                    last_error = LLMClientError(
                        f"OpenRouter chat completions failed with HTTP {response.status_code}"
                    )
                    break
                if (
                    response.status_code in _RETRYABLE_HTTP_STATUS_CODES
                    and attempt_index + 1 < attempts
                ):
                    continue
                if response.status_code in {401, 403}:
                    raise LLMClientError(
                        f"OpenRouter chat completions failed with HTTP {response.status_code}"
                    )
                last_error = LLMClientError(
                    f"OpenRouter chat completions failed with HTTP {response.status_code}"
                )
                break
            if model_index + 1 < len(candidate_models):
                continue
        if last_error is not None:
            raise last_error
        raise LLMClientError("OpenRouter chat completions request failed.")

    def _candidate_models(self, request: LLMRequest) -> tuple[str, ...]:
        candidates: list[str] = []
        for candidate in (request.model, self.settings.model, self.settings.fallback_model):
            normalized = candidate.strip() if candidate is not None else ""
            if normalized and normalized not in candidates:
                candidates.append(normalized)
        if not candidates:
            raise LLMClientConfigurationError("No OpenRouter model is configured.")
        return tuple(candidates)

    def _build_payload(self, request: LLMRequest, *, model: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "messages": [message.to_payload() for message in request.messages],
            "stream": False,
        }
        payload["model"] = model
        if request.output_format is LLMOutputFormat.JSON:
            payload["response_format"] = {"type": "json_object"}
        if request.max_output_tokens is not None:
            payload["max_tokens"] = request.max_output_tokens
        reasoning_enabled = (
            request.reasoning_enabled
            if request.reasoning_enabled is not None
            else self.settings.reasoning_enabled
        )
        if reasoning_enabled:
            payload["reasoning"] = {"enabled": True}
        return payload

    def _parse_response(
        self,
        response: OpenRouterTransportResponse,
        *,
        request: LLMRequest,
        attempt_count: int,
    ) -> LLMClientResponse:
        try:
            payload = json.loads(response.body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise LLMClientError("OpenRouter returned invalid JSON.") from exc

        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LLMClientError("OpenRouter response did not include any choices.")
        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise LLMClientError("OpenRouter response included an invalid choice payload.")
        message = first_choice.get("message")
        if not isinstance(message, dict):
            raise LLMClientError("OpenRouter response did not include a message payload.")
        content = _extract_message_text(message)

        usage_payload = payload.get("usage")
        usage: LLMUsage | None = None
        if isinstance(usage_payload, dict):
            usage = LLMUsage(
                input_tokens=usage_payload.get("prompt_tokens"),
                output_tokens=usage_payload.get("completion_tokens"),
                total_tokens=usage_payload.get("total_tokens"),
            )

        output_json: Mapping[str, Any] | None = None
        if request.output_format is LLMOutputFormat.JSON and content:
            try:
                parsed_content = json.loads(content)
            except json.JSONDecodeError:
                parsed_content = None
            if isinstance(parsed_content, Mapping):
                output_json = parsed_content

        return LLMClientResponse(
            provider="openrouter",
            model=payload.get("model"),
            output_text=content,
            output_json=output_json,
            raw_payload=payload,
            response_id=payload.get("id"),
            finish_reason=first_choice.get("finish_reason"),
            usage=usage,
            metadata=request.metadata,
            attempt_count=attempt_count,
        )


def build_llm_client(
    settings: AppSettings | LLMSettings,
    *,
    transport: OpenRouterTransport | None = None,
) -> LLMClient:
    llm_settings = settings.llm if isinstance(settings, AppSettings) else settings
    provider = (llm_settings.provider or "").strip().lower()
    if provider == "openrouter":
        return OpenRouterLLMClient(llm_settings, transport=transport)
    if not provider:
        raise LLMClientConfigurationError("LLM_PROVIDER is not configured.")
    raise LLMClientConfigurationError(f"Unsupported LLM provider: {provider}")
