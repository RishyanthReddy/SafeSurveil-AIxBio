from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import math
import re
from typing import Any

from pydantic import BaseModel

from app.contracts import (
    ArtifactManifest,
    CitationValiditySummary,
    CopilotResponse,
    DecisionObject,
    EvidenceCoverageSummary,
    ExecutionGateCheck,
    ExecutionGateCheckCategory,
    ExecutionGateCheckStatus,
    ExecutionGateDecision,
    ExecutionGateIssue,
    ExecutionGateIssueSeverity,
    ExecutionGateReport,
    NumericConsistencySummary,
    PolicyAlignmentSummary,
    ReasoningTrace,
    REASONING_TRACE_REQUIRED_STEP_TYPES,
    SemanticUIObject,
)

_NUMERIC_FIELD_TOKEN_PATTERN = re.compile(r"[^a-z0-9]+")
_SIGNAL_TOKEN_PATTERN = re.compile(r"[^a-z0-9]+")
_POLICY_TEXT_TOKEN_PATTERN = re.compile(r"\s+")
_FREE_TEXT_TERMINAL_PUNCTUATION_PATTERN = re.compile(r"[.!?;:]+$")
_DISPLAY_ABSOLUTE_TOLERANCE = 0.005001
_DISPLAY_RELATIVE_TOLERANCE = 0.005
_NUMERIC_FIELD_ALIASES = {
    "probability": "probability",
    "prediction_probability": "probability",
    "resistance_probability": "probability",
    "phenotype_probability": "probability",
    "actionability": "actionability_score",
    "actionability_score": "actionability_score",
    "novelty": "novelty_score",
    "novelty_score": "novelty_score",
    "lineage_novelty": "novelty_score",
    "qc": "qc_risk",
    "qc_risk": "qc_risk",
    "quality_risk": "qc_risk",
    "metadata": "metadata_completeness",
    "metadata_completeness": "metadata_completeness",
}
_ACTION_OVERRIDE_PATTERNS = (
    re.compile(r"\bact now\b"),
    re.compile(r"\bsafe to act\b"),
    re.compile(r"\btake operational action\b"),
    re.compile(r"\bproceed with operational action\b"),
    re.compile(r"\bproceed to treatment\b"),
    re.compile(r"\bstart treatment\b"),
    re.compile(r"\btreat the patient\b"),
    re.compile(r"\badminister\b"),
    re.compile(r"\bskip analyst review\b"),
    re.compile(r"\bno analyst review needed\b"),
)
_LAB_CONFIRMATION_BYPASS_PATTERNS = (
    re.compile(r"\bno lab confirmation (?:is )?(?:required|needed)\b"),
    re.compile(r"\blab confirmation (?:is )?not (?:required|needed)\b"),
    re.compile(r"\bskip lab confirmation\b"),
    re.compile(r"\bbypass lab\b"),
    re.compile(r"\bwithout lab confirmation\b"),
    re.compile(r"\bdo not wait for confirmation\b"),
)
_CERTAINTY_OVERCLAIM_PATTERNS = (
    re.compile(r"\bconfirmed resistant\b"),
    re.compile(r"\bconfirmed susceptible\b"),
    re.compile(r"\bdefinitive\b"),
    re.compile(r"\bguaranteed\b"),
    re.compile(r"\bclinically validated\b"),
    re.compile(r"\bconclusive\b"),
    re.compile(r"\bno uncertainty\b"),
    re.compile(r"\b100% certain\b"),
)
_VOLATILE_AUDIT_KEYS = {
    "completed_at",
    "created_at",
    "generated_at",
    "submitted_at",
    "timestamp",
    "updated_at",
}
_SECRET_KEY_FRAGMENTS = (
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "credential",
    "password",
    "private_key",
    "secret",
    "token",
)


@dataclass(frozen=True)
class NumericObservation:
    field_key: str | None
    location: str
    value: float


@dataclass(frozen=True)
class IdentityNumericVerificationResult:
    checks: tuple[ExecutionGateCheck, ...]
    numeric_consistency: NumericConsistencySummary
    gate_decision: ExecutionGateDecision


@dataclass(frozen=True)
class EvidenceCitationVerificationResult:
    checks: tuple[ExecutionGateCheck, ...]
    evidence_coverage: EvidenceCoverageSummary
    citation_validity: CitationValiditySummary
    gate_decision: ExecutionGateDecision


@dataclass(frozen=True)
class PolicyAlignmentVerificationResult:
    checks: tuple[ExecutionGateCheck, ...]
    policy_alignment: PolicyAlignmentSummary
    gate_decision: ExecutionGateDecision


@dataclass(frozen=True)
class ReasoningTraceVerificationResult:
    checks: tuple[ExecutionGateCheck, ...]
    gate_decision: ExecutionGateDecision


@dataclass(frozen=True)
class AuditDigestBundle:
    policy_hash: str
    audit_fingerprint: str


