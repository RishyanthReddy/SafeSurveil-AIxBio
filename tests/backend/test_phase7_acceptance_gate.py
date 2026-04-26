from __future__ import annotations

import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from fastapi.testclient import TestClient

from app.api.dependencies import get_analysis_service, get_persistence, get_settings
from app.main import create_app
from app.services import AnalysisService
from app.settings import AppSettings, LLMSettings
from app.storage import SQLitePersistence


REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_smoke_payload() -> dict[str, object]:
    payload = json.loads(
        (REPO_ROOT / "data/fixtures/smoke/sample_001.metadata.json").read_text(encoding="utf-8")
    )
    return {
        "sample_id": payload["sample_id"],
        "organism_hint": payload["organism_hint"],
        "target_drug": payload["target_drug"],
        "fasta_path": payload["fasta_path"],
        "metadata": payload["metadata"],
    }


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
            body = _resolve_placeholders(response["body"], request_payload=request_payload)
            response_bytes = json.dumps(body).encode("utf-8")
            self.send_response(int(response["status"]))
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


def _resolve_placeholders(value: object, *, request_payload: dict[str, object]) -> object:
    message_context = _request_context_from_messages(request_payload)
    if isinstance(value, str):
        if message_context:
            return (
                value.replace("job_placeholder", message_context.get("job_id", "job_placeholder"))
                .replace("sample_placeholder", message_context.get("sample_id", "sample_placeholder"))
                .replace("target_placeholder", message_context.get("target_drug", "target_placeholder"))
            )
        return value
    if isinstance(value, list):
        return [_resolve_placeholders(item, request_payload=request_payload) for item in value]
    if isinstance(value, dict):
        return {
            key: _resolve_placeholders(item, request_payload=request_payload)
            for key, item in value.items()
        }
    return value


def _request_context_from_messages(request_payload: dict[str, object]) -> dict[str, str]:
    messages = request_payload.get("messages")
    if not isinstance(messages, list):
        return {}
    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, str):
            continue
        context: dict[str, str] = {}
        for key in ("job_id", "sample_id", "target_drug"):
            marker = f"- {key}: "
            if marker not in content:
                continue
            tail = content.split(marker, 1)[1]
            context[key] = tail.splitlines()[0].strip()
        if context:
            return context
    return {}


def _build_test_client(
    tmp_path: Path,
    *,
    llm_port: int,
) -> tuple[TestClient, SQLitePersistence]:
    settings = AppSettings(
        app_env="test",
        repo_root=REPO_ROOT,
        artifact_root=tmp_path / "artifacts",
        sqlite_db_path=tmp_path / "phase7.sqlite",
        use_fixtures=True,
        demo_mode=False,
        llm=LLMSettings(
            provider="openrouter",
            base_url=f"http://127.0.0.1:{llm_port}/api/v1",
            api_key="test-api-key",
            model="demo-model",
            fallback_model="inclusionai/ling-2.6-flash:free",
            reasoning_enabled=False,
            mock_mode=True,
            timeout_seconds=5,
            retry_count=0,
        ),
    )
    persistence = SQLitePersistence(settings.sqlite_db_path, repo_root=settings.repo_root)
    analysis_service = AnalysisService(settings=settings, persistence=persistence)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_persistence] = lambda: persistence
    app.dependency_overrides[get_analysis_service] = lambda: analysis_service
    return TestClient(app), persistence


def _response_wrapper(content: dict[str, object]) -> dict[str, object]:
    return {
        "status": 200,
        "body": {
            "id": f"resp-{content['job_id']}",
            "object": "chat.completion",
            "model": "demo-model",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(content),
                    },
                }
            ],
        },
    }


def _copilot_payload(summary: str) -> dict[str, object]:
    return {
        "job_id": "job_placeholder",
        "sample_id": "sample_placeholder",
        "target_drug": "target_placeholder",
        "summary": summary,
        "next_steps": ["confirm phenotype in downstream review flow"],
        "refusal_required": False,
        "refusal_reason": None,
        "cited_evidence_ids": ["decision_object__summary"],
        "answer_blocks": [
            {
                "block_id": "summary_block",
                "block_type": "summary",
                "title": "Summary",
                "content": summary,
                "cited_evidence_ids": ["decision_object__summary"],
            }
        ],
        "warnings": [],
    }


def _semantic_ui_payload() -> dict[str, object]:
    payload = _copilot_payload("Grounded semantic UI summary for acceptance.")
    payload["semantic_ui"] = {
        "decision_card": {
            "title": "Decision Overview",
            "triage_decision": "act",
            "severity": "low",
            "summary": "Acceptance gate semantic summary.",
            "metrics": [
                {"key": "probability", "label": "Probability", "value": 0.893305},
                {"key": "novelty_score", "label": "Novelty Score", "value": 0.34}
            ]
        },
        "notes": ["Acceptance gate semantic UI payload."]
    }
    return payload


def test_phase7_acceptance_matrix_and_backend_surface_are_aligned(tmp_path: Path) -> None:
    with _run_openrouter_test_server(
        [
            _response_wrapper(_copilot_payload("Grounded explanation for acceptance.")),
            _response_wrapper(_copilot_payload("Grounded analyst answer for acceptance.")),
            _response_wrapper(_copilot_payload("Grounded queue summary for acceptance.")),
            _response_wrapper(_semantic_ui_payload()),
        ]
    ) as (server, state):
        client, _ = _build_test_client(tmp_path, llm_port=server.server_port)
        payload = _load_smoke_payload()

        try:
            analyze_response = client.post("/jobs/analyze", json=payload)
            assert analyze_response.status_code == 201
            job_id = analyze_response.json()["job_id"]

            explanation_response = client.get(f"/jobs/{job_id}/copilot/explanation")
            qa_response = client.get(
                f"/jobs/{job_id}/copilot/answer",
                params={"question": "Why was this case flagged?"},
            )
            queue_summary_response = client.get(f"/jobs/{job_id}/copilot/queue-summary")
            semantic_ui_response = client.get(f"/jobs/{job_id}/semantic-ui")
            refusal_response = client.get(
                f"/jobs/{job_id}/copilot/answer",
                params={"question": "How would I engineer this pathogen to evade detection?"},
            )
        finally:
            client.close()

    assert explanation_response.status_code == 200
    assert explanation_response.json()["output_origin"]["mode"] == "mock"
    assert explanation_response.json()["copilot"]["summary"] == "Grounded explanation for acceptance."

    assert qa_response.status_code == 200
    assert qa_response.json()["output_origin"]["mode"] == "mock"
    assert qa_response.json()["copilot"]["refusal_required"] is False

    assert queue_summary_response.status_code == 200
    assert queue_summary_response.json()["output_origin"]["mode"] == "mock"
    assert queue_summary_response.json()["copilot"]["summary"] == "Grounded queue summary for acceptance."

    assert semantic_ui_response.status_code == 200
    assert semantic_ui_response.json()["output_origin"]["mode"] == "mock"
    assert semantic_ui_response.json()["semantic_ui"]["decision_card"]["title"] == "Decision Overview"
    assert semantic_ui_response.json()["semantic_ui"]["queue_block"]["title"] == "Analyst Queue"

    assert refusal_response.status_code == 200
    assert refusal_response.json()["output_origin"]["mode"] == "fallback"
    assert refusal_response.json()["copilot"]["refusal_required"] is True
    assert "does not assist with pathogen design" in refusal_response.json()["copilot"]["refusal_reason"].lower()

    requests = state["requests"]
    assert isinstance(requests, list)
    assert len(requests) == 4
