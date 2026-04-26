from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
from typing import Literal

import pytest
from pydantic import BaseModel, Field

from app.contracts import CopilotResponse
from app.api.dependencies import get_llm_client
from app.llm import (
    LLMClientConfigurationError,
    LLMRequest,
    LLMResponseValidationError,
    OpenRouterLLMClient,
    build_llm_client,
)
from app.settings import LLMSettings, load_settings


class _SmokeEnvelope(BaseModel):
    status: Literal["ok"]
    summary: str = Field(min_length=5, max_length=200)


@contextmanager
def _run_openrouter_test_server(responses: list[dict[str, object]]):
    state: dict[str, object] = {
        "requests": [],
        "responses": [dict(item) for item in responses],
    }

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            content_length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(content_length)
            request_payload = json.loads(raw_body.decode("utf-8"))
            state["requests"].append(
                {
                    "path": self.path,
                    "headers": dict(self.headers.items()),
                    "body": request_payload,
                }
            )
            queued = state["responses"]
            assert isinstance(queued, list)
            response = queued.pop(0)
            status = int(response["status"])
            body = response["body"]
            if isinstance(body, str):
                response_bytes = body.encode("utf-8")
            else:
                response_bytes = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response_bytes)))
            self.end_headers()
            self.wfile.write(response_bytes)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A003
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, state
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _settings_for_test_server(port: int, *, retry_count: int = 0) -> LLMSettings:
    return LLMSettings(
        provider="openrouter",
        base_url=f"http://127.0.0.1:{port}/api/v1",
        api_key="test-api-key",
        model="demo-model",
        reasoning_enabled=True,
        timeout_seconds=5,
        retry_count=retry_count,
    )


def _smoke_request() -> LLMRequest:
    return LLMRequest(
        operation="phase7_smoke",
        messages=(
            {
                "role": "system",
                "content": "Return only JSON with fields status and summary.",
            },
            {
                "role": "user",
                "content": "Acknowledge the smoke test.",
            },
        ),
        output_format="json",
        max_output_tokens=120,
    )


def test_load_settings_reads_llm_configuration_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example/api/v1")
    monkeypatch.setenv("LLM_API_KEY", "test-secret")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LLM_REASONING_ENABLED", "true")
    monkeypatch.setenv("LLM_MOCK_MODE", "true")
    monkeypatch.setenv("LIVE_HTTP_TIMEOUT_SECONDS", "41")
    monkeypatch.setenv("LIVE_HTTP_RETRY_COUNT", "5")
    get_llm_client.cache_clear()

    settings = load_settings()

    assert settings.llm.provider == "openrouter"
    assert settings.llm.base_url == "https://llm.example/api/v1"
    assert settings.llm.api_key == "test-secret"
    assert settings.llm.model == "test-model"
    assert settings.llm.fallback_model == "inclusionai/ling-2.6-flash:free"
    assert settings.llm.reasoning_enabled is True
    assert settings.llm.mock_mode is True
    assert settings.llm.timeout_seconds == 41
    assert settings.llm.retry_count == 5


def test_build_llm_client_returns_openrouter_client() -> None:
    client = build_llm_client(
        LLMSettings(
            provider="openrouter",
            base_url="https://openrouter.ai/api/v1",
            api_key="test-key",
            model="test-model",
        )
    )

    assert isinstance(client, OpenRouterLLMClient)


def test_build_llm_client_rejects_missing_provider() -> None:
    with pytest.raises(LLMClientConfigurationError, match="LLM_PROVIDER"):
        build_llm_client(LLMSettings())


def test_openrouter_client_posts_json_request_and_validates_response() -> None:
    with _run_openrouter_test_server(
        [
            {
                "status": 200,
                "body": {
                    "id": "gen-test-001",
                    "object": "chat.completion",
                    "model": "demo-model",
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": json.dumps(
                                    {
                                        "status": "ok",
                                        "summary": "Smoke test acknowledged cleanly.",
                                    }
                                ),
                            },
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 12,
                        "completion_tokens": 8,
                        "total_tokens": 20,
                    },
                },
            }
        ]
    ) as (server, state):
        client = build_llm_client(_settings_for_test_server(server.server_port))
        validated = client.generate_validated(_smoke_request(), _SmokeEnvelope)

    requests = state["requests"]
    assert isinstance(requests, list)
    assert len(requests) == 1
    captured = requests[0]
    assert captured["path"] == "/api/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer test-api-key"
    assert captured["body"]["model"] == "demo-model"
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert captured["body"]["reasoning"] == {"enabled": True}
    assert validated.parsed.status == "ok"
    assert validated.response.response_id == "gen-test-001"
    assert validated.response.usage is not None
    assert validated.response.usage.total_tokens == 20


def test_openrouter_client_retries_retryable_http_statuses() -> None:
    with _run_openrouter_test_server(
        [
            {
                "status": 503,
                "body": {"error": {"message": "temporary upstream failure"}},
            },
            {
                "status": 200,
                "body": {
                    "id": "gen-test-002",
                    "object": "chat.completion",
                    "model": "demo-model",
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": json.dumps(
                                    {
                                        "status": "ok",
                                        "summary": "Retry path succeeded.",
                                    }
                                ),
                            },
                        }
                    ],
                },
            },
        ]
    ) as (server, state):
        client = build_llm_client(_settings_for_test_server(server.server_port, retry_count=1))
        request = LLMRequest(
            operation="phase7_smoke",
            messages=(
                {
                    "role": "system",
                    "content": "Return only JSON with fields status and summary.",
                },
                {
                    "role": "user",
                    "content": "Acknowledge the smoke test.",
                },
            ),
            output_format="json",
            max_output_tokens=120,
            retry_count=1,
        )

        validated = client.generate_validated(request, _SmokeEnvelope)

    requests = state["requests"]
    assert isinstance(requests, list)
    assert len(requests) == 2
    assert validated.response.attempt_count == 2
    assert validated.parsed.summary == "Retry path succeeded."


