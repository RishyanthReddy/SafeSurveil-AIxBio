from __future__ import annotations

from dataclasses import dataclass
from http.client import IncompleteRead
import json
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from app.contracts import JobStatus, SemanticUIObject
from app.settings import ThesysSettings


_RETRYABLE_HTTP_STATUS_CODES = {408, 429, 500, 502, 503, 504}
_RETRYABLE_TRANSPORT_EXCEPTIONS = (IncompleteRead, TimeoutError, ConnectionError)


class ThesysC1Error(RuntimeError):
    """Raised when the Thesys C1 renderer boundary cannot produce a response."""


class ThesysC1ConfigurationError(ThesysC1Error):
    """Raised when required C1 configuration is missing."""


@dataclass(frozen=True)
class ThesysC1TransportRequest:
    method: str
    url: str
    headers: dict[str, str]
    body: bytes
    timeout_seconds: int


@dataclass(frozen=True)
class ThesysC1TransportResponse:
    status_code: int
    body: bytes
    content_type: str | None = None


ThesysC1Transport = Callable[[ThesysC1TransportRequest], ThesysC1TransportResponse]


def _default_transport(request: ThesysC1TransportRequest) -> ThesysC1TransportResponse:
    urllib_request = Request(
        request.url,
        data=request.body,
        headers=request.headers,
        method=request.method,
    )
    try:
        with urlopen(urllib_request, timeout=request.timeout_seconds) as response:
            return ThesysC1TransportResponse(
                status_code=response.status,
                body=response.read(),
                content_type=response.headers.get("Content-Type"),
            )
    except HTTPError as exc:
        return ThesysC1TransportResponse(
            status_code=exc.code,
            body=exc.read(),
            content_type=exc.headers.get("Content-Type") if exc.headers else None,
        )
    except URLError as exc:
        raise ThesysC1Error(f"Thesys C1 request failed: {exc.reason}") from exc


def _chat_completions_url(base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    if not normalized:
        raise ThesysC1ConfigurationError("THESYS_BASE_URL must not be empty.")
    path = urlsplit(normalized).path.rstrip("/")
    if path.endswith("/chat/completions"):
        return normalized
    return f"{normalized}/chat/completions"


def _extract_message_text(message: Mapping[str, Any]) -> str | None:
    content = message.get("content")
    if isinstance(content, str):
        return content.strip() or None
    if isinstance(content, list):
        text_parts = [
            item.get("text", "")
            for item in content
            if isinstance(item, Mapping) and isinstance(item.get("text"), str)
        ]
        text = "".join(text_parts).strip()
        if text:
            return text
    return None


def _build_c1_messages(job_status: JobStatus, semantic_ui: SemanticUIObject) -> list[dict[str, str]]:
    semantic_ui_json = semantic_ui.model_dump_json(indent=2)
    status_json = job_status.model_dump_json(indent=2)
    return [
        {
            "role": "system",
            "content": (
                "You are a renderer for SafeSurveil-AIxBio clinical triage dashboards. "
                "Generate a Thesys C1 response that faithfully displays the supplied semantic_ui JSON. "
                "Do not add, remove, recalculate, relabel, or invent scientific values, citations, evidence IDs, "
                "mechanisms, risk scores, or triage fields. If content is missing, display a clear unavailable state."
            ),
        },
        {
            "role": "user",
            "content": (
                "Render this case as a compact clinical dashboard using the provided semantic UI contract only.\n\n"
                f"JOB_STATUS_JSON:\n{status_json}\n\n"
                f"SEMANTIC_UI_JSON:\n{semantic_ui_json}"
            ),
        },
    ]


class ThesysC1Client:
    def __init__(
        self,
        settings: ThesysSettings,
        *,
        transport: ThesysC1Transport | None = None,
    ) -> None:
        if not settings.api_key:
            raise ThesysC1ConfigurationError("THESYS_API_KEY is not configured.")
        if not settings.model.strip():
            raise ThesysC1ConfigurationError("THESYS_MODEL must not be empty.")
        self.settings = settings
        self._endpoint = _chat_completions_url(settings.base_url)
        self._transport = transport or _default_transport

    def render_semantic_ui(
        self,
        *,
        job_status: JobStatus,
        semantic_ui: SemanticUIObject,
    ) -> str:
        payload = {
            "model": self.settings.model,
            "messages": _build_c1_messages(job_status, semantic_ui),
            "stream": False,
            "temperature": 0.1,
        }
        transport_request = ThesysC1TransportRequest(
            method="POST",
            url=self._endpoint,
            headers={
                "Authorization": f"Bearer {self.settings.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "SafeSurveil-AIxBio/0.1",
            },
            body=json.dumps(payload).encode("utf-8"),
            timeout_seconds=self.settings.timeout_seconds,
        )
        last_error: ThesysC1Error | None = None
        attempts = self.settings.retry_count + 1
        for attempt_index in range(attempts):
            try:
                response = self._transport(transport_request)
            except ThesysC1Error as exc:
                last_error = exc
                if attempt_index + 1 < attempts:
                    continue
                break
            except _RETRYABLE_TRANSPORT_EXCEPTIONS as exc:
                last_error = ThesysC1Error(f"Thesys C1 request failed: {exc}")
                if attempt_index + 1 < attempts:
                    continue
                break
            if 200 <= response.status_code < 300:
                return self._parse_response(response)
            if (
                response.status_code in _RETRYABLE_HTTP_STATUS_CODES
                and attempt_index + 1 < attempts
            ):
                continue
            raise ThesysC1Error(f"Thesys C1 request failed with HTTP {response.status_code}.")
        if last_error is not None:
            raise last_error
        raise ThesysC1Error("Thesys C1 request failed.")

    def _parse_response(self, response: ThesysC1TransportResponse) -> str:
        try:
            payload = json.loads(response.body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ThesysC1Error("Thesys C1 returned invalid JSON.") from exc

        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ThesysC1Error("Thesys C1 response did not include any choices.")
        first_choice = choices[0]
        if not isinstance(first_choice, Mapping):
            raise ThesysC1Error("Thesys C1 response included an invalid choice payload.")
        message = first_choice.get("message")
        if not isinstance(message, Mapping):
            raise ThesysC1Error("Thesys C1 response did not include a message payload.")
        content = _extract_message_text(message)
        if content is None:
            raise ThesysC1Error("Thesys C1 response did not include renderable content.")
        return content


def build_thesys_c1_client(settings: ThesysSettings) -> ThesysC1Client:
    return ThesysC1Client(settings)
