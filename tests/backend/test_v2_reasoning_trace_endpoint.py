from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.dependencies import get_analysis_service, get_persistence, get_settings
from app.main import create_app
from app.services import AnalysisService
from app.settings import AppSettings
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


def _build_test_client(tmp_path: Path) -> tuple[TestClient, SQLitePersistence]:
    settings = AppSettings(
        app_env="test",
        repo_root=REPO_ROOT,
        artifact_root=tmp_path / "artifacts",
        sqlite_db_path=tmp_path / "phase8.sqlite",
        use_fixtures=True,
        demo_mode=False,
    )
    persistence = SQLitePersistence(settings.sqlite_db_path, repo_root=settings.repo_root)
    service = AnalysisService(settings=settings, persistence=persistence)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_persistence] = lambda: persistence
    app.dependency_overrides[get_analysis_service] = lambda: service
    return TestClient(app), persistence


def _submit_smoke_job(client: TestClient) -> str:
    response = client.post("/jobs/analyze", json=_load_smoke_payload())
    assert response.status_code == 201
    return response.json()["job_id"]


def test_reasoning_trace_endpoint_returns_not_found_for_missing_job(tmp_path: Path) -> None:
    client, _ = _build_test_client(tmp_path)
    try:
        response = client.get("/jobs/job_missing_001/reasoning-trace")
    finally:
        client.close()

    assert response.status_code == 404
    assert response.json()["detail"] == "Decision not found."


def test_reasoning_trace_endpoint_returns_deterministic_trace_for_completed_job(tmp_path: Path) -> None:
    payload = _load_smoke_payload()
    client, _ = _build_test_client(tmp_path)
    try:
        job_id = _submit_smoke_job(client)
        response = client.get(f"/jobs/{job_id}/reasoning-trace")
    finally:
        client.close()

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "v2.reasoning_trace.v1"
    assert body["job_id"] == job_id
    assert body["sample_id"] == payload["sample_id"]
    assert body["target_drug"] == payload["target_drug"]
    assert body["metadata"]["provider_calls_triggered"] is False
    assert body["metadata"]["builder"] == "deterministic_decision_object_trace"
    assert body["coverage"]["required_steps"] == 8
    assert body["coverage"]["present_steps"] == 8
    assert body["coverage"]["coverage_ratio"] == 1.0
    assert [step["step_number"] for step in body["steps"]] == list(range(1, 9))
    assert body["steps"][0]["step_type"] == "sample_context"
    assert body["steps"][-1]["step_type"] == "final_triage"
    assert all(step["evidence_refs"] for step in body["steps"])


def test_reasoning_trace_endpoint_uses_same_trace_as_verification_gate(tmp_path: Path) -> None:
    client, _ = _build_test_client(tmp_path)
    try:
        job_id = _submit_smoke_job(client)
        trace_response = client.get(f"/jobs/{job_id}/reasoning-trace")
        verification_response = client.get(f"/jobs/{job_id}/verification")
    finally:
        client.close()

    assert trace_response.status_code == 200
    assert verification_response.status_code == 200
    trace = trace_response.json()
    verification = verification_response.json()
    trace_checks = [check for check in verification["checks"] if check["category"] == "reasoning_trace"]

    assert trace["coverage"]["present_steps"] == 8
    assert len(trace_checks) == 6
    assert all(check["status"] == "pass" for check in trace_checks)
    assert verification["metadata"]["reasoning_trace_available"] is True