def test_openrouter_client_falls_back_to_ling_when_primary_model_fails() -> None:
    with _run_openrouter_test_server(
        [
            {
                "status": 429,
                "body": {"error": {"message": "gemma rate limited"}},
            },
            {
                "status": 200,
                "body": {
                    "id": "gen-test-004",
                    "object": "chat.completion",
                    "model": "inclusionai/ling-2.6-flash:free",
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": json.dumps(
                                    {
                                        "status": "ok",
                                        "summary": "Fallback model succeeded.",
                                    }
                                ),
                            },
                        }
                    ],
                },
            },
        ]
    ) as (server, state):
        client = build_llm_client(
            LLMSettings(
                provider="openrouter",
                base_url=f"http://127.0.0.1:{server.server_port}/api/v1",
                api_key="test-api-key",
                model="google/gemma-4-31b-it:free",
                fallback_model="inclusionai/ling-2.6-flash:free",
                reasoning_enabled=False,
                timeout_seconds=5,
                retry_count=0,
            )
        )

        validated = client.generate_validated(_smoke_request(), _SmokeEnvelope)

    requests = state["requests"]
    assert isinstance(requests, list)
    assert len(requests) == 2
    assert requests[0]["body"]["model"] == "google/gemma-4-31b-it:free"
    assert requests[1]["body"]["model"] == "inclusionai/ling-2.6-flash:free"
    assert validated.response.model == "inclusionai/ling-2.6-flash:free"
    assert validated.parsed.summary == "Fallback model succeeded."


def test_openrouter_client_rejects_invalid_schema_output() -> None:
    with _run_openrouter_test_server(
        [
            {
                "status": 200,
                "body": {
                    "id": "gen-test-003",
                    "object": "chat.completion",
                    "model": "demo-model",
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": json.dumps({"status": "ok"}),
                            },
                        }
                    ],
                },
            }
        ]
    ) as (server, _state):
        client = build_llm_client(_settings_for_test_server(server.server_port))
        request = LLMRequest(
            operation="phase7_smoke",
            messages=_smoke_request().messages,
            output_format="json",
            max_output_tokens=120,
            retry_count=0,
        )

        with pytest.raises(LLMResponseValidationError, match="SmokeEnvelope"):
            client.generate_validated(request, _SmokeEnvelope)


def test_openrouter_client_retries_schema_validation_failures() -> None:
    with _run_openrouter_test_server(
        [
            {
                "status": 200,
                "body": {
                    "id": "gen-test-003a",
                    "object": "chat.completion",
                    "model": "demo-model",
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": json.dumps({"status": "ok"}),
                            },
                        }
                    ],
                },
            },
            {
                "status": 200,
                "body": {
                    "id": "gen-test-003b",
                    "object": "chat.completion",
                    "model": "demo-model",
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": json.dumps(
                                    {
                                        "status": "ok",
                                        "summary": "Schema retry path recovered cleanly.",
                                    }
                                ),
                            },
                        }
                    ],
                },
            },
        ]
    ) as (server, state):
        client = build_llm_client(_settings_for_test_server(server.server_port))
        request = LLMRequest(
            operation="phase7_smoke",
            messages=_smoke_request().messages,
            output_format="json",
            max_output_tokens=120,
            retry_count=1,
        )

        validated = client.generate_validated(request, _SmokeEnvelope)

    requests = state["requests"]
    assert isinstance(requests, list)
    assert len(requests) == 2
    assert validated.parsed.summary == "Schema retry path recovered cleanly."


def test_openrouter_client_rejects_citations_outside_allowed_context() -> None:
    with _run_openrouter_test_server(
        [
            {
                "status": 200,
                "body": {
                    "id": "gen-test-006",
                    "object": "chat.completion",
                    "model": "demo-model",
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": json.dumps(
                                    {
                                        "job_id": "job_001",
                                        "sample_id": "sample_001",
                                        "target_drug": "tetracycline",
                                        "summary": "Grounded explanation text that is long enough.",
                                        "next_steps": [
                                            "confirm phenotype in downstream review flow"
                                        ],
                                        "refusal_required": False,
                                        "refusal_reason": None,
                                        "cited_evidence_ids": ["invented_evidence_123"],
                                        "answer_blocks": [
                                            {
                                                "block_id": "summary_block",
                                                "block_type": "summary",
                                                "content": "Grounded explanation text that is long enough.",
                                                "cited_evidence_ids": ["another_fake_id"],
                                            }
                                        ],
                                        "warnings": [],
                                    }
                                ),
                            },
                        }
                    ],
                },
            }
        ]
    ) as (server, _state):
        client = build_llm_client(_settings_for_test_server(server.server_port))
        request = LLMRequest(
            operation="decision_explanation",
            messages=(
                {
                    "role": "system",
                    "content": "Return only grounded copilot JSON.",
                },
                {
                    "role": "user",
                    "content": "Explain the current decision.",
                },
            ),
            metadata={
                "allowed_evidence_ids_json": json.dumps(
                    ["decision_object__summary", "decision_object__triage"]
                )
            },
            output_format="json",
            max_output_tokens=250,
        )

        with pytest.raises(
            LLMResponseValidationError,
            match="outside the grounded context",
        ):
            client.generate_validated(request, CopilotResponse)