def _enum_value(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _normalize_numeric_token(value: str) -> str:
    return _NUMERIC_FIELD_TOKEN_PATTERN.sub("_", value.strip().lower()).strip("_")


def _normalize_free_text(value: str) -> str:
    normalized = " ".join(value.strip().lower().split())
    return _FREE_TEXT_TERMINAL_PUNCTUATION_PATTERN.sub("", normalized)


def _normalize_policy_text(value: str) -> str:
    return _POLICY_TEXT_TOKEN_PATTERN.sub(" ", value.strip().lower())


def _normalize_signal_text(value: str) -> str:
    return _SIGNAL_TOKEN_PATTERN.sub("", value.strip().lower())


def _canonical_key(value: object) -> str:
    return str(value)


def _is_secret_or_volatile_key(key: str) -> bool:
    normalized = key.strip().lower()
    if normalized in _VOLATILE_AUDIT_KEYS:
        return True
    return any(fragment in normalized for fragment in _SECRET_KEY_FRAGMENTS)


def _canonical_audit_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _canonical_audit_value(value.model_dump(mode="json"))
    if hasattr(value, "__dataclass_fields__"):
        return _canonical_audit_value(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {
            str(key): _canonical_audit_value(item)
            for key, item in sorted(value.items(), key=lambda entry: str(entry[0]))
            if not _is_secret_or_volatile_key(str(key))
        }
    if isinstance(value, list | tuple):
        return [_canonical_audit_value(item) for item in value]
    if isinstance(value, set | frozenset):
        canonical_items = [_canonical_audit_value(item) for item in value]
        return sorted(canonical_items, key=_canonical_key)
    return value


def _sha256_digest(payload: Any) -> str:
    canonical_payload = _canonical_audit_value(payload)
    encoded = json.dumps(
        canonical_payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _resolve_numeric_field(*candidates: str) -> str | None:
    for candidate in candidates:
        normalized = _normalize_numeric_token(candidate)
        if not normalized:
            continue
        return _NUMERIC_FIELD_ALIASES.get(normalized, normalized if normalized in _NUMERIC_FIELD_ALIASES else None)
    return None


def _expected_numeric_values(decision: DecisionObject) -> dict[str, float]:
    return {
        "probability": float(decision.phenotype_prediction.probability),
        "actionability_score": float(decision.actionability_features.actionability_score),
        "novelty_score": float(decision.novelty_assessment.novelty_score),
        "qc_risk": float(decision.actionability_features.qc_risk),
        "metadata_completeness": float(decision.actionability_features.metadata_completeness),
    }


def _numbers_match(observed: float, expected: float) -> bool:
    candidate_values = [observed]
    if expected <= 1.0 and observed > 1.0:
        candidate_values.append(observed / 100.0)
    if expected > 1.0 and observed <= 1.0:
        candidate_values.append(observed * 100.0)
    return any(
        math.isclose(
            candidate,
            expected,
            rel_tol=_DISPLAY_RELATIVE_TOLERANCE,
            abs_tol=_DISPLAY_ABSOLUTE_TOLERANCE,
        )
        for candidate in candidate_values
    )


def _semantic_ui_numeric_observations(semantic_ui: SemanticUIObject | None) -> list[NumericObservation]:
    if semantic_ui is None:
        return []
    observations: list[NumericObservation] = []
    if semantic_ui.decision_card is not None:
        for metric_index, metric in enumerate(semantic_ui.decision_card.metrics):
            if isinstance(metric.value, bool) or not isinstance(metric.value, int | float):
                continue
            observations.append(
                NumericObservation(
                    field_key=_resolve_numeric_field(metric.key, metric.label),
                    location=f"semantic_ui.decision_card.metrics[{metric_index}]",
                    value=float(metric.value),
                )
            )
    for chart_index, chart in enumerate(semantic_ui.risk_charts):
        for point_index, point in enumerate(chart.points):
            observations.append(
                NumericObservation(
                    field_key=_resolve_numeric_field(point.label),
                    location=f"semantic_ui.risk_charts[{chart_index}].points[{point_index}]",
                    value=float(point.value),
                )
            )
    if semantic_ui.safety_profile is not None:
        for axis_index, axis in enumerate(semantic_ui.safety_profile.axes):
            observations.append(
                NumericObservation(
                    field_key=_resolve_numeric_field(axis.label),
                    location=f"semantic_ui.safety_profile.axes[{axis_index}]",
                    value=float(axis.value),
                )
            )
    return observations


def _sorted_unique(values: list[str] | set[str]) -> list[str]:
    return sorted({value for value in values if value})


def _mechanistic_evidence_ids(decision: DecisionObject) -> dict[str, set[str]]:
    indexed_signals: dict[str, set[str]] = {}
    for index, evidence in enumerate(decision.mechanistic_evidence, start=1):
        signals = {
            _normalize_signal_text(value)
            for value in (evidence.gene_symbol, evidence.mutation)
            if value
        }
        indexed_signals[f"mechanistic_evidence__{index}"] = {signal for signal in signals if signal}
    return indexed_signals


def _allowed_evidence_ids(
    decision: DecisionObject,
    *,
    artifact_manifest: ArtifactManifest | None,
) -> list[str]:
    evidence_ids = {
        "decision_object__summary",
        "decision_object__triage",
        "decision_object__assembly_qc",
        "decision_object__warnings",
        "phenotype_prediction__summary",
        "actionability_features__summary",
        "novelty_assessment__summary",
    }
    mechanism_ids = _mechanistic_evidence_ids(decision)
    if mechanism_ids:
        evidence_ids.update(mechanism_ids)
    else:
        evidence_ids.add("mechanistic_evidence__none")

    for evidence in decision.mechanistic_evidence:
        if evidence.raw_artifact_id:
            evidence_ids.add(evidence.raw_artifact_id)
    if artifact_manifest is not None:
        evidence_ids.update(artifact.artifact_id for artifact in artifact_manifest.artifacts)
    return _sorted_unique(evidence_ids)


def _required_evidence_ids(decision: DecisionObject) -> list[str]:
    evidence_ids = {
        "decision_object__summary",
        "decision_object__triage",
        "decision_object__warnings",
        "phenotype_prediction__summary",
        "actionability_features__summary",
        "novelty_assessment__summary",
    }
    mechanism_ids = _mechanistic_evidence_ids(decision)
    if mechanism_ids:
        evidence_ids.update(mechanism_ids)
    else:
        evidence_ids.add("mechanistic_evidence__none")
    return _sorted_unique(evidence_ids)


def _semantic_ui_cited_evidence_ids(semantic_ui: SemanticUIObject | None) -> list[str]:
    if semantic_ui is None:
        return []
    evidence_ids: list[str] = []
    if semantic_ui.decision_card is not None:
        evidence_ids.append("decision_object__triage")
    if semantic_ui.evidence_table is not None:
        evidence_ids.extend(
            row.evidence_id
            for row in semantic_ui.evidence_table.rows
            if row.evidence_id is not None
        )
    for chart in semantic_ui.risk_charts:
        evidence_ids.extend(
            point.evidence_id
            for point in chart.points
            if point.evidence_id is not None
        )
    return evidence_ids


def _copilot_cited_evidence_ids(copilot: CopilotResponse | None) -> list[str]:
    if copilot is None:
        return []
    evidence_ids = list(copilot.cited_evidence_ids)
    for block in copilot.answer_blocks:
        evidence_ids.extend(block.cited_evidence_ids)
    evidence_ids.extend(_semantic_ui_cited_evidence_ids(copilot.semantic_ui))
    return evidence_ids


def _policy_text_fragments(
    *,
    semantic_ui: SemanticUIObject | None,
    copilot: CopilotResponse | None,
) -> list[str]:
    fragments: list[str] = []
    if copilot is not None:
        if copilot.summary:
            fragments.append(copilot.summary)
        fragments.extend(copilot.next_steps)
        for block in copilot.answer_blocks:
            if block.title:
                fragments.append(block.title)
            fragments.append(block.content)
        if copilot.semantic_ui is not None:
            fragments.extend(_policy_text_fragments(semantic_ui=copilot.semantic_ui, copilot=None))

    if semantic_ui is not None:
        if semantic_ui.decision_card is not None:
            fragments.extend([semantic_ui.decision_card.title, semantic_ui.decision_card.summary])
        fragments.extend(semantic_ui.notes)
        if semantic_ui.queue_block is not None:
            for item in semantic_ui.queue_block.items:
                fragments.append(item.headline)
    return fragments


def _contains_pattern(text: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    return any(pattern.search(text) for pattern in patterns)


def _rendered_semantic_evidence_row_checks(
    decision: DecisionObject,
    *,
    semantic_ui: SemanticUIObject | None,
) -> list[ExecutionGateCheck]:
    if semantic_ui is None or semantic_ui.evidence_table is None:
        return []

    mechanism_signals_by_id = _mechanistic_evidence_ids(decision)
    checks: list[ExecutionGateCheck] = []
    for row_index, row in enumerate(semantic_ui.evidence_table.rows):
        if row.evidence_id not in mechanism_signals_by_id:
            continue
        expected_signals = mechanism_signals_by_id[row.evidence_id]
        rendered_text = " ".join(
            str(value)
            for value in [row.label, *row.cells.values()]
            if value is not None
        )
        normalized_rendered_text = _normalize_signal_text(rendered_text)
        matched_signal = next(
            (signal for signal in expected_signals if signal and signal in normalized_rendered_text),
            None,
        )
        checks.append(
            _check(
                check_id=f"semantic_mechanism_row_{row_index}",
                category=ExecutionGateCheckCategory.EVIDENCE_COVERAGE,
                status=(
                    ExecutionGateCheckStatus.PASS
                    if matched_signal is not None
                    else ExecutionGateCheckStatus.FAIL
                ),
                title="Semantic mechanism row matches cited evidence",
                detail=(
                    f"Semantic UI row {row.row_id} names the mechanism tied to {row.evidence_id}."
                    if matched_signal is not None
                    else f"Semantic UI row {row.row_id} does not name the mechanism tied to {row.evidence_id}."
                ),
                evidence_refs=[row.evidence_id],
                observed_value=row.label,
                expected_value=", ".join(sorted(expected_signals)),
            )
        )
    return checks


def _check(
    *,
    check_id: str,
    category: ExecutionGateCheckCategory,
    status: ExecutionGateCheckStatus,
    title: str,
    detail: str,
    evidence_refs: list[str] | None = None,
    observed_value: str | float | int | bool | None = None,
    expected_value: str | float | int | bool | None = None,
) -> ExecutionGateCheck:
    return ExecutionGateCheck(
        check_id=check_id,
        category=category,
        status=status,
        title=title,
        detail=detail,
        evidence_refs=evidence_refs or [],
        observed_value=observed_value,
        expected_value=expected_value,
    )


def _identity_checks(
    decision: DecisionObject,
    *,
    semantic_ui: SemanticUIObject | None,
    copilot: CopilotResponse | None,
) -> list[ExecutionGateCheck]:
    expected_job_id = decision.job_id or decision.triage_decision.job_id
    expected_identity = {
        "job_id": expected_job_id,
        "sample_id": decision.sample.sample_id,
        "target_drug": decision.sample.target_drug,
    }
    checks: list[ExecutionGateCheck] = []

    if copilot is not None:
        for field_name, expected_value in expected_identity.items():
            observed_value = getattr(copilot, field_name)
            status = (
                ExecutionGateCheckStatus.PASS
                if observed_value == expected_value
                else ExecutionGateCheckStatus.FAIL
            )
            checks.append(
                _check(
                    check_id=f"copilot_{field_name}",
                    category=ExecutionGateCheckCategory.IDENTITY,
                    status=status,
                    title=f"Copilot {field_name} matches decision",
                    detail=(
                        f"Copilot {field_name} matches the persisted decision."
                        if status == ExecutionGateCheckStatus.PASS
                        else f"Copilot {field_name} drifted from the persisted decision."
                    ),
                    observed_value=observed_value,
                    expected_value=expected_value,
                )
            )
        expected_next_step = _normalize_free_text(decision.triage_decision.recommended_next_step)
        observed_next_steps = [_normalize_free_text(item) for item in copilot.next_steps]
        if not observed_next_steps:
            checks.append(
                _check(
                    check_id="copilot_next_step",
                    category=ExecutionGateCheckCategory.IDENTITY,
                    status=ExecutionGateCheckStatus.WARN,
                    title="Copilot next step is missing",
                    detail="Copilot output did not include a next step to compare against the persisted decision.",
                    observed_value=None,
                    expected_value=decision.triage_decision.recommended_next_step,
                )
            )
        else:
            matches_next_step = expected_next_step in observed_next_steps
            checks.append(
                _check(
                    check_id="copilot_next_step",
                    category=ExecutionGateCheckCategory.IDENTITY,
                    status=(
                        ExecutionGateCheckStatus.PASS
                        if matches_next_step
                        else ExecutionGateCheckStatus.FAIL
                    ),
                    title="Copilot next step matches decision",
                    detail=(
                        "Copilot next step matches the persisted decision."
                        if matches_next_step
                        else "Copilot next step drifted from the persisted decision."
                    ),
                    observed_value=" | ".join(copilot.next_steps),
                    expected_value=decision.triage_decision.recommended_next_step,
                )
            )

    if semantic_ui is None or semantic_ui.decision_card is None:
        checks.append(
            _check(
                check_id="semantic_decision_card",
                category=ExecutionGateCheckCategory.IDENTITY,
                status=ExecutionGateCheckStatus.WARN,
                title="Semantic decision card is unavailable",
                detail="No semantic UI decision card was available for triage and severity comparison.",
            )
        )
        return checks

    expected_card = {
        "triage_decision": _enum_value(decision.triage_decision.triage),
        "severity": _enum_value(decision.triage_decision.severity),
    }
    observed_card = {
        "triage_decision": _enum_value(semantic_ui.decision_card.triage_decision),
        "severity": _enum_value(semantic_ui.decision_card.severity),
    }
    for field_name, expected_value in expected_card.items():
        observed_value = observed_card[field_name]
        status = (
            ExecutionGateCheckStatus.PASS
            if observed_value == expected_value
            else ExecutionGateCheckStatus.FAIL
        )
        checks.append(
            _check(
                check_id=f"decision_card_{field_name}",
                category=ExecutionGateCheckCategory.IDENTITY,
                status=status,
                title=f"Decision card {field_name} matches decision",
                detail=(
                    f"Semantic UI decision card {field_name} matches the persisted decision."
                    if status == ExecutionGateCheckStatus.PASS
                    else f"Semantic UI decision card {field_name} drifted from the persisted decision."
                ),
                observed_value=observed_value,
                expected_value=expected_value,
            )
        )
    return checks


def _numeric_checks(
    decision: DecisionObject,
    *,
    semantic_ui: SemanticUIObject | None,
) -> tuple[list[ExecutionGateCheck], NumericConsistencySummary]:
    expected_values = _expected_numeric_values(decision)
    observations = _semantic_ui_numeric_observations(semantic_ui)
    observations_by_field: dict[str, list[NumericObservation]] = {key: [] for key in expected_values}
    unbound_observations: list[NumericObservation] = []
    for observation in observations:
        if observation.field_key in observations_by_field:
            observations_by_field[observation.field_key].append(observation)
        else:
            unbound_observations.append(observation)

    checks: list[ExecutionGateCheck] = []
    matched_fields: list[str] = []
    mismatched_fields: list[str] = []
    for field_key, expected_value in expected_values.items():
        field_observations = observations_by_field[field_key]
        if not field_observations:
            mismatched_fields.append(field_key)
            checks.append(
                _check(
                    check_id=f"numeric_{field_key}",
                    category=ExecutionGateCheckCategory.NUMERIC_CONSISTENCY,
                    status=ExecutionGateCheckStatus.WARN,
                    title=f"{field_key} is not rendered",
                    detail=f"No semantic UI numeric value was found for {field_key}.",
                    observed_value=None,
                    expected_value=expected_value,
                )
            )
            continue

        bad_observations = [
            observation
            for observation in field_observations
            if not _numbers_match(observation.value, expected_value)
        ]
        if bad_observations:
            mismatched_fields.append(field_key)
            first_bad = bad_observations[0]
            checks.append(
                _check(
                    check_id=f"numeric_{field_key}",
                    category=ExecutionGateCheckCategory.NUMERIC_CONSISTENCY,
                    status=ExecutionGateCheckStatus.FAIL,
                    title=f"{field_key} matches grounded value",
                    detail=(
                        f"{first_bad.location} rendered {first_bad.value}, which does not match "
                        f"the persisted {field_key} value."
                    ),
                    observed_value=first_bad.value,
                    expected_value=expected_value,
                )
            )
            continue

        matched_fields.append(field_key)
        checks.append(
            _check(
                check_id=f"numeric_{field_key}",
                category=ExecutionGateCheckCategory.NUMERIC_CONSISTENCY,
                status=ExecutionGateCheckStatus.PASS,
                title=f"{field_key} matches grounded value",
                detail=f"Rendered {field_key} is traceable to the persisted decision value.",
                observed_value=field_observations[0].value,
                expected_value=expected_value,
            )
        )

    for index, observation in enumerate(unbound_observations):
        checks.append(
            _check(
                check_id=f"numeric_unbound_{index}",
                category=ExecutionGateCheckCategory.NUMERIC_CONSISTENCY,
                status=ExecutionGateCheckStatus.FAIL,
                title="Rendered number has no grounded field",
                detail=f"{observation.location} rendered a number that could not be bound to a grounded field.",
                observed_value=observation.value,
            )
        )

    numeric_consistency = NumericConsistencySummary(
        checked_fields=list(expected_values),
        matched_fields=matched_fields,
        mismatched_fields=mismatched_fields,
        consistency_ratio=len(matched_fields) / len(expected_values),
    )
    return checks, numeric_consistency


def _policy_alignment_checks(
    decision: DecisionObject,
    *,
    semantic_ui: SemanticUIObject | None,
    copilot: CopilotResponse | None,
) -> tuple[list[ExecutionGateCheck], PolicyAlignmentSummary]:
    expected_triage = _enum_value(decision.triage_decision.triage)
    expected_severity = _enum_value(decision.triage_decision.severity)
    expected_next_step = _normalize_free_text(decision.triage_decision.recommended_next_step)
    checks: list[ExecutionGateCheck] = []

    triage_matches = True
    severity_matches = True
    if semantic_ui is not None and semantic_ui.decision_card is not None:
        observed_triage = _enum_value(semantic_ui.decision_card.triage_decision)
        triage_matches = observed_triage == expected_triage
        checks.append(
            _check(
                check_id="policy_triage_alignment",
                category=ExecutionGateCheckCategory.POLICY_ALIGNMENT,
                status=(
                    ExecutionGateCheckStatus.PASS
                    if triage_matches
                    else ExecutionGateCheckStatus.FAIL
                ),
                title="Rendered triage follows policy",
                detail=(
                    "Rendered triage matches the persisted policy decision."
                    if triage_matches
                    else "Rendered triage overrides the persisted policy decision."
                ),
                observed_value=observed_triage,
                expected_value=expected_triage,
            )
        )
        observed_severity = _enum_value(semantic_ui.decision_card.severity)
        severity_matches = observed_severity == expected_severity
        checks.append(
            _check(
                check_id="policy_severity_alignment",
                category=ExecutionGateCheckCategory.POLICY_ALIGNMENT,
                status=(
                    ExecutionGateCheckStatus.PASS
                    if severity_matches
                    else ExecutionGateCheckStatus.FAIL
                ),
                title="Rendered severity follows policy",
                detail=(
                    "Rendered severity matches the persisted policy decision."
                    if severity_matches
                    else "Rendered severity overrides the persisted policy decision."
                ),
                observed_value=observed_severity,
                expected_value=expected_severity,
            )
        )

    next_step_matches = True
    if copilot is not None:
        observed_next_steps = [_normalize_free_text(item) for item in copilot.next_steps]
        if not observed_next_steps:
            next_step_matches = False
            checks.append(
                _check(
                    check_id="policy_next_step_alignment",
                    category=ExecutionGateCheckCategory.POLICY_ALIGNMENT,
                    status=ExecutionGateCheckStatus.WARN,
                    title="Generated next step is missing",
                    detail="Copilot output does not include the persisted policy next step.",
                    observed_value=None,
                    expected_value=decision.triage_decision.recommended_next_step,
                )
            )
        else:
            next_step_matches = expected_next_step in observed_next_steps
            checks.append(
                _check(
                    check_id="policy_next_step_alignment",
                    category=ExecutionGateCheckCategory.POLICY_ALIGNMENT,
                    status=(
                        ExecutionGateCheckStatus.PASS
                        if next_step_matches
                        else ExecutionGateCheckStatus.FAIL
                    ),
                    title="Generated next step follows policy",
                    detail=(
                        "Generated next step matches the persisted policy next step."
                        if next_step_matches
                        else "Generated next step overrides the persisted policy next step."
                    ),
                    observed_value=" | ".join(copilot.next_steps),
                    expected_value=decision.triage_decision.recommended_next_step,
                )
            )

    policy_text = _normalize_policy_text(
        " ".join(_policy_text_fragments(semantic_ui=semantic_ui, copilot=copilot))
    )
    has_action_override = _contains_pattern(policy_text, _ACTION_OVERRIDE_PATTERNS)
    action_override_is_unsafe = has_action_override and expected_triage != "act"
    checks.append(
        _check(
            check_id="policy_action_override",
            category=ExecutionGateCheckCategory.POLICY_ALIGNMENT,
            status=(
                ExecutionGateCheckStatus.FAIL
                if action_override_is_unsafe
                else ExecutionGateCheckStatus.PASS
            ),
            title="Generated text does not override triage action",
            detail=(
                "Generated text does not recommend operational action outside the persisted triage."
                if not action_override_is_unsafe
                else "Generated text recommends action even though the persisted triage is not ACT."
            ),
            observed_value="action_override_detected" if has_action_override else None,
            expected_value=expected_triage,
        )
    )

    rationale_codes = {_enum_value(item) for item in decision.triage_decision.rationale_codes}
    confirmation_required = (
        expected_triage == "defer_to_lab"
        or "manual_confirmation_required" in rationale_codes
    )
    has_lab_bypass = _contains_pattern(policy_text, _LAB_CONFIRMATION_BYPASS_PATTERNS)
    lab_bypass_is_unsafe = confirmation_required and has_lab_bypass
    checks.append(
        _check(
            check_id="policy_lab_confirmation_boundary",
            category=ExecutionGateCheckCategory.POLICY_ALIGNMENT,
            status=(
                ExecutionGateCheckStatus.FAIL
                if lab_bypass_is_unsafe
                else ExecutionGateCheckStatus.PASS
            ),
            title="Generated text preserves lab confirmation boundary",
            detail=(
                "Generated text does not bypass required confirmation."
                if not lab_bypass_is_unsafe
                else "Generated text bypasses lab confirmation even though policy requires confirmation."
            ),
            observed_value="lab_bypass_detected" if has_lab_bypass else None,
            expected_value="confirmation_required" if confirmation_required else "confirmation_not_required",
        )
    )

    has_certainty_overclaim = _contains_pattern(policy_text, _CERTAINTY_OVERCLAIM_PATTERNS)
    checks.append(
        _check(
            check_id="policy_certainty_language",
            category=ExecutionGateCheckCategory.POLICY_ALIGNMENT,
            status=(
                ExecutionGateCheckStatus.WARN
                if has_certainty_overclaim
                else ExecutionGateCheckStatus.PASS
            ),
            title="Generated text avoids certainty overclaims",
            detail=(
                "Generated text avoids definitive clinical-certainty language."
                if not has_certainty_overclaim
                else "Generated text uses definitive certainty language that exceeds the current evidence boundary."
            ),
            observed_value="certainty_overclaim_detected" if has_certainty_overclaim else None,
            expected_value="uncertainty_preserved",
        )
    )

    notes: list[str] = []
    if action_override_is_unsafe:
        notes.append("action_override_detected")
    if lab_bypass_is_unsafe:
        notes.append("lab_confirmation_bypass_detected")
    if has_certainty_overclaim:
        notes.append("certainty_overclaim_detected")
    if copilot is not None and not next_step_matches:
        notes.append("next_step_mismatch")
    if semantic_ui is not None and semantic_ui.decision_card is not None:
        if not triage_matches:
            notes.append("triage_mismatch")
        if not severity_matches:
            notes.append("severity_mismatch")

    policy_alignment = PolicyAlignmentSummary(
        policy_version=decision.triage_decision.threshold_version,
        triage_matches_decision=triage_matches,
        severity_matches_decision=severity_matches,
        next_step_matches_decision=next_step_matches,
        unsafe_claims_detected=action_override_is_unsafe or lab_bypass_is_unsafe,
        notes=notes,
    )
    return checks, policy_alignment


def derive_gate_decision(checks: list[ExecutionGateCheck] | tuple[ExecutionGateCheck, ...]) -> ExecutionGateDecision:
    if any(check.status == ExecutionGateCheckStatus.FAIL for check in checks):
        return ExecutionGateDecision.BLOCK
    if any(check.status == ExecutionGateCheckStatus.WARN for check in checks):
        return ExecutionGateDecision.REVIEW
    return ExecutionGateDecision.ALLOW


def _issues_from_checks(checks: list[ExecutionGateCheck] | tuple[ExecutionGateCheck, ...]) -> list[ExecutionGateIssue]:
    issues: list[ExecutionGateIssue] = []
    for check in checks:
        if check.status == ExecutionGateCheckStatus.PASS:
            continue
        issues.append(
            ExecutionGateIssue(
                issue_id=f"issue_{check.check_id}",
                category=check.category,
                severity=(
                    ExecutionGateIssueSeverity.BLOCKING
                    if check.status == ExecutionGateCheckStatus.FAIL
                    else ExecutionGateIssueSeverity.WARNING
                ),
                title=check.title,
                detail=check.detail,
                evidence_refs=check.evidence_refs,
            )
        )
    return issues


def build_identity_numeric_checks(
    decision: DecisionObject,
    *,
    semantic_ui: SemanticUIObject | None = None,
    copilot: CopilotResponse | None = None,
) -> IdentityNumericVerificationResult:
    identity_checks = _identity_checks(decision, semantic_ui=semantic_ui, copilot=copilot)
    numeric_checks, numeric_consistency = _numeric_checks(decision, semantic_ui=semantic_ui)
    checks = tuple(identity_checks + numeric_checks)
    return IdentityNumericVerificationResult(
        checks=checks,
        numeric_consistency=numeric_consistency,
        gate_decision=derive_gate_decision(checks),
    )


def build_evidence_citation_checks(
    decision: DecisionObject,
    *,
    artifact_manifest: ArtifactManifest | None = None,
    semantic_ui: SemanticUIObject | None = None,
    copilot: CopilotResponse | None = None,
) -> EvidenceCitationVerificationResult:
    allowed_ids = _allowed_evidence_ids(decision, artifact_manifest=artifact_manifest)
    required_ids = _required_evidence_ids(decision)
    cited_ids = _sorted_unique(
        _copilot_cited_evidence_ids(copilot)
        + _semantic_ui_cited_evidence_ids(semantic_ui)
    )
    allowed_set = set(allowed_ids)
    required_set = set(required_ids)
    cited_set = set(cited_ids)
    invalid_ids = _sorted_unique(cited_set - allowed_set)
    covered_required_ids = _sorted_unique(required_set & cited_set)
    missing_required_ids = _sorted_unique(required_set - cited_set)

    checks: list[ExecutionGateCheck] = []
    checks.append(
        _check(
            check_id="citation_ids_allowed",
            category=ExecutionGateCheckCategory.CITATION_VALIDITY,
            status=(
                ExecutionGateCheckStatus.PASS
                if not invalid_ids
                else ExecutionGateCheckStatus.FAIL
            ),
            title="Cited evidence IDs are allowed",
            detail=(
                "Every rendered or generated citation resolves to the grounded case context."
                if not invalid_ids
                else "At least one rendered or generated citation is outside the grounded case context."
            ),
            observed_value=", ".join(invalid_ids) if invalid_ids else None,
            expected_value=f"{len(allowed_ids)} allowed evidence IDs",
        )
    )
    checks.append(
        _check(
            check_id="required_evidence_coverage",
            category=ExecutionGateCheckCategory.EVIDENCE_COVERAGE,
            status=(
                ExecutionGateCheckStatus.PASS
                if not missing_required_ids
                else ExecutionGateCheckStatus.WARN
            ),
            title="Required evidence is cited",
            detail=(
                "The rendered/generated payload covers all required decision evidence IDs."
                if not missing_required_ids
                else "The rendered/generated payload omits required decision evidence IDs."
            ),
            observed_value=", ".join(covered_required_ids) if covered_required_ids else None,
            expected_value=", ".join(required_ids),
        )
    )
    checks.extend(_rendered_semantic_evidence_row_checks(decision, semantic_ui=semantic_ui))

    evidence_coverage = EvidenceCoverageSummary(
        required_evidence_ids=required_ids,
        covered_evidence_ids=covered_required_ids,
        missing_evidence_ids=missing_required_ids,
        coverage_ratio=1.0 if not required_ids else len(covered_required_ids) / len(required_ids),
    )
    citation_validity = CitationValiditySummary(
        allowed_evidence_ids=allowed_ids,
        cited_evidence_ids=cited_ids,
        invalid_evidence_ids=invalid_ids,
        missing_required_evidence_ids=missing_required_ids,
        validity_ratio=1.0 if not cited_ids else (len(cited_set) - len(invalid_ids)) / len(cited_set),
    )
    return EvidenceCitationVerificationResult(
        checks=tuple(checks),
        evidence_coverage=evidence_coverage,
        citation_validity=citation_validity,
        gate_decision=derive_gate_decision(checks),
    )


def build_policy_alignment_checks(
    decision: DecisionObject,
    *,
    semantic_ui: SemanticUIObject | None = None,
    copilot: CopilotResponse | None = None,
) -> PolicyAlignmentVerificationResult:
    checks, policy_alignment = _policy_alignment_checks(
        decision,
        semantic_ui=semantic_ui,
        copilot=copilot,
    )
    return PolicyAlignmentVerificationResult(
        checks=tuple(checks),
        policy_alignment=policy_alignment,
        gate_decision=derive_gate_decision(checks),
    )


def build_reasoning_trace_checks(
    decision: DecisionObject,
    *,
    trace: ReasoningTrace | None = None,
) -> ReasoningTraceVerificationResult:
    if trace is None:
        return ReasoningTraceVerificationResult(checks=(), gate_decision=ExecutionGateDecision.ALLOW)

    expected_job_id = decision.job_id or decision.triage_decision.job_id
    expected_identity = {
        "job_id": expected_job_id,
        "sample_id": decision.sample.sample_id,
        "target_drug": decision.sample.target_drug,
        "decision": _enum_value(decision.triage_decision.triage),
        "severity": _enum_value(decision.triage_decision.severity),
    }
    observed_identity = {
        "job_id": trace.job_id,
        "sample_id": trace.sample_id,
        "target_drug": trace.target_drug,
        "decision": _enum_value(trace.decision),
        "severity": _enum_value(trace.severity),
    }
    identity_mismatches = [
        field_name
        for field_name, expected_value in expected_identity.items()
        if observed_identity[field_name] != expected_value
    ]

    required_step_types = list(REASONING_TRACE_REQUIRED_STEP_TYPES)
    trace_step_types = [step.step_type for step in trace.steps]
    present_step_types = set(trace_step_types)
    missing_step_types = [
        step_type
        for step_type in required_step_types
        if step_type not in present_step_types
    ]
    repeated_step_types = sorted(
        {
            _enum_value(step_type)
            for step_type in trace_step_types
            if trace_step_types.count(step_type) > 1
        }
    )
    expected_numbers = list(range(1, len(trace.steps) + 1))
    observed_numbers = [step.step_number for step in trace.steps]
    required_order = {step_type: index for index, step_type in enumerate(required_step_types)}
    observed_order = [required_order[step_type] for step_type in trace_step_types if step_type in required_order]
    order_is_valid = observed_order == sorted(observed_order) and observed_numbers == expected_numbers

    steps_missing_evidence = [
        _enum_value(step.step_type)
        for step in trace.steps
        if not step.evidence_refs
    ]

    declared_caveat_ids = {caveat.caveat_id for caveat in trace.caveats}
    unknown_caveat_refs = sorted(
        {
            caveat_id
            for step in trace.steps
            for caveat_id in step.caveat_ids
            if caveat_id not in declared_caveat_ids
        }
    )

    coverage_present = set(trace.coverage.present_step_types)
    coverage_missing = set(trace.coverage.missing_step_types)
    coverage_matches_steps = coverage_present == present_step_types
    expected_coverage_ratio = len(present_step_types) / len(required_step_types)
    coverage_ratio_matches = math.isclose(
        trace.coverage.coverage_ratio,
        expected_coverage_ratio,
        rel_tol=0.0,
        abs_tol=0.001,
    )

    checks = [
        _check(
            check_id="trace_identity_matches_decision",
            category=ExecutionGateCheckCategory.REASONING_TRACE,
            status=(
                ExecutionGateCheckStatus.PASS
                if not identity_mismatches
                else ExecutionGateCheckStatus.FAIL
            ),
            title="Reasoning trace identity matches decision",
            detail=(
                "Reasoning trace job, sample, target, triage, and severity match the persisted decision."
                if not identity_mismatches
                else "Reasoning trace identity or decision fields drift from the persisted decision."
            ),
            observed_value=", ".join(f"{field}={observed_identity[field]}" for field in identity_mismatches)
            if identity_mismatches
            else None,
            expected_value=", ".join(f"{field}={expected_identity[field]}" for field in identity_mismatches)
            if identity_mismatches
            else "persisted decision identity",
        ),
        _check(
            check_id="trace_required_steps_present",
            category=ExecutionGateCheckCategory.REASONING_TRACE,
            status=(
                ExecutionGateCheckStatus.PASS
                if not missing_step_types and not repeated_step_types
                else ExecutionGateCheckStatus.FAIL
            ),
            title="Reasoning trace covers required biological steps",
            detail=(
                "Reasoning trace includes each required biological reasoning step exactly once."
                if not missing_step_types and not repeated_step_types
                else "Reasoning trace is missing or repeating required biological reasoning steps."
            ),
            observed_value=", ".join(_enum_value(item) for item in trace_step_types),
            expected_value=", ".join(_enum_value(item) for item in required_step_types),
        ),
        _check(
            check_id="trace_step_order",
            category=ExecutionGateCheckCategory.REASONING_TRACE,
            status=(ExecutionGateCheckStatus.PASS if order_is_valid else ExecutionGateCheckStatus.FAIL),
            title="Reasoning trace follows required order",
            detail=(
                "Reasoning trace follows the required sample-to-triage order with sequential step numbers."
                if order_is_valid
                else "Reasoning trace order or step numbering drifted from the required sample-to-triage sequence."
            ),
            observed_value=", ".join(str(item) for item in observed_numbers),
            expected_value=", ".join(str(item) for item in expected_numbers),
        ),
        _check(
            check_id="trace_step_evidence_refs",
            category=ExecutionGateCheckCategory.REASONING_TRACE,
            status=(
                ExecutionGateCheckStatus.PASS
                if not steps_missing_evidence
                else ExecutionGateCheckStatus.FAIL
            ),
            title="Reasoning trace steps cite source evidence",
            detail=(
                "Every reasoning trace step carries at least one source evidence reference."
                if not steps_missing_evidence
                else "At least one reasoning trace step is missing source evidence references."
            ),
            observed_value=", ".join(steps_missing_evidence) if steps_missing_evidence else None,
            expected_value="evidence refs on every trace step",
        ),
        _check(
            check_id="trace_caveat_refs_declared",
            category=ExecutionGateCheckCategory.REASONING_TRACE,
            status=(
                ExecutionGateCheckStatus.PASS
                if not unknown_caveat_refs
                else ExecutionGateCheckStatus.FAIL
            ),
            title="Reasoning trace caveat refs are declared",
            detail=(
                "Reasoning trace step caveat IDs all reference declared caveats."
                if not unknown_caveat_refs
                else "Reasoning trace step caveat IDs include undeclared caveats."
            ),
            observed_value=", ".join(unknown_caveat_refs) if unknown_caveat_refs else None,
            expected_value="declared caveat IDs",
        ),
        _check(
            check_id="trace_coverage_matches_steps",
            category=ExecutionGateCheckCategory.REASONING_TRACE,
            status=(
                ExecutionGateCheckStatus.PASS
                if coverage_matches_steps and not coverage_missing and coverage_ratio_matches
                else ExecutionGateCheckStatus.FAIL
            ),
            title="Reasoning trace coverage matches steps",
            detail=(
                "Reasoning trace coverage summary matches the actual emitted steps."
                if coverage_matches_steps and not coverage_missing and coverage_ratio_matches
                else "Reasoning trace coverage summary does not match the actual emitted steps."
            ),
            observed_value=trace.coverage.coverage_ratio,
            expected_value=expected_coverage_ratio,
        ),
    ]
    return ReasoningTraceVerificationResult(
        checks=tuple(checks),
        gate_decision=derive_gate_decision(checks),
    )


def build_policy_hash(
    *,
    policy_version: str,
    check_definitions: list[ExecutionGateCheck] | tuple[ExecutionGateCheck, ...],
) -> str:
    policy_payload = {
        "policy_version": policy_version,
        "check_definitions": [
            {
                "check_id": check.check_id,
                "category": _enum_value(check.category),
                "title": check.title,
            }
            for check in sorted(check_definitions, key=lambda item: item.check_id)
        ],
    }
    return _sha256_digest(policy_payload)


def build_audit_fingerprint(
    *,
    job_id: str,
    sample_id: str,
    target_drug: str,
    gate_decision: ExecutionGateDecision,
    checks: list[ExecutionGateCheck] | tuple[ExecutionGateCheck, ...],
    policy_hash: str,
    evidence_coverage: EvidenceCoverageSummary | None = None,
    numeric_consistency: NumericConsistencySummary | None = None,
    citation_validity: CitationValiditySummary | None = None,
    policy_alignment: PolicyAlignmentSummary | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    fingerprint_payload = {
        "job_id": job_id,
        "sample_id": sample_id,
        "target_drug": target_drug,
        "gate_decision": _enum_value(gate_decision),
        "checks": sorted(checks, key=lambda item: item.check_id),
        "policy_hash": policy_hash,
        "evidence_coverage": evidence_coverage,
        "numeric_consistency": numeric_consistency,
        "citation_validity": citation_validity,
        "policy_alignment": policy_alignment,
        "metadata": metadata or {},
    }
    return _sha256_digest(fingerprint_payload)


def build_audit_digest_bundle(
    *,
    job_id: str,
    sample_id: str,
    target_drug: str,
    gate_decision: ExecutionGateDecision,
    checks: list[ExecutionGateCheck] | tuple[ExecutionGateCheck, ...],
    policy_version: str,
    evidence_coverage: EvidenceCoverageSummary | None = None,
    numeric_consistency: NumericConsistencySummary | None = None,
    citation_validity: CitationValiditySummary | None = None,
    policy_alignment: PolicyAlignmentSummary | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditDigestBundle:
    policy_hash = build_policy_hash(policy_version=policy_version, check_definitions=checks)
    audit_fingerprint = build_audit_fingerprint(
        job_id=job_id,
        sample_id=sample_id,
        target_drug=target_drug,
        gate_decision=gate_decision,
        checks=checks,
        policy_hash=policy_hash,
        evidence_coverage=evidence_coverage,
        numeric_consistency=numeric_consistency,
        citation_validity=citation_validity,
        policy_alignment=policy_alignment,
        metadata=metadata,
    )
    return AuditDigestBundle(policy_hash=policy_hash, audit_fingerprint=audit_fingerprint)


def build_execution_gate_report(
    decision: DecisionObject,
    *,
    artifact_manifest: ArtifactManifest | None = None,
    semantic_ui: SemanticUIObject | None = None,
    copilot: CopilotResponse | None = None,
    reasoning_trace: ReasoningTrace | None = None,
    metadata: dict[str, Any] | None = None,
) -> ExecutionGateReport:
    identity_numeric = build_identity_numeric_checks(
        decision,
        semantic_ui=semantic_ui,
        copilot=copilot,
    )
    evidence_citation = build_evidence_citation_checks(
        decision,
        artifact_manifest=artifact_manifest,
        semantic_ui=semantic_ui,
        copilot=copilot,
    )
    policy_alignment = build_policy_alignment_checks(
        decision,
        semantic_ui=semantic_ui,
        copilot=copilot,
    )
    trace_verification = build_reasoning_trace_checks(decision, trace=reasoning_trace)
    checks = tuple(
        identity_numeric.checks
        + evidence_citation.checks
        + policy_alignment.checks
        + trace_verification.checks
    )
    gate_decision = derive_gate_decision(checks)
    policy_version = decision.triage_decision.threshold_version
    digests = build_audit_digest_bundle(
        job_id=decision.job_id or decision.triage_decision.job_id,
        sample_id=decision.sample.sample_id,
        target_drug=decision.sample.target_drug,
        gate_decision=gate_decision,
        checks=checks,
        policy_version=policy_version,
        evidence_coverage=evidence_citation.evidence_coverage,
        numeric_consistency=identity_numeric.numeric_consistency,
        citation_validity=evidence_citation.citation_validity,
        policy_alignment=policy_alignment.policy_alignment,
        metadata=metadata,
    )
    failed_count = sum(1 for check in checks if check.status == ExecutionGateCheckStatus.FAIL)
    warning_count = sum(1 for check in checks if check.status == ExecutionGateCheckStatus.WARN)
    issues = _issues_from_checks(checks)
    return ExecutionGateReport(
        job_id=decision.job_id or decision.triage_decision.job_id,
        sample_id=decision.sample.sample_id,
        decision=decision.triage_decision.triage,
        severity=decision.triage_decision.severity,
        gate_decision=gate_decision,
        summary=(
            f"Execution gate {gate_decision.value.upper()} with "
            f"{failed_count} failed and {warning_count} warning checks."
        ),
        checks=list(checks),
        evidence_coverage=evidence_citation.evidence_coverage,
        numeric_consistency=identity_numeric.numeric_consistency,
        citation_validity=evidence_citation.citation_validity,
        policy_alignment=policy_alignment.policy_alignment,
        issues=issues,
        policy_hash=digests.policy_hash,
        audit_fingerprint=digests.audit_fingerprint,
        metadata=metadata or {},
    )
