from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.dependencies import get_analysis_service, get_persistence, get_settings
from app.contracts import JobState, JobStatus, QueueItem, SampleInput, SeverityLevel, TriageOutcome
from app.main import create_app
from app.paths import display_path
from app.services import AnalysisService
from app.settings import AppSettings, load_settings
from app.storage import SQLitePersistence


REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_smoke_payload() -> dict[str, object]:
    payload = json.loads((REPO_ROOT / "data/fixtures/smoke/sample_001.metadata.json").read_text(encoding="utf-8"))
    return {
        "sample_id": payload["sample_id"],
        "organism_hint": payload["organism_hint"],
        "target_drug": payload["target_drug"],
        "fasta_path": payload["fasta_path"],
        "metadata": payload["metadata"],
    }


def _build_test_client(
    tmp_path: Path,
    *,
    artifact_root: Path | None = None,
    demo_mode: bool = False,
) -> tuple[TestClient, SQLitePersistence]:
    settings = AppSettings(
        app_env="test",
        repo_root=REPO_ROOT,
        artifact_root=artifact_root or tmp_path / "artifacts",
        sqlite_db_path=tmp_path / "phase6.sqlite",
        use_fixtures=True,
        demo_mode=demo_mode,
    )
    persistence = SQLitePersistence(settings.sqlite_db_path, repo_root=settings.repo_root)
    service = AnalysisService(settings=settings, persistence=persistence)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_persistence] = lambda: persistence
    app.dependency_overrides[get_analysis_service] = lambda: service
    return TestClient(app), persistence


def _seed_queue_item(
    persistence: SQLitePersistence,
    *,
    job_id: str,
    triage: TriageOutcome,
    severity: SeverityLevel,
    queue_priority: int,
    status: JobState,
) -> None:
    sample = SampleInput.model_validate(_load_smoke_payload())
    persistence.upsert_sample(sample)
    persistence.create_job(
        JobStatus(
            job_id=job_id,
            sample_id=sample.sample_id,
            target_drug=sample.target_drug,
            status=status,
            current_step="decision_ready",
        ),
        sample=sample,
    )
    persistence.save_queue_item(
        QueueItem(
            job_id=job_id,
            sample_id=sample.sample_id,
            target_drug=sample.target_drug,
            triage=triage,
            severity=severity,
            status=status,
            queue_priority=queue_priority,
            headline=f"{triage.value} case for {sample.sample_id}",
            rationale_codes=["manual_confirmation_required"],
        )
    )


def test_health_endpoint_boots(tmp_path: Path) -> None:
    client, _ = _build_test_client(tmp_path)
    try:
        response = client.get("/health")
    finally:
        client.close()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["runtime"]["app_env"] == "test"
    assert body["runtime"]["backend_mode"] == "persisted"
    assert body["runtime"]["job_data_mode"] == "persisted_jobs"
    assert body["runtime"]["evidence_mode"] == "fixture"
    assert body["runtime"]["llm_mode"] == "live"
    assert body["runtime"]["live_mode_ready"] is False
    assert body["runtime"]["live_mode_blockers"] == ["fixture_mode_enabled"]


def test_integration_health_endpoint_exposes_runtime_and_operational_dependencies(
    tmp_path: Path,
) -> None:
    client, _ = _build_test_client(tmp_path)
    try:
        response = client.get("/health/integrations")
    finally:
        client.close()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "fixture"
    assert body["mode"] == "fixture"
    assert body["runtime"]["backend_mode"] == "persisted"
    assert body["runtime"]["evidence_mode"] == "fixture"
    assert body["external_apis"]["ncbi_datasets"]["status"] == "configured"
    assert body["external_apis"]["llm"]["status"] == "missing"
    assert body["external_apis"]["llm"]["api_key"] == "missing"
    assert body["external_apis"]["llm"]["mock_mode"] is False
    assert body["external_apis"]["thesys"]["status"] == "missing"
    assert body["external_apis"]["thesys"]["api_key"] == "missing"
    assert body["tools"]["amrfinderplus"]["status"] == "missing"
    assert body["tools"]["mash"]["status"] == "missing"
    assert body["secrets"] == {"redacted": True, "values_exposed": False}


