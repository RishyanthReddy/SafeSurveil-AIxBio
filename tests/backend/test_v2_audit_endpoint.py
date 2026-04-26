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
    DecisionCardBlock,
    DecisionObject,
    EvidenceTableBlock,
    EvidenceTableRow,
    MetricDatum,
    RiskChartBlock,
    RiskChartPoint,
    SafetyProfileAxis,
    SafetyProfileBlock,
    SemanticUIObject,
    V2_AUDIT_BUNDLE_SCHEMA_VERSION,
    V2_AUDIT_REQUIRED_SECTIONS,
)
from app.main import create_app
from app.paths import display_path
from app.services import AnalysisService
from app.settings import AppSettings, LLMSettings, ThesysSettings
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
        llm=LLMSettings(
            provider="openrouter",
            base_url="https://openrouter.ai/api/v1",
            api_key="test-openrouter-key",
            model="google/gemma-3-4b-it:free",
            mock_mode=False,
        ),
        thesys=ThesysSettings(api_key="test-thesys-key"),
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


def _semantic_ui_from_decision(decision: DecisionObject) -> SemanticUIObject:
    first_evidence = decision.mechanistic_evidence[0] if decision.mechanistic_evidence else None
    evidence_table = None
    if first_evidence is not None:
        signal = first_evidence.gene_symbol or first_evidence.mutation or "mechanism"
        evidence_table = EvidenceTableBlock(
            title="Mechanistic Evidence",
            columns=["signal", "detail", "support"],
            rows=[
                EvidenceTableRow(
                    row_id="mechanism_001",
                    label=signal,
                    cells={
                        "signal": signal,
                        "detail": first_evidence.interpretation,
                        "support": first_evidence.support_level.value,
                    },
                    evidence_id="mechanistic_evidence__1",
                )
            ],
        )
    return SemanticUIObject(
        decision_card=DecisionCardBlock(
            title="Decision Overview",
            triage_decision=decision.triage_decision.triage,
            severity=decision.triage_decision.severity,
            summary="Grounded decision overview follows the persisted triage policy.",
            metrics=[
                MetricDatum(
                    key="probability",
                    label="Probability",
                    value=decision.phenotype_prediction.probability,
                ),
                MetricDatum(
                    key="actionability_score",
                    label="Actionability",
                    value=decision.actionability_features.actionability_score,
                ),
                MetricDatum(
                    key="novelty_score",
                    label="Novelty Score",
                    value=decision.novelty_assessment.novelty_score,
                ),
            ],
        ),
        evidence_table=evidence_table,
        risk_charts=[
            RiskChartBlock(
                chart_id="risk_overview",
                title="Risk Overview",
                chart_type="bar",
                points=[
                    RiskChartPoint(
                        label="QC Risk",
                        value=decision.actionability_features.qc_risk,
                        evidence_id="decision_object__assembly_qc",
                    )
                ],
            )
        ],
        safety_profile=SafetyProfileBlock(
            title="Safety Profile",
            axes=[
                SafetyProfileAxis(
                    label="Metadata Completeness",
                    value=decision.actionability_features.metadata_completeness,
                )
            ],
        ),
    )


def _copilot_from_decision(decision: DecisionObject) -> CopilotResponse:
    cited_evidence_ids = [
        "decision_object__summary",
        "decision_object__triage",
        "decision_object__warnings",
        "phenotype_prediction__summary",
        "actionability_features__summary",
        "novelty_assessment__summary",
    ]
    if decision.mechanistic_evidence:
        cited_evidence_ids.extend(
            f"mechanistic_evidence__{index}"
            for index, _ in enumerate(decision.mechanistic_evidence, start=1)
        )
    else:
        cited_evidence_ids.append("mechanistic_evidence__none")
    return CopilotResponse(
        job_id=decision.job_id or decision.triage_decision.job_id,
        sample_id=decision.sample.sample_id,
        target_drug=decision.sample.target_drug,
        summary="Grounded explanation preserves the persisted triage policy and evidence limits.",
        next_steps=[decision.triage_decision.recommended_next_step],
        cited_evidence_ids=cited_evidence_ids,
        answer_blocks=[
            CopilotAnswerBlock(
                block_id="summary_block",
                block_type="summary",
                title="Decision summary",
                content="Persisted evidence supports the recorded decision boundary.",
                cited_evidence_ids=["decision_object__summary"],
            )
        ],
    )