def test_openrouter_client_maps_context_section_citations_to_evidence_ids() -> None:
    with _run_openrouter_test_server(
        [
            {
                "status": 200,
                "body": {
                    "id": "gen-test-006a",
                    "object": "chat.completion",
                    "model": "demo-model",
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": json.dumps(
                                    {
                                        "job_id": "job_001",
                                        "sample_id": "sample_001",
                                        "target_drug": "tetracycline",
                                        "summary": "Grounded explanation text that is long enough.",
                                        "next_steps": [
                                            "confirm phenotype in downstream review flow"
                                        ],
                                        "refusal_required": False,
                                        "refusal_reason": None,
                                        "cited_evidence_ids": ["limitations"],
                                        "answer_blocks": [
                                            {
                                                "block_id": "summary_block",
                                                "block_type": "summary",
                                                "content": "Grounded explanation text that is long enough.",
                                                "cited_evidence_ids": ["limitations"],
                                            }
                                        ],
                                        "warnings": [],
                                    }
                                ),
                            },
                        }
                    ],
                },
            }
        ]
    ) as (server, _state):
        client = build_llm_client(_settings_for_test_server(server.server_port))
        request = LLMRequest(
            operation="grounded_analyst_qa",
            messages=(
                {
                    "role": "system",
                    "content": "Return only grounded copilot JSON.",
                },
                {
                    "role": "user",
                    "content": "Why was this deferred?",
                },
            ),
            metadata={
                "allowed_evidence_ids_json": json.dumps(["decision_object__assembly_qc"]),
                "citation_aliases_json": json.dumps(
                    {"limitations": "decision_object__assembly_qc"}
                ),
            },
            output_format="json",
            max_output_tokens=250,
        )

        validated = client.generate_validated(request, CopilotResponse)

    assert validated.parsed.cited_evidence_ids == ["decision_object__assembly_qc"]
    assert validated.parsed.answer_blocks[0].cited_evidence_ids == [
        "decision_object__assembly_qc"
    ]


def test_openrouter_client_rejects_identity_drift_from_request_metadata() -> None:
    with _run_openrouter_test_server(
        [
            {
                "status": 200,
                "body": {
                    "id": "gen-test-006b",
                    "object": "chat.completion",
                    "model": "demo-model",
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": json.dumps(
                                    {
                                        "job_id": "job_other",
                                        "sample_id": "sample_001",
                                        "target_drug": "tetracycline",
                                        "summary": "Grounded explanation text that is long enough.",
                                        "next_steps": [
                                            "confirm phenotype in downstream review flow"
                                        ],
                                        "refusal_required": False,
                                        "refusal_reason": None,
                                        "cited_evidence_ids": ["decision_object__summary"],
                                        "answer_blocks": [
                                            {
                                                "block_id": "summary_block",
                                                "block_type": "summary",
                                                "content": "Grounded explanation text that is long enough.",
                                                "cited_evidence_ids": ["decision_object__summary"],
                                            }
                                        ],
                                        "warnings": [],
                                    }
                                ),
                            },
                        }
                    ],
                },
            }
        ]
    ) as (server, _state):
        client = build_llm_client(_settings_for_test_server(server.server_port))
        request = LLMRequest(
            operation="decision_explanation",
            messages=(
                {
                    "role": "system",
                    "content": "Return only grounded copilot JSON.",
                },
                {
                    "role": "user",
                    "content": "Explain the current decision.",
                },
            ),
            metadata={
                "job_id": "job_001",
                "sample_id": "sample_001",
                "target_drug": "tetracycline",
                "allowed_evidence_ids_json": json.dumps(["decision_object__summary"]),
            },
            output_format="json",
            max_output_tokens=250,
        )

        with pytest.raises(
            LLMResponseValidationError,
            match="identity drifted",
        ):
            client.generate_validated(request, CopilotResponse)


def test_openrouter_client_normalizes_null_citation_arrays() -> None:
    with _run_openrouter_test_server(
        [
            {
                "status": 200,
                "body": {
                    "id": "gen-test-006a",
                    "object": "chat.completion",
                    "model": "demo-model",
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": json.dumps(
                                    {
                                        "job_id": "job_001",
                                        "sample_id": "sample_001",
                                        "target_drug": "tetracycline",
                                        "summary": "Grounded queue summary.",
                                        "next_steps": ["confirm phenotype in downstream review flow"],
                                        "refusal_required": False,
                                        "refusal_reason": None,
                                        "cited_evidence_ids": ["decision_object__summary"],
                                        "answer_blocks": [
                                            {
                                                "block_id": "queue_summary",
                                                "block_type": "summary",
                                                "content": "Grounded queue handoff.",
                                                "cited_evidence_ids": None,
                                            }
                                        ],
                                        "warnings": [],
                                    }
                                ),
                            },
                        }
                    ],
                },
            }
        ]
    ) as (server, _state):
        client = build_llm_client(_settings_for_test_server(server.server_port))
        request = LLMRequest(
            operation="queue_summary_handoff",
            messages=(
                {
                    "role": "system",
                    "content": "Return only grounded queue JSON.",
                },
                {
                    "role": "user",
                    "content": "Build a queue summary.",
                },
            ),
            output_format="json",
            retry_count=0,
            max_output_tokens=250,
        )

        validated = client.generate_validated(request, CopilotResponse)

    assert validated.parsed.cited_evidence_ids == ["decision_object__summary"]
    assert validated.parsed.answer_blocks[0].cited_evidence_ids == []