def test_analyze_endpoint_runs_fixture_backed_job_and_persists_decision(tmp_path: Path) -> None:
    client, persistence = _build_test_client(tmp_path)
    payload = _load_smoke_payload()
    try:
        response = client.post("/jobs/analyze", json=payload)
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "degraded"
        job_id = body["job_id"]

        status_response = client.get(f"/jobs/{job_id}/status")
        assert status_response.status_code == 200
        assert status_response.json()["status"] == "degraded"

        decision_response = client.get(f"/jobs/{job_id}/decision")
        assert decision_response.status_code == 200
        decision_payload = decision_response.json()
    finally:
        client.close()

    assert decision_payload["job_status"]["job_id"] == job_id
    assert decision_payload["decision"]["job_id"] == job_id
    assert decision_payload["decision"]["sample"]["sample_id"] == "sample_001"
    assert decision_payload["decision"]["triage_decision"]["triage"] == "act"
    assert decision_payload["decision"]["phenotype_prediction"]["job_id"] == job_id
    assert decision_payload["decision"]["novelty_assessment"]["job_id"] == job_id
    assert persistence.get_job_decision_response(job_id) is not None


def test_rerun_preserves_original_sample_snapshot_per_job(tmp_path: Path) -> None:
    client, _ = _build_test_client(tmp_path)
    first_payload = _load_smoke_payload()
    second_payload = {
        **first_payload,
        "fasta_path": "data/fixtures/smoke/reference_ec_001.fasta",
    }
    try:
        first_response = client.post("/jobs/analyze", json=first_payload)
        second_response = client.post("/jobs/analyze", json=second_payload)
        assert first_response.status_code == 201
        assert second_response.status_code == 201

        first_job_id = first_response.json()["job_id"]
        second_job_id = second_response.json()["job_id"]
        first_decision = client.get(f"/jobs/{first_job_id}/decision").json()
        second_decision = client.get(f"/jobs/{second_job_id}/decision").json()
    finally:
        client.close()

    assert first_decision["decision"]["sample"]["fasta_path"] == first_payload["fasta_path"]
    assert second_decision["decision"]["sample"]["fasta_path"] == second_payload["fasta_path"]