def _write_cached_sidecars(
    *,
    decision: DecisionObject,
    persistence: SQLitePersistence,
    settings: AppSettings,
) -> None:
    job_id = decision.job_id or decision.triage_decision.job_id
    output_dir = settings.artifact_root / "runs" / "jobs" / job_id / "copilot"
    output_dir.mkdir(parents=True, exist_ok=True)
    copilot_path = output_dir / "explanation.json"
    semantic_ui_path = output_dir / "semantic_ui.json"
    copilot_path.write_text(_copilot_from_decision(decision).model_dump_json(indent=2), encoding="utf-8")
    semantic_ui_path.write_text(_semantic_ui_from_decision(decision).model_dump_json(indent=2), encoding="utf-8")
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
            ),
            ArtifactRecord(
                artifact_id=f"{job_id}_semantic_ui_json",
                job_id=job_id,
                sample_id=decision.sample.sample_id,
                target_drug=decision.sample.target_drug,
                kind=ArtifactKind.SEMANTIC_UI,
                path=display_path(semantic_ui_path, repo_root=settings.repo_root),
                media_type="application/json",
                generated_by="copilot_service",
                size_bytes=semantic_ui_path.stat().st_size,
            ),
        ]
    )


def _section(body: dict[str, object], section_id: str) -> dict[str, object]:
    sections = body["sections"]
    assert isinstance(sections, list)
    for section in sections:
        assert isinstance(section, dict)
        if section["section_id"] == section_id:
            return section
    raise AssertionError(f"Missing audit section {section_id}")


def test_v2_audit_endpoint_returns_not_found_for_missing_job(tmp_path: Path) -> None:
    client, _, _ = _build_test_client(tmp_path)
    try:
        response = client.get("/jobs/job_missing_001/v2-audit")
    finally:
        client.close()

    assert response.status_code == 404
    assert response.json()["detail"] == "Decision not found."


def test_v2_audit_endpoint_returns_coherent_bundle_for_completed_job(tmp_path: Path) -> None:
    client, _, _ = _build_test_client(tmp_path)
    try:
        job_id = _submit_smoke_job(client)
        response = client.get(f"/jobs/{job_id}/v2-audit")
    finally:
        client.close()

    assert response.status_code == 200
    body = response.json()
    summary = body["summary"]
    provenance = body["provenance"]
    section_ids = {section["section_id"] for section in body["sections"]}

    assert body["schema_version"] == V2_AUDIT_BUNDLE_SCHEMA_VERSION
    assert body["job_id"] == job_id
    assert set(item.value for item in V2_AUDIT_REQUIRED_SECTIONS) == section_ids
    assert body["metadata"]["provider_calls_triggered"] is False
    assert body["metadata"]["builder"] == "deterministic_v2_audit_bundle"
    assert provenance["live_input"] is False
    assert provenance["fixture_trained_baseline"] is True
    assert summary["live_ready"] is False
    assert summary["provider_proof_required"] is True
    assert summary["total_checks"] == sum(len(section["checks"]) for section in body["sections"])
    assert summary["warning_checks"] >= 1
    assert summary["pending_checks"] == 2
    assert _section(body, "openrouter_proof")["status"] == "pending"
    assert _section(body, "thesys_proof")["status"] == "pending"
    assert _section(body, "artifact_coverage")["status"] == "pass"


def test_v2_audit_endpoint_does_not_count_cached_sidecars_as_provider_proof(tmp_path: Path) -> None:
    client, persistence, settings = _build_test_client(tmp_path)
    try:
        job_id = _submit_smoke_job(client)
        decision_response = persistence.get_job_decision_response(job_id)
        assert decision_response is not None
        _write_cached_sidecars(
            decision=decision_response.decision,
            persistence=persistence,
            settings=settings,
        )
        response = client.get(f"/jobs/{job_id}/v2-audit")
    finally:
        client.close()

    assert response.status_code == 200
    body = response.json()
    openrouter_section = _section(body, "openrouter_proof")
    thesys_section = _section(body, "thesys_proof")

    assert body["metadata"]["cached_copilot_sidecar_available"] is True
    assert body["metadata"]["cached_semantic_ui_sidecar_available"] is True
    assert body["metadata"]["provider_calls_triggered"] is False
    assert openrouter_section["status"] == "pending"
    assert thesys_section["status"] == "pending"
    assert "cached content is not counted" in openrouter_section["checks"][0]["detail"]
    assert _section(body, "execution_gate")["status"] == "pass"


def test_v2_audit_endpoint_is_exposed_in_openapi(tmp_path: Path) -> None:
    client, _, _ = _build_test_client(tmp_path)
    try:
        response = client.get("/openapi.json")
    finally:
        client.close()

    assert response.status_code == 200
    body = response.json()
    route = body["paths"]["/jobs/{job_id}/v2-audit"]["get"]
    assert route["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/V2AuditBundle"
    )