def test_openrouter_client_rejects_semantic_ui_row_evidence_mismatch() -> None:
    with _run_openrouter_test_server(
        [
            {
                "status": 200,
                "body": {
                    "id": "gen-test-006b",
                    "object": "chat.completion",
                    "model": "demo-model",
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": json.dumps(
                                    {
                                        "job_id": "job_001",
                                        "sample_id": "sample_001",
                                        "target_drug": "tetracycline",
                                        "summary": "Grounded semantic UI summary for the case detail surface.",
                                        "next_steps": [
                                            "confirm phenotype in downstream review flow"
                                        ],
                                        "refusal_required": False,
                                        "refusal_reason": None,
                                        "cited_evidence_ids": [
                                            "decision_object__summary",
                                            "mechanistic_evidence__4",
                                        ],
                                        "answer_blocks": [
                                            {
                                                "block_id": "ui_summary",
                                                "block_type": "summary",
                                                "content": "Grounded semantic UI summary for the case detail surface.",
                                                "cited_evidence_ids": ["decision_object__summary"],
                                            }
                                        ],
                                        "semantic_ui": {
                                            "decision_card": {
                                                "title": "Decision Overview",
                                                "triage_decision": "defer_to_lab",
                                                "severity": "high",
                                                "summary": "High novelty keeps this case in manual confirmation.",
                                                "metrics": [],
                                            },
                                            "evidence_table": {
                                                "title": "Evidence Summary",
                                                "columns": ["signal", "detail", "support"],
                                                "rows": [
                                                    {
                                                        "row_id": "mechanism_3",
                                                        "label": "dfrA8",
                                                        "cells": {
                                                            "signal": "dfrA8",
                                                            "detail": "Detected dfrA8 with exact support.",
                                                            "support": "supported",
                                                        },
                                                        "evidence_id": "mechanistic_evidence__4",
                                                    }
                                                ],
                                            },
                                        },
                                        "warnings": [],
                                    }
                                ),
                            },
                        }
                    ],
                },
            }
        ]
    ) as (server, _state):
        client = build_llm_client(_settings_for_test_server(server.server_port))
        request = LLMRequest(
            operation="semantic_ui_payload",
            messages=(
                {
                    "role": "system",
                    "content": "Return only grounded semantic UI JSON.",
                },
                {
                    "role": "user",
                    "content": "Build a semantic UI payload.",
                },
            ),
            metadata={
                "allowed_evidence_ids_json": json.dumps(
                    ["decision_object__summary", "mechanistic_evidence__4"]
                ),
                "grounded_mechanistic_evidence_json": json.dumps(
                    {"mechanistic_evidence__4": ["sul2"]}
                ),
            },
            output_format="json",
            max_output_tokens=300,
        )

        with pytest.raises(
            LLMResponseValidationError,
            match="mechanistic evidence rows",
        ):
            client.generate_validated(request, CopilotResponse)


def test_openrouter_client_rejects_semantic_ui_numbers_outside_grounded_context() -> None:
    with _run_openrouter_test_server(
        [
            {
                "status": 200,
                "body": {
                    "id": "gen-test-007",
                    "object": "chat.completion",
                    "model": "demo-model",
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": json.dumps(
                                    {
                                        "job_id": "job_001",
                                        "sample_id": "sample_001",
                                        "target_drug": "tetracycline",
                                        "summary": "Grounded semantic UI summary for the case detail surface.",
                                        "next_steps": [
                                            "confirm phenotype in downstream review flow"
                                        ],
                                        "refusal_required": False,
                                        "refusal_reason": None,
                                        "cited_evidence_ids": ["decision_object__summary"],
                                        "answer_blocks": [
                                            {
                                                "block_id": "ui_summary",
                                                "block_type": "summary",
                                                "content": "Grounded semantic UI summary for the case detail surface.",
                                                "cited_evidence_ids": ["decision_object__summary"],
                                            }
                                        ],
                                        "semantic_ui": {
                                            "decision_card": {
                                                "title": "Decision Overview",
                                                "triage_decision": "defer_to_lab",
                                                "severity": "high",
                                                "summary": "High novelty and incomplete mechanism support keep this case in manual confirmation.",
                                                "metrics": [
                                                    {
                                                        "key": "probability",
                                                        "label": "Probability",
                                                        "value": 999.0,
                                                    }
                                                ],
                                            },
                                            "queue_block": {
                                                "title": "Analyst Queue",
                                                "items": [
                                                    {
                                                        "job_id": "job_001",
                                                        "sample_id": "sample_001",
                                                        "target_drug": "tetracycline",
                                                        "triage": "defer_to_lab",
                                                        "severity": "high",
                                                        "status": "completed",
                                                        "queue_priority": 10,
                                                        "headline": "Defer to lab for high-novelty tetracycline case",
                                                        "rationale_codes": [
                                                            "no_supported_mechanism",
                                                            "high_lineage_novelty",
                                                            "manual_confirmation_required",
                                                        ],
                                                    }
                                                ],
                                            },
                                        },
                                        "warnings": [],
                                    }
                                ),
                            },
                        }
                    ],
                },
            }
        ]
    ) as (server, _state):
        client = build_llm_client(_settings_for_test_server(server.server_port))
        request = LLMRequest(
            operation="semantic_ui_payload",
            messages=(
                {
                    "role": "system",
                    "content": "Return only grounded semantic UI JSON.",
                },
                {
                    "role": "user",
                    "content": "Build a semantic UI payload.",
                },
            ),
            metadata={
                "allowed_evidence_ids_json": json.dumps(["decision_object__summary"]),
                "grounded_numeric_values_json": json.dumps(
                    {
                        "probability": 0.84,
                        "novelty_score": 0.73,
                        "queue_priority": 10,
                    }
                ),
            },
            output_format="json",
            max_output_tokens=400,
        )

        with pytest.raises(
            LLMResponseValidationError,
            match="numeric values outside the grounded context",
        ):
            client.generate_validated(request, CopilotResponse)


