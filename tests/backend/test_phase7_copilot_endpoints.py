from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from fastapi.testclient import TestClient

from app.api.dependencies import get_analysis_service, get_copilot_service, get_persistence, get_settings
from app.contracts import CopilotResponse, JobState
from app.llm import LLMClientResponse, LLMResponseValidationError, ValidatedLLMResponse
from app.main import create_app
from app.services import AnalysisService, CopilotService
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
            status = int(response["status"])
            body = _resolve_placeholders(response["body"], request_payload=request_payload)
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
    llm_port: int | None = None,
    llm_settings: LLMSettings | None = None,
) -> tuple[TestClient, SQLitePersistence]:
    resolved_llm_settings = llm_settings or LLMSettings(
        provider="openrouter",
        base_url=f"http://127.0.0.1:{llm_port}/api/v1",
        api_key="test-api-key",
        model="demo-model",
        fallback_model="inclusionai/ling-2.6-flash:free",
        reasoning_enabled=False,
        mock_mode=True,
        timeout_seconds=5,
        retry_count=0,
    )
    settings = AppSettings(
        app_env="test",
        repo_root=REPO_ROOT,
        artifact_root=tmp_path / "artifacts",
        sqlite_db_path=tmp_path / "phase7.sqlite",
        use_fixtures=True,
        demo_mode=False,
        llm=resolved_llm_settings,
    )
    persistence = SQLitePersistence(settings.sqlite_db_path, repo_root=settings.repo_root)
    analysis_service = AnalysisService(settings=settings, persistence=persistence)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_persistence] = lambda: persistence
    app.dependency_overrides[get_analysis_service] = lambda: analysis_service
    return TestClient(app), persistence


def _write_phase6b_acceptance_report(
    tmp_path: Path,
    *,
    gate_status: str,
    can_begin: bool,
    created_at: datetime | None = None,
) -> None:
    report_path = (
        tmp_path
        / "artifacts"
        / "runs"
        / "phase6b_acceptance"
        / "latest"
        / "phase6b_acceptance_report.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "matrix_version": "0.1.0",
        "created_at": (created_at or datetime.now(UTC)).isoformat(),
        "phase7_gate": {
            "status": gate_status,
            "can_begin": can_begin,
            "blocking_areas": [] if can_begin else ["persistence"],
            "summary": "Synthetic test gate status.",
        },
    }
    report_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _response_wrapper(content: dict[str, object], *, model: str = "demo-model") -> dict[str, object]:
    return {
        "status": 200,
        "body": {
            "id": f"resp-{content['job_id']}",
            "object": "chat.completion",
            "model": model,
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
        "sample_id": "sample_001",
        "target_drug": "tetracycline",
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
            },
            {
                "block_id": "next_steps_block",
                "block_type": "next_steps",
                "title": "Next Steps",
                "content": "confirm phenotype in downstream review flow",
                "cited_evidence_ids": ["decision_object__triage"],
            },
        ],
        "warnings": [],
    }


def _semantic_ui_payload() -> dict[str, object]:
    payload = _copilot_payload("Grounded semantic UI summary for the case detail surface.")
    payload["answer_blocks"] = [
        {
            "block_id": "ui_summary",
            "block_type": "summary",
            "title": "Case Summary",
            "content": "Grounded semantic UI summary for the case detail surface.",
            "cited_evidence_ids": ["decision_object__summary"],
        },
        {
            "block_id": "ui_next_steps",
            "block_type": "next_steps",
            "title": "Recorded Next Step",
            "content": "confirm phenotype in downstream review flow",
            "cited_evidence_ids": ["decision_object__triage"],
        },
    ]
    payload["semantic_ui"] = {
        "decision_card": {
            "title": "Decision Overview",
            "triage_decision": "act",
            "severity": "low",
            "summary": "The case remains actionable with strong supporting signals.",
            "metrics": [
                {"key": "probability", "label": "Probability", "value": 0.893305},
                {"key": "novelty_score", "label": "Novelty Score", "value": 0.34},
            ],
        },
        "evidence_table": {
            "title": "Mechanistic Evidence",
            "columns": ["signal", "support"],
            "rows": [
                {
                    "row_id": "row_1",
                    "label": "tetA",
                    "cells": {"signal": "tetA", "support": "supported"},
                    "evidence_id": "mechanistic_evidence__1",
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
                        "label": "Novelty",
                        "value": 0.34,
                        "evidence_id": "novelty_assessment__summary",
                    },
                    {
                        "label": "QC Risk",
                        "value": 0.0,
                        "evidence_id": "decision_object__assembly_qc",
                    },
                ],
            }
        ],
        "notes": ["Renderer should prioritize the decision card and queue block."],
    }
    return payload


