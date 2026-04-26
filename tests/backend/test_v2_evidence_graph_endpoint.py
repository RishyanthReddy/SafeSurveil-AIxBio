from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.dependencies import get_analysis_service, get_persistence, get_settings
from app.contracts import (
    ArtifactKind,
    ArtifactRecord,
    CopilotAnswerBlock,
    CopilotResponse,
    DecisionObject,
)
from app.main import create_app
from app.paths import display_path
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


def _build_test_client(tmp_path: Path) -> tuple[TestClient, SQLitePersistence, AppSettings]:
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
    return TestClient(app), persistence, settings


def _submit_smoke_job(client: TestClient) -> str:
    response = client.post("/jobs/analyze", json=_load_smoke_payload())
    assert response.status_code == 201
    return response.json()["job_id"]


def _copilot_from_decision(decision: DecisionObject) -> CopilotResponse:
    return CopilotResponse(
        job_id=decision.job_id or decision.triage_decision.job_id,
        sample_id=decision.sample.sample_id,
        target_drug=decision.sample.target_drug,
        summary="Grounded explanation preserves the persisted evidence boundary.",
        next_steps=[decision.triage_decision.recommended_next_step],
        cited_evidence_ids=["decision_object__summary", "mechanistic_evidence__1"],
        answer_blocks=[
            CopilotAnswerBlock(
                block_id="summary_block",
                block_type="summary",
                title="Decision summary",
                content="Persisted decision and mechanism evidence are cited.",
                cited_evidence_ids=["decision_object__triage", "actionability_features__summary"],
            )
        ],
    )


def _write_cached_copilot(
    *,
    decision: DecisionObject,
    persistence: SQLitePersistence,
    settings: AppSettings,
) -> None:
    job_id = decision.job_id or decision.triage_decision.job_id
    output_dir = settings.artifact_root / "runs" / "jobs" / job_id / "copilot"
    output_dir.mkdir(parents=True, exist_ok=True)
    copilot_path = output_dir / "explanation.json"
    copilot_path.write_text(_copilot_from_decision(decision).model_dump_json(indent=2), encoding="utf-8")
    persistence.save_artifacts(
        [
            ArtifactRecord(
                artifact_id=f"{job_id}_copilot_explanation_json",
                job_id=job_id,
                sample_id=decision.sample.sample_id,
                target_drug=decision.sample.target_drug,
                kind=ArtifactKind.COPILOT_OUTPUT,
                path=display_path(copilot_path, repo_root=settings.repo_root),
                media_type="application/json",
                generated_by="copilot_service",
                size_bytes=copilot_path.stat().st_size,
            )
        ]
    )


def _node_ids(body: dict[str, object]) -> set[str]:
    nodes = body["nodes"]
    assert isinstance(nodes, list)
    return {str(node["node_id"]) for node in nodes if isinstance(node, dict)}


def _node_classes(body: dict[str, object]) -> set[str]:
    nodes = body["nodes"]
    assert isinstance(nodes, list)
    return {str(node["node_class"]) for node in nodes if isinstance(node, dict)}


def test_evidence_graph_endpoint_returns_not_found_for_missing_job(tmp_path: Path) -> None:
    client, _, _ = _build_test_client(tmp_path)
    try:
        response = client.get("/jobs/job_missing_001/evidence-graph")
    finally:
        client.close()

    assert response.status_code == 404
    assert response.json()["detail"] == "Decision not found."


def test_evidence_graph_endpoint_returns_deterministic_graph_for_completed_job(tmp_path: Path) -> None:
    client, _, _ = _build_test_client(tmp_path)
    try:
        job_id = _submit_smoke_job(client)
        response = client.get(f"/jobs/{job_id}/evidence-graph")
    finally:
        client.close()

    assert response.status_code == 200
    body = response.json()
    stats = body["stats"]
    metadata = body["metadata"]

    assert body["schema_version"] == "v2.evidence_graph.v1"
    assert body["job_id"] == job_id
    assert metadata["provider_calls_triggered"] is False
    assert metadata["builder"] == "deterministic_decision_object_evidence_graph"
    assert stats["completeness_ratio"] == 1.0
    assert stats["weakly_connected"] is True
    assert stats["isolated_node_ids"] == []
    assert stats["artifact_nodes"] >= 1
    assert stats["artifact_linkage_ratio"] == 1.0
    assert "sample" in _node_classes(body)
    assert "decision" in _node_classes(body)
    assert "execution_gate__report" in _node_ids(body)
    assert "reasoning_trace__summary" in _node_ids(body)
    assert "warning__copilot_sidecar_missing" in _node_ids(body)


def test_evidence_graph_endpoint_links_cached_copilot_citations(tmp_path: Path) -> None:
    client, persistence, settings = _build_test_client(tmp_path)
    try:
        job_id = _submit_smoke_job(client)
        decision_response = persistence.get_job_decision_response(job_id)
        assert decision_response is not None
        _write_cached_copilot(
            decision=decision_response.decision,
            persistence=persistence,
            settings=settings,
        )
        response = client.get(f"/jobs/{job_id}/evidence-graph")
    finally:
        client.close()

    assert response.status_code == 200
    body = response.json()
    stats = body["stats"]
    node_ids = _node_ids(body)

    assert "copilot__grounded_response" in node_ids
    assert "warning__copilot_sidecar_missing" not in node_ids
    assert stats["citation_nodes"] >= 3
    assert stats["linked_citation_nodes"] == stats["citation_nodes"]
    assert stats["citation_linkage_ratio"] == 1.0
    assert body["metadata"]["provider_calls_triggered"] is False