def test_openrouter_client_rejects_semantic_ui_numbers_swapped_between_fields() -> None:
    with _run_openrouter_test_server(
        [
            {
                "status": 200,
                "body": {
                    "id": "gen-test-008",
                    "object": "chat.completion",
                    "model": "demo-model",
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": json.dumps(
                                    {
                                        "job_id": "job_001",
                                        "sample_id": "sample_001",
                                        "target_drug": "tetracycline",
                                        "summary": "Grounded semantic UI summary for the case detail surface.",
                                        "next_steps": [
                                            "confirm phenotype in downstream review flow"
                                        ],
                                        "refusal_required": False,
                                        "refusal_reason": None,
                                        "cited_evidence_ids": ["decision_object__summary"],
                                        "answer_blocks": [
                                            {
                                                "block_id": "ui_summary",
                                                "block_type": "summary",
                                                "content": "Grounded semantic UI summary for the case detail surface.",
                                                "cited_evidence_ids": ["decision_object__summary"],
                                            }
                                        ],
                                        "semantic_ui": {
                                            "decision_card": {
                                                "title": "Decision Overview",
                                                "triage_decision": "defer_to_lab",
                                                "severity": "high",
                                                "summary": "High novelty and incomplete mechanism support keep this case in manual confirmation.",
                                                "metrics": [
                                                    {
                                                        "key": "probability",
                                                        "label": "Probability",
                                                        "value": 0.73,
                                                    }
                                                ],
                                            },
                                            "risk_charts": [
                                                {
                                                    "chart_id": "risk_overview",
                                                    "title": "Risk Overview",
                                                    "chart_type": "bar",
                                                    "points": [
                                                        {
                                                            "label": "QC Risk",
                                                            "value": 0.73,
                                                            "evidence_id": "decision_object__summary",
                                                        }
                                                    ],
                                                }
                                            ],
                                        },
                                        "warnings": [],
                                    }
                                ),
                            },
                        }
                    ],
                },
            }
        ]
    ) as (server, _state):
        client = build_llm_client(_settings_for_test_server(server.server_port))
        request = LLMRequest(
            operation="semantic_ui_payload",
            messages=(
                {
                    "role": "system",
                    "content": "Return only grounded semantic UI JSON.",
                },
                {
                    "role": "user",
                    "content": "Build a semantic UI payload.",
                },
            ),
            metadata={
                "allowed_evidence_ids_json": json.dumps(["decision_object__summary"]),
                "grounded_numeric_values_json": json.dumps(
                    {
                        "probability": 0.84,
                        "novelty_score": 0.73,
                        "qc_risk": 0.25,
                    }
                ),
            },
            output_format="json",
            max_output_tokens=400,
        )

        with pytest.raises(
            LLMResponseValidationError,
            match="expected probability=0.84",
        ):
            client.generate_validated(request, CopilotResponse)


def test_openrouter_client_rejects_renamed_semantic_metric_keys() -> None:
    with _run_openrouter_test_server(
        [
            {
                "status": 200,
                "body": {
                    "id": "gen-test-008b",
                    "object": "chat.completion",
                    "model": "demo-model",
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": json.dumps(
                                    {
                                        "job_id": "job_001",
                                        "sample_id": "sample_001",
                                        "target_drug": "tetracycline",
                                        "summary": "Grounded semantic UI summary for the case detail surface.",
                                        "next_steps": [
                                            "confirm phenotype in downstream review flow"
                                        ],
                                        "refusal_required": False,
                                        "refusal_reason": None,
                                        "cited_evidence_ids": ["decision_object__summary"],
                                        "answer_blocks": [
                                            {
                                                "block_id": "ui_summary",
                                                "block_type": "summary",
                                                "content": "Grounded semantic UI summary for the case detail surface.",
                                                "cited_evidence_ids": ["decision_object__summary"],
                                            }
                                        ],
                                        "semantic_ui": {
                                            "decision_card": {
                                                "title": "Decision Overview",
                                                "triage_decision": "defer_to_lab",
                                                "severity": "high",
                                                "summary": "High novelty and incomplete mechanism support keep this case in manual confirmation.",
                                                "metrics": [
                                                    {
                                                        "key": "prediction_confidence",
                                                        "label": "Prediction Confidence",
                                                        "value": 0.84,
                                                    }
                                                ],
                                            }
                                        },
                                        "warnings": [],
                                    }
                                ),
                            },
                        }
                    ],
                },
            }
        ]
    ) as (server, _state):
        client = build_llm_client(_settings_for_test_server(server.server_port))
        request = LLMRequest(
            operation="semantic_ui_payload",
            messages=(
                {
                    "role": "system",
                    "content": "Return only grounded semantic UI JSON.",
                },
                {
                    "role": "user",
                    "content": "Build a semantic UI payload.",
                },
            ),
            metadata={
                "allowed_evidence_ids_json": json.dumps(["decision_object__summary"]),
                "grounded_numeric_values_json": json.dumps(
                    {
                        "probability": 0.84,
                        "novelty_score": 0.73,
                    }
                ),
            },
            output_format="json",
            max_output_tokens=400,
        )

        with pytest.raises(
            LLMResponseValidationError,
            match="did not match a grounded numeric field",
        ):
            client.generate_validated(request, CopilotResponse)


def test_openrouter_client_rejects_queue_block_drift_from_grounded_item() -> None:
    with _run_openrouter_test_server(
        [
            {
                "status": 200,
                "body": {
                    "id": "gen-test-009",
                    "object": "chat.completion",
                    "model": "demo-model",
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": json.dumps(
                                    {
                                        "job_id": "job_001",
                                        "sample_id": "sample_001",
                                        "target_drug": "tetracycline",
                                        "summary": "Grounded semantic UI summary for the case detail surface.",
                                        "next_steps": [
                                            "confirm phenotype in downstream review flow"
                                        ],
                                        "refusal_required": False,
                                        "refusal_reason": None,
                                        "cited_evidence_ids": ["decision_object__summary"],
                                        "answer_blocks": [
                                            {
                                                "block_id": "ui_summary",
                                                "block_type": "summary",
                                                "content": "Grounded semantic UI summary for the case detail surface.",
                                                "cited_evidence_ids": ["decision_object__summary"],
                                            }
                                        ],
                                        "semantic_ui": {
                                            "decision_card": {
                                                "title": "Decision Overview",
                                                "triage_decision": "defer_to_lab",
                                                "severity": "high",
                                                "summary": "High novelty and incomplete mechanism support keep this case in manual confirmation.",
                                                "metrics": [
                                                    {
                                                        "key": "novelty_score",
                                                        "label": "Novelty Score",
                                                        "value": 0.73,
                                                    }
                                                ],
                                            },
                                            "queue_block": {
                                                "title": "Analyst Queue",
                                                "items": [
                                                    {
                                                        "job_id": "job_001",
                                                        "sample_id": "sample_001",
                                                        "target_drug": "tetracycline",
                                                        "triage": "defer_to_lab",
                                                        "severity": "high",
                                                        "status": "completed",
                                                        "queue_priority": 7,
                                                        "headline": "Escalate this case immediately",
                                                        "rationale_codes": [
                                                            "high_lineage_novelty",
                                                            "manual_confirmation_required",
                                                        ],
                                                    }
                                                ],
                                            },
                                        },
                                        "warnings": [],
                                    }
                                ),
                            },
                        }
                    ],
                },
            }
        ]
    ) as (server, _state):
        client = build_llm_client(_settings_for_test_server(server.server_port))
        request = LLMRequest(
            operation="semantic_ui_payload",
            messages=(
                {
                    "role": "system",
                    "content": "Return only grounded semantic UI JSON.",
                },
                {
                    "role": "user",
                    "content": "Build a semantic UI payload.",
                },
            ),
            metadata={
                "allowed_evidence_ids_json": json.dumps(["decision_object__summary"]),
                "grounded_numeric_values_json": json.dumps(
                    {
                        "novelty_score": 0.73,
                    }
                ),
                "queue_item_json": json.dumps(
                    {
                        "job_id": "job_001",
                        "sample_id": "sample_001",
                        "target_drug": "tetracycline",
                        "triage": "defer_to_lab",
                        "severity": "high",
                        "status": "completed",
                        "queue_priority": 10,
                        "headline": "Defer to lab for high-novelty tetracycline case",
                        "rationale_codes": [
                            "no_supported_mechanism",
                            "high_lineage_novelty",
                            "manual_confirmation_required",
                        ],
                    }
                ),
            },
            output_format="json",
            max_output_tokens=400,
        )

        with pytest.raises(
            LLMResponseValidationError,
            match="queue_block drifted from the grounded queue item",
        ):
            client.generate_validated(request, CopilotResponse)