def _semantic_ui_refusal_payload(reason: str) -> dict[str, object]:
    return {
        "job_id": "job_placeholder",
        "sample_id": "sample_001",
        "target_drug": "tetracycline",
        "summary": None,
        "next_steps": [],
        "refusal_required": True,
        "refusal_reason": reason,
        "cited_evidence_ids": [],
        "answer_blocks": [
            {
                "block_id": "ui_refusal",
                "block_type": "refusal",
                "title": "Refusal",
                "content": reason,
                "cited_evidence_ids": [],
            }
        ],
        "semantic_ui": None,
        "warnings": [],
    }


def test_job_copilot_and_semantic_ui_endpoints_return_grounded_outputs(tmp_path: Path) -> None:
    with _run_openrouter_test_server(
        [
            _response_wrapper(_copilot_payload("Grounded decision explanation for the saved case.")),
            _response_wrapper(_copilot_payload("Grounded analyst answer for the saved case.")),
            _response_wrapper(_copilot_payload("Grounded queue handoff for the saved case.")),
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
            cached_explanation_response = client.get(f"/jobs/{job_id}/copilot/explanation")
            analyst_qa_response = client.get(
                f"/jobs/{job_id}/copilot/answer",
                params={"question": "Why was this case flagged?"},
            )
            queue_summary_response = client.get(f"/jobs/{job_id}/copilot/queue-summary")
            cached_queue_summary_response = client.get(f"/jobs/{job_id}/copilot/queue-summary")
            semantic_ui_response = client.get(f"/jobs/{job_id}/semantic-ui")
            cached_semantic_ui_response = client.get(f"/jobs/{job_id}/semantic-ui")
            artifact_response = client.get(f"/jobs/{job_id}/artifacts")
        finally:
            client.close()

    assert explanation_response.status_code == 200
    explanation_payload = explanation_response.json()
    assert explanation_payload["job_status"]["job_id"] == job_id
    assert explanation_payload["output_origin"]["mode"] == "mock"
    assert explanation_payload["copilot"]["summary"] == "Grounded decision explanation for the saved case."
    assert explanation_payload["copilot"]["answer_blocks"][1]["block_id"] == "evidence_limitations"
    assert explanation_payload["copilot"]["warnings"][0].startswith(
        "Grounding status: not live-grounded."
    )
    assert "Evidence policy: The saved job context is degraded or fixture-backed." in explanation_payload[
        "copilot"
    ]["warnings"]
    assert explanation_payload["copilot"]["answer_blocks"][0]["block_id"] == "grounding_status"
    assert cached_explanation_response.status_code == 200
    assert cached_explanation_response.json()["output_origin"]["mode"] == "cached"
    assert cached_explanation_response.json()["copilot"]["summary"] == (
        "Grounded decision explanation for the saved case."
    )

    assert analyst_qa_response.status_code == 200
    assert analyst_qa_response.json()["output_origin"]["mode"] == "mock"
    assert analyst_qa_response.json()["copilot"]["summary"] == "Grounded analyst answer for the saved case."

    assert queue_summary_response.status_code == 200
    assert queue_summary_response.json()["output_origin"]["mode"] == "mock"
    assert queue_summary_response.json()["copilot"]["summary"] == "Grounded queue handoff for the saved case."
    assert cached_queue_summary_response.status_code == 200
    assert cached_queue_summary_response.json()["output_origin"]["mode"] == "cached"
    assert cached_queue_summary_response.json()["copilot"]["summary"] == (
        "Grounded queue handoff for the saved case."
    )

    assert semantic_ui_response.status_code == 200
    semantic_payload = semantic_ui_response.json()
    assert semantic_payload["job_id"] == job_id
    assert semantic_payload["output_origin"]["mode"] == "mock"
    assert semantic_payload["semantic_ui"]["decision_card"]["title"] == "Decision Overview"
    assert semantic_payload["semantic_ui"]["queue_block"]["title"] == "Analyst Queue"
    assert semantic_payload["semantic_ui"]["notes"][0].startswith(
        "Grounding status: not live-grounded."
    )
    assert cached_semantic_ui_response.status_code == 200
    assert cached_semantic_ui_response.json()["output_origin"]["mode"] == "cached"
    assert cached_semantic_ui_response.json()["semantic_ui"]["decision_card"]["title"] == "Decision Overview"

    assert artifact_response.status_code == 200
    artifact_ids = {item["artifact_id"] for item in artifact_response.json()["artifacts"]}
    assert f"{job_id}_copilot_explanation_json" in artifact_ids
    assert f"{job_id}_copilot_queue_summary_json" in artifact_ids
    assert f"{job_id}_semantic_ui_json" in artifact_ids

    requests = state["requests"]
    assert isinstance(requests, list)
    assert len(requests) == 4
    assert requests[0]["path"] == "/api/v1/chat/completions"
    assert "decision explanation" in requests[0]["body"]["messages"][1]["content"].lower()
    assert "analyst question" in requests[1]["body"]["messages"][1]["content"].lower()
    assert "queue metadata" in requests[2]["body"]["messages"][1]["content"].lower()
    assert "semantic ui" in requests[3]["body"]["messages"][1]["content"].lower()


def test_localhost_llm_relay_can_still_report_live_origin(tmp_path: Path) -> None:
    with _run_openrouter_test_server(
        [_response_wrapper(_copilot_payload("Grounded decision explanation for the saved case."))]
    ) as (server, _state):
        client, _ = _build_test_client(
            tmp_path,
            llm_settings=LLMSettings(
                provider="openrouter",
                base_url=f"http://127.0.0.1:{server.server_port}/api/v1",
                api_key="test-api-key",
                model="demo-model",
                fallback_model="inclusionai/ling-2.6-flash:free",
                reasoning_enabled=False,
                mock_mode=False,
                timeout_seconds=5,
                retry_count=0,
            ),
        )
        payload = _load_smoke_payload()

        try:
            analyze_response = client.post("/jobs/analyze", json=payload)
            assert analyze_response.status_code == 201
            job_id = analyze_response.json()["job_id"]
            explanation_response = client.get(f"/jobs/{job_id}/copilot/explanation")
        finally:
            client.close()

    assert explanation_response.status_code == 200
    assert explanation_response.json()["output_origin"]["mode"] == "live_llm"


def test_refresh_query_bypasses_cached_explanation(tmp_path: Path) -> None:
    with _run_openrouter_test_server(
        [
            _response_wrapper(_copilot_payload("First live explanation.")),
            _response_wrapper(_copilot_payload("Refreshed live explanation.")),
        ]
    ) as (server, state):
        client, _ = _build_test_client(tmp_path, llm_port=server.server_port)
        payload = _load_smoke_payload()

        try:
            analyze_response = client.post("/jobs/analyze", json=payload)
            assert analyze_response.status_code == 201
            job_id = analyze_response.json()["job_id"]

            first_response = client.get(f"/jobs/{job_id}/copilot/explanation")
            cached_response = client.get(f"/jobs/{job_id}/copilot/explanation")
            refreshed_response = client.get(
                f"/jobs/{job_id}/copilot/explanation",
                params={"refresh": "true"},
            )
            refreshed_cached_response = client.get(f"/jobs/{job_id}/copilot/explanation")
        finally:
            client.close()

    assert first_response.status_code == 200
    assert first_response.json()["output_origin"]["mode"] == "mock"
    assert first_response.json()["copilot"]["summary"] == "First live explanation."

    assert cached_response.status_code == 200
    assert cached_response.json()["output_origin"]["mode"] == "cached"
    assert cached_response.json()["copilot"]["summary"] == "First live explanation."

    assert refreshed_response.status_code == 200
    assert refreshed_response.json()["output_origin"]["mode"] == "mock"
    assert refreshed_response.json()["copilot"]["summary"] == "Refreshed live explanation."

    assert refreshed_cached_response.status_code == 200
    assert refreshed_cached_response.json()["output_origin"]["mode"] == "cached"
    assert refreshed_cached_response.json()["copilot"]["summary"] == "Refreshed live explanation."

    requests = state["requests"]
    assert isinstance(requests, list)
    assert len(requests) == 2


def test_mismatched_copilot_identity_is_not_cached(tmp_path: Path) -> None:
    mismatched_payload = _copilot_payload("Grounded decision explanation for the saved case.")
    mismatched_payload["job_id"] = "job_other"
    with _run_openrouter_test_server([_response_wrapper(mismatched_payload)]) as (server, _state):
        client, _ = _build_test_client(tmp_path, llm_port=server.server_port)
        payload = _load_smoke_payload()

        try:
            analyze_response = client.post("/jobs/analyze", json=payload)
            assert analyze_response.status_code == 201
            job_id = analyze_response.json()["job_id"]
            explanation_response = client.get(f"/jobs/{job_id}/copilot/explanation")
            artifact_response = client.get(f"/jobs/{job_id}/artifacts")
        finally:
            client.close()

    assert explanation_response.status_code == 502
    assert "identity drifted" in explanation_response.json()["detail"]
    artifact_ids = {item["artifact_id"] for item in artifact_response.json()["artifacts"]}
    assert f"{job_id}_copilot_explanation_json" not in artifact_ids


def test_semantic_ui_refusal_returns_controlled_fallback_surface(tmp_path: Path) -> None:
    refusal_reason = (
        "Grounded semantic UI is unavailable because artifact provenance is missing from the saved job."
    )
    with _run_openrouter_test_server(
        [_response_wrapper(_semantic_ui_refusal_payload(refusal_reason))]
    ) as (server, _state):
        client, _ = _build_test_client(tmp_path, llm_port=server.server_port)
        payload = _load_smoke_payload()

        try:
            analyze_response = client.post("/jobs/analyze", json=payload)
            assert analyze_response.status_code == 201
            job_id = analyze_response.json()["job_id"]

            semantic_ui_response = client.get(f"/jobs/{job_id}/semantic-ui")
            artifact_response = client.get(f"/jobs/{job_id}/artifacts")
        finally:
            client.close()

    assert semantic_ui_response.status_code == 200
    semantic_payload = semantic_ui_response.json()
    assert semantic_payload["output_origin"]["mode"] == "fallback"
    assert semantic_payload["semantic_ui"]["decision_card"]["title"] == (
        "Grounded Semantic UI Unavailable"
    )
    assert semantic_payload["semantic_ui"]["queue_block"]["title"] == "Analyst Queue"
    assert semantic_payload["semantic_ui"]["queue_block"]["items"][0]["job_id"] == job_id
    assert refusal_reason in semantic_payload["semantic_ui"]["notes"]
    assert any(
        note.startswith("Semantic UI fallback:")
        for note in semantic_payload["semantic_ui"]["notes"]
    )
    artifact_ids = {item["artifact_id"] for item in artifact_response.json()["artifacts"]}
    assert f"{job_id}_semantic_ui_json" not in artifact_ids


def test_new_copilot_job_endpoints_return_not_found_for_missing_job(tmp_path: Path) -> None:
    with _run_openrouter_test_server([]) as (server, _state):
        client, _ = _build_test_client(tmp_path, llm_port=server.server_port)
        try:
            explanation_response = client.get("/jobs/job_missing_001/copilot/explanation")
            semantic_ui_response = client.get("/jobs/job_missing_001/semantic-ui")
        finally:
            client.close()

    assert explanation_response.status_code == 404
    assert explanation_response.json()["detail"] == "Job not found."
    assert semantic_ui_response.status_code == 404
    assert semantic_ui_response.json()["detail"] == "Job not found."


def test_cached_semantic_ui_relabels_when_phase7_gate_becomes_ready(tmp_path: Path) -> None:
    with _run_openrouter_test_server([_response_wrapper(_semantic_ui_payload())]) as (server, state):
        client, persistence = _build_test_client(tmp_path, llm_port=server.server_port)
        payload = _load_smoke_payload()

        try:
            analyze_response = client.post("/jobs/analyze", json=payload)
            assert analyze_response.status_code == 201
            job_id = analyze_response.json()["job_id"]

            first_response = client.get(f"/jobs/{job_id}/semantic-ui")
            assert first_response.status_code == 200
            assert first_response.json()["semantic_ui"]["notes"][0].startswith(
                "Grounding status: not live-grounded."
            )

            job_status = persistence.get_job_status(job_id)
            assert job_status is not None
            persistence.update_job_status(job_status.model_copy(update={"status": JobState.COMPLETED}))
            queue_item = persistence.get_queue_item(job_id)
            assert queue_item is not None
            persistence.save_queue_item(
                queue_item.model_copy(
                    update={
                        "status": JobState.COMPLETED,
                        "queue_priority": 3,
                        "headline": "Completed live-grounded queue item",
                    }
                )
            )
            _write_phase6b_acceptance_report(tmp_path, gate_status="ready", can_begin=True)

            second_response = client.get(f"/jobs/{job_id}/semantic-ui")
        finally:
            client.close()

    assert second_response.status_code == 200
    assert second_response.json()["output_origin"]["mode"] == "cached"
    assert second_response.json()["semantic_ui"]["notes"][0] == (
        "Grounding status: live-grounded. The current Phase 6B gate is ready and the persisted job status is completed."
    )
    refreshed_queue_item = second_response.json()["semantic_ui"]["queue_block"]["items"][0]
    assert refreshed_queue_item["status"] == "completed"
    assert refreshed_queue_item["queue_priority"] == 3
    assert refreshed_queue_item["headline"] == "Completed live-grounded queue item"
    requests = state["requests"]
    assert isinstance(requests, list)
    assert len(requests) == 1


def test_copilot_explanation_labels_degraded_when_gate_ready_but_job_degraded(tmp_path: Path) -> None:
    _write_phase6b_acceptance_report(tmp_path, gate_status="ready", can_begin=True)
    with _run_openrouter_test_server(
        [_response_wrapper(_copilot_payload("Grounded decision explanation for the saved case."))]
    ) as (server, _state):
        client, _ = _build_test_client(tmp_path, llm_port=server.server_port)
        payload = _load_smoke_payload()

        try:
            analyze_response = client.post("/jobs/analyze", json=payload)
            assert analyze_response.status_code == 201
            job_id = analyze_response.json()["job_id"]
            explanation_response = client.get(f"/jobs/{job_id}/copilot/explanation")
        finally:
            client.close()

    assert explanation_response.status_code == 200
    assert explanation_response.json()["output_origin"]["mode"] == "mock"
    assert explanation_response.json()["copilot"]["warnings"][0] == (
        "Grounding status: degraded. The persisted job status is degraded, so this output must not be described as fully live-grounded."
    )


def test_analyst_qa_refuses_without_mechanistic_slice_and_skips_llm(tmp_path: Path) -> None:
    with _run_openrouter_test_server([]) as (server, state):
        client, persistence = _build_test_client(tmp_path, llm_port=server.server_port)
        payload = _load_smoke_payload()

        try:
            analyze_response = client.post("/jobs/analyze", json=payload)
            assert analyze_response.status_code == 201
            job_id = analyze_response.json()["job_id"]
            with persistence.connect() as connection:
                connection.execute("DELETE FROM mechanistic_evidence WHERE job_id = ?", (job_id,))

            qa_response = client.get(
                f"/jobs/{job_id}/copilot/answer",
                params={"question": "Which mechanism supports this call?"},
            )
        finally:
            client.close()

    assert qa_response.status_code == 200
    qa_payload = qa_response.json()["copilot"]
    assert qa_response.json()["output_origin"]["mode"] == "fallback"
    assert qa_payload["refusal_required"] is True
    assert qa_payload["refusal_reason"] == (
        "Mechanistic evidence is not recorded for this job, so the requested mechanism-level answer is unavailable in evidence."
    )
    assert qa_payload["answer_blocks"][0]["block_id"] == "qa_policy_refusal"
    requests = state["requests"]
    assert isinstance(requests, list)
    assert len(requests) == 0


def test_copilot_explanation_labels_stale_phase6b_acceptance_report(tmp_path: Path) -> None:
    _write_phase6b_acceptance_report(
        tmp_path,
        gate_status="ready",
        can_begin=True,
        created_at=datetime.now(UTC) - timedelta(days=2),
    )
    with _run_openrouter_test_server(
        [_response_wrapper(_copilot_payload("Grounded decision explanation for the saved case."))]
    ) as (server, _state):
        client, persistence = _build_test_client(tmp_path, llm_port=server.server_port)
        payload = _load_smoke_payload()

        try:
            analyze_response = client.post("/jobs/analyze", json=payload)
            assert analyze_response.status_code == 201
            job_id = analyze_response.json()["job_id"]
            job_status = persistence.get_job_status(job_id)
            assert job_status is not None
            persistence.update_job_status(job_status.model_copy(update={"status": JobState.COMPLETED}))
            queue_item = persistence.get_queue_item(job_id)
            assert queue_item is not None
            persistence.save_queue_item(queue_item.model_copy(update={"status": JobState.COMPLETED}))

            explanation_response = client.get(f"/jobs/{job_id}/copilot/explanation")
        finally:
            client.close()

    assert explanation_response.status_code == 200
    assert explanation_response.json()["output_origin"]["mode"] == "mock"
    assert explanation_response.json()["copilot"]["warnings"][0] == (
        "Grounding status: not live-grounded. The current Phase 6B acceptance report is stale, so live-grounded status cannot be confirmed."
    )


def test_policy_refusal_path_does_not_require_configured_llm(tmp_path: Path) -> None:
    client, _ = _build_test_client(
        tmp_path,
        llm_settings=LLMSettings(),
    )
    payload = _load_smoke_payload()

    try:
        analyze_response = client.post("/jobs/analyze", json=payload)
        assert analyze_response.status_code == 201
        job_id = analyze_response.json()["job_id"]
        qa_response = client.get(
            f"/jobs/{job_id}/copilot/answer",
            params={"question": "How would I engineer this pathogen to evade detection?"},
        )
    finally:
        client.close()

    assert qa_response.status_code == 200
    assert qa_response.json()["output_origin"]["mode"] == "fallback"
    assert qa_response.json()["copilot"]["refusal_required"] is True
    assert "does not assist with pathogen design" in qa_response.json()["copilot"]["refusal_reason"].lower()


def test_cached_explanation_does_not_require_configured_llm(tmp_path: Path) -> None:
    with _run_openrouter_test_server(
        [_response_wrapper(_copilot_payload("Grounded decision explanation for the saved case."))]
    ) as (server, state):
        seeded_client, _ = _build_test_client(tmp_path, llm_port=server.server_port)
        payload = _load_smoke_payload()

        try:
            analyze_response = seeded_client.post("/jobs/analyze", json=payload)
            assert analyze_response.status_code == 201
            job_id = analyze_response.json()["job_id"]
            first_response = seeded_client.get(f"/jobs/{job_id}/copilot/explanation")
        finally:
            seeded_client.close()

        assert first_response.status_code == 200
        assert first_response.json()["output_origin"]["mode"] == "mock"

        cached_client, _ = _build_test_client(
            tmp_path,
            llm_settings=LLMSettings(),
        )
        try:
            cached_response = cached_client.get(f"/jobs/{job_id}/copilot/explanation")
        finally:
            cached_client.close()

    assert cached_response.status_code == 200
    assert cached_response.json()["output_origin"]["mode"] == "cached"
    assert cached_response.json()["copilot"]["summary"] == (
        "Grounded decision explanation for the saved case."
    )
    requests = state["requests"]
    assert isinstance(requests, list)
    assert len(requests) == 1


def test_copilot_service_applies_runtime_timeout_and_retry_settings(tmp_path: Path) -> None:
    class _CapturingLLMClient:
        def __init__(self) -> None:
            self.requests: list[object] = []

        def generate_validated(self, request, response_model):
            self.requests.append(request)
            payload = {
                "job_id": request.metadata["job_id"],
                "sample_id": request.metadata["sample_id"],
                "target_drug": request.metadata["target_drug"],
                "summary": "Grounded decision explanation for the saved case.",
                "next_steps": ["confirm phenotype in downstream review flow"],
                "refusal_required": False,
                "refusal_reason": None,
                "cited_evidence_ids": ["decision_object__summary"],
                "answer_blocks": [
                    {
                        "block_id": "summary_block",
                        "block_type": "summary",
                        "title": "Summary",
                        "content": "Grounded decision explanation for the saved case.",
                        "cited_evidence_ids": ["decision_object__summary"],
                    }
                ],
                "warnings": [],
            }
            parsed = response_model.model_validate(payload)
            return ValidatedLLMResponse(
                response=LLMClientResponse(
                    provider="openrouter",
                    model="demo-model",
                    output_json=payload,
                ),
                parsed=parsed,
            )

    settings = AppSettings(
        app_env="test",
        repo_root=REPO_ROOT,
        artifact_root=tmp_path / "artifacts",
        sqlite_db_path=tmp_path / "phase7.sqlite",
        use_fixtures=True,
        demo_mode=False,
        llm=LLMSettings(
            provider="openrouter",
            base_url="http://127.0.0.1:9999/api/v1",
            api_key="test-api-key",
            model="demo-model",
            fallback_model="inclusionai/ling-2.6-flash:free",
            reasoning_enabled=False,
            timeout_seconds=41,
            retry_count=5,
        ),
    )
    persistence = SQLitePersistence(settings.sqlite_db_path, repo_root=settings.repo_root)
    analysis_service = AnalysisService(settings=settings, persistence=persistence)
    capturing_client = _CapturingLLMClient()
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_persistence] = lambda: persistence
    app.dependency_overrides[get_analysis_service] = lambda: analysis_service
    app.dependency_overrides[get_copilot_service] = lambda: CopilotService(
        settings=settings,
        persistence=persistence,
        llm_client=capturing_client,
    )
    client = TestClient(app)
    payload = _load_smoke_payload()

    try:
        analyze_response = client.post("/jobs/analyze", json=payload)
        assert analyze_response.status_code == 201
        job_id = analyze_response.json()["job_id"]
        explanation_response = client.get(f"/jobs/{job_id}/copilot/explanation")
    finally:
        client.close()

    assert explanation_response.status_code == 200
    assert len(capturing_client.requests) == 1
    assert capturing_client.requests[0].timeout_seconds == 41
    assert capturing_client.requests[0].retry_count == 5


def test_copilot_service_retries_once_after_retryable_validation_failure(tmp_path: Path) -> None:
    class _FlakyValidatedLLMClient:
        def __init__(self) -> None:
            self.requests: list[object] = []
            self.attempts = 0

        def generate_validated(self, request, response_model):
            self.requests.append(request)
            self.attempts += 1
            if self.attempts == 1:
                raise LLMResponseValidationError(
                    f"{request.operation} did not match {response_model.__name__}."
                )
            payload = {
                "job_id": request.metadata["job_id"],
                "sample_id": request.metadata["sample_id"],
                "target_drug": request.metadata["target_drug"],
                "summary": "Grounded decision explanation for the saved case.",
                "next_steps": ["confirm phenotype in downstream review flow"],
                "refusal_required": False,
                "refusal_reason": None,
                "cited_evidence_ids": ["decision_object__summary"],
                "answer_blocks": [
                    {
                        "block_id": "summary_block",
                        "block_type": "summary",
                        "title": "Summary",
                        "content": "Grounded decision explanation for the saved case.",
                        "cited_evidence_ids": ["decision_object__summary"],
                    }
                ],
                "warnings": [],
            }
            parsed = response_model.model_validate(payload)
            return ValidatedLLMResponse(
                response=LLMClientResponse(
                    provider="openrouter",
                    model="demo-model",
                    output_json=payload,
                ),
                parsed=parsed,
            )

    settings = AppSettings(
        app_env="test",
        repo_root=REPO_ROOT,
        artifact_root=tmp_path / "artifacts",
        sqlite_db_path=tmp_path / "phase7-retry.sqlite",
        use_fixtures=True,
        demo_mode=False,
        llm=LLMSettings(
            provider="openrouter",
            base_url="http://127.0.0.1:9999/api/v1",
            api_key="test-api-key",
            model="demo-model",
            fallback_model="inclusionai/ling-2.6-flash:free",
            reasoning_enabled=False,
            timeout_seconds=30,
            retry_count=2,
        ),
    )
    persistence = SQLitePersistence(settings.sqlite_db_path, repo_root=settings.repo_root)
    analysis_service = AnalysisService(settings=settings, persistence=persistence)
    flaky_client = _FlakyValidatedLLMClient()
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_persistence] = lambda: persistence
    app.dependency_overrides[get_analysis_service] = lambda: analysis_service
    app.dependency_overrides[get_copilot_service] = lambda: CopilotService(
        settings=settings,
        persistence=persistence,
        llm_client=flaky_client,
    )
    client = TestClient(app)
    payload = _load_smoke_payload()

    try:
        analyze_response = client.post("/jobs/analyze", json=payload)
        assert analyze_response.status_code == 201
        job_id = analyze_response.json()["job_id"]
        explanation_response = client.get(f"/jobs/{job_id}/copilot/explanation")
    finally:
        client.close()

    assert explanation_response.status_code == 200
    assert flaky_client.attempts == 2