def test_artifact_manifest_endpoint_returns_safe_preview_metadata(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    client, _ = _build_test_client(tmp_path, artifact_root=artifact_root)
    payload = _load_smoke_payload()
    try:
        analyze_response = client.post("/jobs/analyze", json=payload)
        assert analyze_response.status_code == 201
        job_id = analyze_response.json()["job_id"]

        artifact_response = client.get(f"/jobs/{job_id}/artifacts")
    finally:
        client.close()

    assert artifact_response.status_code == 200
    manifest_payload = artifact_response.json()
    assert manifest_payload["job_id"] == job_id
    assert manifest_payload["sample_id"] == "sample_001"
    assert manifest_payload["target_drug"] == "tetracycline"
    expected_root = (artifact_root / "runs" / "jobs" / job_id).resolve().as_posix()
    assert manifest_payload["artifact_root"] == expected_root
    assert any(item["preview_eligible"] for item in manifest_payload["artifacts"])
    decision_artifact = next(
        item
        for item in manifest_payload["artifacts"]
        if item["artifact_id"] == f"{job_id}_decision_json"
    )
    assert decision_artifact["path"].startswith(expected_root)
    assert Path(decision_artifact["path"]).is_absolute()


def test_artifact_manifest_endpoint_preserves_external_artifact_root(tmp_path: Path) -> None:
    external_artifact_root = tmp_path / "live_cache"
    client, _ = _build_test_client(tmp_path, artifact_root=external_artifact_root)
    payload = _load_smoke_payload()
    try:
        analyze_response = client.post("/jobs/analyze", json=payload)
        assert analyze_response.status_code == 201
        job_id = analyze_response.json()["job_id"]

        artifact_response = client.get(f"/jobs/{job_id}/artifacts")
    finally:
        client.close()

    assert artifact_response.status_code == 200
    manifest_payload = artifact_response.json()
    expected_root = (external_artifact_root / "runs" / "jobs" / job_id).resolve().as_posix()
    assert manifest_payload["artifact_root"] == expected_root
    decision_artifact = next(
        item
        for item in manifest_payload["artifacts"]
        if item["artifact_id"] == f"{job_id}_decision_json"
    )
    assert Path(decision_artifact["path"]).is_absolute()


def test_artifact_manifest_endpoint_preserves_external_artifact_root_named_artifacts(tmp_path: Path) -> None:
    external_artifact_root = tmp_path / "cache" / "artifacts"
    client, _ = _build_test_client(tmp_path, artifact_root=external_artifact_root)
    payload = _load_smoke_payload()
    try:
        analyze_response = client.post("/jobs/analyze", json=payload)
        assert analyze_response.status_code == 201
        job_id = analyze_response.json()["job_id"]

        artifact_response = client.get(f"/jobs/{job_id}/artifacts")
    finally:
        client.close()

    assert artifact_response.status_code == 200
    manifest_payload = artifact_response.json()
    expected_root = (external_artifact_root / "runs" / "jobs" / job_id).resolve().as_posix()
    assert manifest_payload["artifact_root"] == expected_root
    decision_artifact = next(
        item
        for item in manifest_payload["artifacts"]
        if item["artifact_id"] == f"{job_id}_decision_json"
    )
    assert decision_artifact["path"].startswith(expected_root)
    assert Path(decision_artifact["path"]).is_absolute()


def test_load_settings_expands_user_paths_for_artifact_root_and_sqlite_db(monkeypatch) -> None:
    monkeypatch.setenv("ARTIFACT_ROOT", "~/aixbio-artifacts")
    monkeypatch.setenv("SQLITE_DB_PATH", "~/aixbio.sqlite")

    settings = load_settings()

    assert settings.artifact_root == (Path("~/aixbio-artifacts").expanduser())
    assert settings.sqlite_db_path == (Path("~/aixbio.sqlite").expanduser())


def test_display_path_preserves_repo_local_prefix_for_nested_artifact_root(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    nested_artifact_path = (
        repo_root / "data" / "artifacts" / "runs" / "jobs" / "job_001" / "decision.json"
    )

    assert display_path(nested_artifact_path, repo_root=repo_root) == (
        "data/artifacts/runs/jobs/job_001/decision.json"
    )


def test_analyze_endpoint_rejects_unsafe_fasta_path(tmp_path: Path) -> None:
    client, _ = _build_test_client(tmp_path)
    payload = _load_smoke_payload()
    payload["fasta_path"] = "../outside/sample_001.fasta"
    try:
        response = client.post("/jobs/analyze", json=payload)
    finally:
        client.close()

    assert response.status_code == 400
    assert "repo-relative fasta_path" in response.json()["detail"]


def test_analyze_endpoint_rejects_targets_without_matching_prediction_model(tmp_path: Path) -> None:
    client, persistence = _build_test_client(tmp_path)
    payload = _load_smoke_payload()
    payload["target_drug"] = "ampicillin"
    try:
        response = client.post("/jobs/analyze", json=payload)
    finally:
        client.close()

    assert response.status_code == 400
    assert "e_coli/tetracycline" in response.json()["detail"]
    with persistence.connect() as connection:
        row = connection.execute("SELECT COUNT(*) AS count FROM jobs").fetchone()
    assert row["count"] == 0


def test_job_endpoints_return_not_found_for_missing_job(tmp_path: Path) -> None:
    client, _ = _build_test_client(tmp_path)
    try:
        status_response = client.get("/jobs/job_missing_001/status")
        decision_response = client.get("/jobs/job_missing_001/decision")
    finally:
        client.close()

    assert status_response.status_code == 404
    assert decision_response.status_code == 404


def test_queue_summary_endpoint_returns_sorted_persisted_items(tmp_path: Path) -> None:
    client, persistence = _build_test_client(tmp_path)
    _seed_queue_item(
        persistence,
        job_id="job_queue_review",
        triage=TriageOutcome.REVIEW,
        severity=SeverityLevel.MEDIUM,
        queue_priority=10,
        status=JobState.DEGRADED,
    )
    _seed_queue_item(
        persistence,
        job_id="job_queue_act",
        triage=TriageOutcome.ACT,
        severity=SeverityLevel.CRITICAL,
        queue_priority=0,
        status=JobState.COMPLETED,
    )
    _seed_queue_item(
        persistence,
        job_id="job_queue_defer",
        triage=TriageOutcome.DEFER_TO_LAB,
        severity=SeverityLevel.HIGH,
        queue_priority=20,
        status=JobState.COMPLETED,
    )

    try:
        response = client.get("/queue")
        filtered_response = client.get("/queue", params={"triage": "review"})
    finally:
        client.close()

    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["job_id"] for item in items] == [
        "job_queue_act",
        "job_queue_review",
        "job_queue_defer",
    ]
    assert filtered_response.status_code == 200
    filtered_items = filtered_response.json()["items"]
    assert len(filtered_items) == 1
    assert filtered_items[0]["job_id"] == "job_queue_review"


def test_queue_summary_endpoint_returns_demo_examples_when_enabled(tmp_path: Path) -> None:
    client, _ = _build_test_client(tmp_path, demo_mode=True)
    try:
        response = client.get("/queue")
    finally:
        client.close()

    assert response.status_code == 200
    triage_values = {item["triage"] for item in response.json()["items"]}
    assert triage_values == {"act", "review", "defer_to_lab"}


def test_openapi_json_matches_current_app_contract(tmp_path: Path) -> None:
    client, _ = _build_test_client(tmp_path)
    try:
        generated_spec = client.app.openapi()
    finally:
        client.close()

    committed_spec = json.loads((REPO_ROOT / "openapi.json").read_text(encoding="utf-8"))
    assert committed_spec == generated_spec