def test_openrouter_client_rejects_semantic_decision_card_drift() -> None:
    with _run_openrouter_test_server(
        [
            {
                "status": 200,
                "body": {
                    "id": "gen-test-009a",
                    "object": "chat.completion",
                    "model": "demo-model",
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": json.dumps(
                                    {
                                        "job_id": "job_001",
                                        "sample_id": "sample_001",
                                        "target_drug": "tetracycline",
                                        "summary": "Grounded semantic UI summary for the case detail surface.",
                                        "next_steps": [
                                            "confirm phenotype in downstream review flow"
                                        ],
                                        "refusal_required": False,
                                        "refusal_reason": None,
                                        "cited_evidence_ids": ["decision_object__summary"],
                                        "answer_blocks": [
                                            {
                                                "block_id": "ui_summary",
                                                "block_type": "summary",
                                                "content": "Grounded semantic UI summary for the case detail surface.",
                                                "cited_evidence_ids": ["decision_object__summary"],
                                            }
                                        ],
                                        "semantic_ui": {
                                            "decision_card": {
                                                "title": "Decision Overview",
                                                "triage_decision": "act",
                                                "severity": "critical",
                                                "summary": "High novelty and incomplete mechanism support keep this case in manual confirmation.",
                                                "metrics": [
                                                    {
                                                        "key": "novelty_score",
                                                        "label": "Novelty Score",
                                                        "value": 0.73,
                                                    }
                                                ],
                                            },
                                            "queue_block": {
                                                "title": "Analyst Queue",
                                                "items": [
                                                    {
                                                        "job_id": "job_001",
                                                        "sample_id": "sample_001",
                                                        "target_drug": "tetracycline",
                                                        "triage": "defer_to_lab",
                                                        "severity": "high",
                                                        "status": "completed",
                                                        "queue_priority": 10,
                                                        "headline": "Defer to lab for high-novelty tetracycline case",
                                                        "rationale_codes": [
                                                            "no_supported_mechanism",
                                                            "high_lineage_novelty",
                                                            "manual_confirmation_required",
                                                        ],
                                                    }
                                                ],
                                            },
                                        },
                                        "warnings": [],
                                    }
                                ),
                            },
                        }
                    ],
                },
            }
        ]
    ) as (server, _state):
        client = build_llm_client(_settings_for_test_server(server.server_port))
        request = LLMRequest(
            operation="semantic_ui_payload",
            messages=(
                {
                    "role": "system",
                    "content": "Return only grounded semantic UI JSON.",
                },
                {
                    "role": "user",
                    "content": "Build a semantic UI payload.",
                },
            ),
            metadata={
                "job_id": "job_001",
                "sample_id": "sample_001",
                "target_drug": "tetracycline",
                "allowed_evidence_ids_json": json.dumps(["decision_object__summary"]),
                "grounded_numeric_values_json": json.dumps(
                    {
                        "novelty_score": 0.73,
                    }
                ),
                "queue_item_json": json.dumps(
                    {
                        "job_id": "job_001",
                        "sample_id": "sample_001",
                        "target_drug": "tetracycline",
                        "triage": "defer_to_lab",
                        "severity": "high",
                        "status": "completed",
                        "queue_priority": 10,
                        "headline": "Defer to lab for high-novelty tetracycline case",
                        "rationale_codes": [
                            "no_supported_mechanism",
                            "high_lineage_novelty",
                            "manual_confirmation_required",
                        ],
                    }
                ),
            },
            output_format="json",
            max_output_tokens=400,
        )

        with pytest.raises(
            LLMResponseValidationError,
            match="decision_card drifted",
        ):
            client.generate_validated(request, CopilotResponse)


def test_openrouter_client_accepts_queue_block_without_hidden_timestamps() -> None:
    with _run_openrouter_test_server(
        [
            {
                "status": 200,
                "body": {
                    "id": "gen-test-009b",
                    "object": "chat.completion",
                    "model": "demo-model",
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": json.dumps(
                                    {
                                        "job_id": "job_001",
                                        "sample_id": "sample_001",
                                        "target_drug": "tetracycline",
                                        "summary": "Grounded semantic UI summary for the case detail surface.",
                                        "next_steps": [
                                            "confirm phenotype in downstream review flow"
                                        ],
                                        "refusal_required": False,
                                        "refusal_reason": None,
                                        "cited_evidence_ids": ["decision_object__summary"],
                                        "answer_blocks": [
                                            {
                                                "block_id": "ui_summary",
                                                "block_type": "summary",
                                                "content": "Grounded semantic UI summary for the case detail surface.",
                                                "cited_evidence_ids": ["decision_object__summary"],
                                            }
                                        ],
                                        "semantic_ui": {
                                            "decision_card": {
                                                "title": "Decision Overview",
                                                "triage_decision": "defer_to_lab",
                                                "severity": "high",
                                                "summary": "High novelty and incomplete mechanism support keep this case in manual confirmation.",
                                                "metrics": [
                                                    {
                                                        "key": "novelty_score",
                                                        "label": "Novelty Score",
                                                        "value": 0.73,
                                                    }
                                                ],
                                            },
                                            "queue_block": {
                                                "title": "Analyst Queue",
                                                "items": [
                                                    {
                                                        "job_id": "job_001",
                                                        "sample_id": "sample_001",
                                                        "target_drug": "tetracycline",
                                                        "triage": "defer_to_lab",
                                                        "severity": "high",
                                                        "status": "completed",
                                                        "queue_priority": 10,
                                                        "headline": "Defer to lab for high-novelty tetracycline case",
                                                        "rationale_codes": [
                                                            "no_supported_mechanism",
                                                            "high_lineage_novelty",
                                                            "manual_confirmation_required",
                                                        ],
                                                    }
                                                ],
                                            },
                                        },
                                        "warnings": [],
                                    }
                                ),
                            },
                        }
                    ],
                },
            }
        ]
    ) as (server, _state):
        client = build_llm_client(_settings_for_test_server(server.server_port))
        request = LLMRequest(
            operation="semantic_ui_payload",
            messages=(
                {
                    "role": "system",
                    "content": "Return only grounded semantic UI JSON.",
                },
                {
                    "role": "user",
                    "content": "Build a semantic UI payload.",
                },
            ),
            metadata={
                "allowed_evidence_ids_json": json.dumps(["decision_object__summary"]),
                "grounded_numeric_values_json": json.dumps(
                    {
                        "novelty_score": 0.73,
                    }
                ),
                "queue_item_json": json.dumps(
                    {
                        "schema_version": "0.1.0",
                        "created_at": "2026-04-23T10:00:00+00:00",
                        "job_id": "job_001",
                        "sample_id": "sample_001",
                        "target_drug": "tetracycline",
                        "triage": "defer_to_lab",
                        "severity": "high",
                        "status": "completed",
                        "queue_priority": 10,
                        "headline": "Defer to lab for high-novelty tetracycline case",
                        "rationale_codes": [
                            "no_supported_mechanism",
                            "high_lineage_novelty",
                            "manual_confirmation_required",
                        ],
                        "updated_at": "2026-04-23T10:05:00+00:00",
                    }
                ),
            },
            output_format="json",
            max_output_tokens=400,
        )

        validated = client.generate_validated(request, CopilotResponse)

    assert validated.parsed.semantic_ui is not None
    assert validated.parsed.semantic_ui.queue_block is not None
    queue_item = validated.parsed.semantic_ui.queue_block.items[0]
    assert queue_item.job_id == "job_001"
    assert queue_item.queue_priority == 10


def test_openrouter_client_normalizes_common_semantic_ui_schema_drift() -> None:
    drifted_payload = {
        "job_id": "job_001",
        "sample_id": "sample_001",
        "target_drug": "tetracycline",
        "summary": "Grounded analyst UI summary for a deferred tetracycline case.",
        "next_steps": ["confirm phenotype in downstream review flow"],
        "refusal_required": False,
        "refusal_reason": None,
        "cited_evidence_ids": ["decision_object__summary"],
        "answer_blocks": [
            {
                "block_id": "ui_summary",
                "block_type": "summary",
                "content": "Novelty is elevated and manual confirmation remains required.",
                "cited_evidence_ids": ["decision_object__summary"],
            }
        ],
        "semantic_ui": {
            "decision_card": {
                "title": "Decision Overview",
                "triage_decision": "defer_to_lab",
                "severity": "high",
                "summary": "High novelty and incomplete mechanism support keep this case in manual confirmation.",
                "metrics": [
                    {"key": "novelty_score", "label": "Novelty Score", "value": 0.73},
                    {"key": "qc_risk", "label": "QC Risk", "value": 0.25},
                ],
            },
            "evidence_table": {
                "title": "Evidence Summary",
                "columns": ["signal", "detail", "support"],
                "rows": [
                    {
                        "row_id": "mechanism_1",
                        "label": "Mechanism Evidence",
                        "signal": "tetA",
                        "detail": "supporting signal present",
                        "support": "supported",
                        "evidence_id": "mechanistic_evidence__1",
                    }
                ],
            },
        },
        "risk_charts": [
            {
                "chart_id": "risk_overview",
                "title": "Risk Overview",
                "chart_type": "bar",
                "points": [
                    {"label": "Novelty", "value": 0.73, "evidence_id": "decision_object__summary"},
                    {"label": "QC Risk", "value": 0.25, "evidence_id": "decision_object__summary"},
                ],
            },
            {
                "chart_id": "heatmap_like",
                "title": "Unsupported Heatmap",
                "chart_type": "heat_2x2",
                "points": [
                    {"x": 0.21, "y": 0.16, "label": "Actionability vs Uncertainty"},
                ],
            },
        ],
    }

    with _run_openrouter_test_server(
        [
            {
                "status": 200,
                "body": {
                    "id": "gen-test-005",
                    "object": "chat.completion",
                    "model": "demo-model",
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": json.dumps(drifted_payload),
                            },
                        }
                    ],
                },
            }
        ]
    ) as (server, _state):
        client = build_llm_client(_settings_for_test_server(server.server_port))
        request = LLMRequest(
            operation="semantic_ui_payload",
            messages=(
                {
                    "role": "system",
                    "content": "Return only grounded semantic UI JSON.",
                },
                {
                    "role": "user",
                    "content": "Build a semantic UI payload.",
                },
            ),
            metadata={
                "queue_item_json": json.dumps(
                    {
                        "job_id": "job_001",
                        "sample_id": "sample_001",
                        "target_drug": "tetracycline",
                        "triage": "defer_to_lab",
                        "severity": "high",
                        "status": "completed",
                        "queue_priority": 10,
                        "headline": "Defer to lab for high-novelty tetracycline case",
                        "rationale_codes": [
                            "no_supported_mechanism",
                            "high_lineage_novelty",
                            "manual_confirmation_required",
                        ],
                    }
                )
            },
            output_format="json",
            max_output_tokens=400,
        )

        validated = client.generate_validated(request, CopilotResponse)

    assert validated.parsed.semantic_ui is not None
    assert validated.parsed.semantic_ui.queue_block is not None
    assert validated.parsed.semantic_ui.evidence_table is not None
    assert validated.parsed.semantic_ui.evidence_table.rows[0].cells["signal"] == "tetA"
    assert len(validated.parsed.semantic_ui.risk_charts) == 1
    assert validated.parsed.semantic_ui.risk_charts[0].chart_type.value == "bar"


def test_openrouter_client_wraps_single_semantic_ui_risk_chart() -> None:
    payload = {
        "job_id": "job_001",
        "sample_id": "sample_001",
        "target_drug": "tetracycline",
        "summary": "Grounded analyst UI summary for a deferred tetracycline case.",
        "next_steps": ["confirm phenotype in downstream review flow"],
        "refusal_required": False,
        "refusal_reason": None,
        "cited_evidence_ids": ["decision_object__summary"],
        "answer_blocks": [
            {
                "block_id": "ui_summary",
                "block_type": "summary",
                "content": "Novelty is elevated and manual confirmation remains required.",
                "cited_evidence_ids": ["decision_object__summary"],
            }
        ],
        "semantic_ui": {
            "decision_card": {
                "title": "Decision Overview",
                "triage_decision": "defer_to_lab",
                "severity": "high",
                "summary": "High novelty keeps this case in manual confirmation.",
                "metrics": [
                    {"key": "novelty_score", "label": "Novelty Score", "value": 0.73},
                ],
            },
            "risk_charts": {
                "chart_id": "risk_overview",
                "title": "Risk Overview",
                "chart_type": "bar",
                "points": [
                    {
                        "label": "Novelty Score",
                        "value": 0.73,
                        "evidence_id": "novelty_assessment__summary",
                    }
                ],
            },
            "queue_block": {
                "title": "Analyst Queue",
                "items": [
                    {
                        "job_id": "job_001",
                        "sample_id": "sample_001",
                        "target_drug": "tetracycline",
                        "triage": "defer_to_lab",
                        "severity": "high",
                        "status": "completed",
                        "queue_priority": 10,
                        "headline": "Defer to lab for high-novelty tetracycline case",
                        "rationale_codes": ["high_lineage_novelty"],
                    }
                ],
            },
        },
        "warnings": [],
    }

    with _run_openrouter_test_server(
        [
            {
                "status": 200,
                "body": {
                    "id": "gen-test-014",
                    "object": "chat.completion",
                    "model": "demo-model",
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": json.dumps(payload),
                            },
                        }
                    ],
                },
            }
        ]
    ) as (server, _state):
        client = build_llm_client(_settings_for_test_server(server.server_port))
        request = LLMRequest(
            operation="semantic_ui_payload",
            messages=(
                {
                    "role": "system",
                    "content": "Return only grounded semantic UI JSON.",
                },
                {
                    "role": "user",
                    "content": "Build a semantic UI payload.",
                },
            ),
            metadata={
                "allowed_evidence_ids_json": json.dumps(
                    ["decision_object__summary", "novelty_assessment__summary"]
                ),
                "grounded_numeric_values_json": json.dumps({"novelty_score": 0.73}),
                "queue_item_json": json.dumps(
                    {
                        "job_id": "job_001",
                        "sample_id": "sample_001",
                        "target_drug": "tetracycline",
                        "triage": "defer_to_lab",
                        "severity": "high",
                        "status": "completed",
                        "queue_priority": 10,
                        "headline": "Defer to lab for high-novelty tetracycline case",
                        "rationale_codes": ["high_lineage_novelty"],
                    }
                ),
            },
            output_format="json",
            max_output_tokens=400,
        )

        validated = client.generate_validated(request, CopilotResponse)

    assert validated.parsed.semantic_ui is not None
    assert len(validated.parsed.semantic_ui.risk_charts) == 1
    assert validated.parsed.semantic_ui.risk_charts[0].chart_id == "risk_overview"


@pytest.mark.live
def test_live_openrouter_gemma_smoke_returns_schema_valid_json() -> None:
    get_llm_client.cache_clear()
    settings = load_settings()
    assert settings.llm.provider == "openrouter", (
        "LLM_PROVIDER must be set to openrouter for live LLM smoke tests"
    )
    assert settings.llm.base_url, "LLM_BASE_URL must be configured in the local environment"
    assert settings.llm.api_key, "LLM_API_KEY must be configured in the local environment"
    assert settings.llm.model, "LLM_MODEL must be configured in the local environment"

    client = build_llm_client(settings)
    request = LLMRequest(
        operation="phase7_live_openrouter_smoke",
        messages=(
            {
                "role": "system",
                "content": (
                    "Return only a JSON object with keys status and summary. "
                    "status must be exactly 'ok'. summary must be one short sentence."
                ),
            },
            {
                "role": "user",
                "content": "Acknowledge that the live Phase 7 LLM adapter smoke test is working.",
            },
        ),
        output_format="json",
        max_output_tokens=200,
        timeout_seconds=settings.llm.timeout_seconds,
        retry_count=settings.llm.retry_count,
        reasoning_enabled=False,
    )

    validated = client.generate_validated(request, _SmokeEnvelope)

    assert validated.parsed.status == "ok"
    assert validated.response.model
    assert validated.response.output_text
