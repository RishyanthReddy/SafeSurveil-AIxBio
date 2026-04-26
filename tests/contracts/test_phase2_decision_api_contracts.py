from __future__ import annotations

from datetime import date

import pytest

from app.contracts import (
    ActionabilityFeatures,
    AnalyzeJobRequest,
    AnalyzeJobResponse,
    AllowedEvidenceSource,
    ArtifactKind,
    ArtifactManifest,
    ArtifactRecord,
    AssemblyQC,
    CopilotOutputOrigin,
    CopilotContext,
    CopilotContextSection,
    CopilotOutputMode,
    CopilotResponse,
    DecisionCardBlock,
    DecisionObject,
    EvidenceTableBlock,
    EvidenceTableRow,
    JobCopilotResponse,
    JobDecisionResponse,
    JobState,
    JobStatus,
    MechanisticEvidence,
    MetricDatum,
    NoveltyAssessment,
    NoveltyBucket,
    OrganismConsistency,
    OrganismHint,
    PhenotypePrediction,
    PredictedPhenotype,
    QCStatus,
    QueueBlock,
    QueueItem,
    QueueSummaryResponse,
    RationaleCode,
    RiskChartBlock,
    RiskChartPoint,
    SafetyProfileAxis,
    SafetyProfileBlock,
    SemanticUIObject,
    SeverityLevel,
    SourceContext,
    TriageDecision,
    TriageOutcome,
)
from app.contracts import CalibrationStatus, SampleInput, SampleMetadata


def build_sample() -> SampleInput:
    return SampleInput(
        sample_id="sample_001",
        organism_hint=OrganismHint.E_COLI,
        target_drug="tetracycline",
        fasta_path="data/fixtures/sample.fa",
        metadata=SampleMetadata(
            accession="ACC-001",
            collection_date=date(2026, 4, 20),
            source_context=SourceContext.BOVINE_MILK,
            country="IN",
        ),
    )


def build_qc() -> AssemblyQC:
    return AssemblyQC(
        job_id="job_001",
        sample_id="sample_001",
        target_drug="tetracycline",
        file_valid=True,
        sequence_count=12,
        total_bases=5032121,
        ambiguous_base_fraction=0.01,
        organism_consistency=OrganismConsistency.MATCH,
        qc_status=QCStatus.WARN,
        missing_metadata_fields=[],
        warnings=["coverage check pending"],
    )


def build_prediction() -> PhenotypePrediction:
    return PhenotypePrediction(
        job_id="job_001",
        sample_id="sample_001",
        target_drug="tetracycline",
        predicted_phenotype=PredictedPhenotype.RESISTANT,
        probability=0.83,
        calibration_status=CalibrationStatus.NOT_AVAILABLE,
        uncertainty_score=0.17,
        feature_set_version="kmers_v1",
        model_version="baseline_v1",
    )


def build_novelty() -> NoveltyAssessment:
    return NoveltyAssessment(
        job_id="job_001",
        sample_id="sample_001",
        target_drug="tetracycline",
        reference_snapshot_id="snapshot_2026_04_20",
        nearest_neighbor_id="ref_0001",
        nearest_neighbor_distance=0.12,
        novelty_score=0.71,
        novelty_percentile=87.0,
        novelty_bucket=NoveltyBucket.HIGH,
    )


def build_actionability() -> ActionabilityFeatures:
    return ActionabilityFeatures(
        job_id="job_001",
        sample_id="sample_001",
        target_drug="tetracycline",
        actionability_score=0.18,
        mechanism_concordance=False,
        prediction_entropy=0.42,
        qc_risk=0.25,
        novelty_risk=0.71,
        metadata_completeness=1.0,
        threshold_version="policy_v1",
        warnings=["novelty elevated"],
    )


def build_triage() -> TriageDecision:
    return TriageDecision(
        job_id="job_001",
        sample_id="sample_001",
        target_drug="tetracycline",
        triage="DEFER_TO_LAB",
        severity="HIGH",
        recommended_next_step="confirm phenotype in downstream review flow",
        threshold_version="policy_v1",
        rationale_codes=[
            "NO_SUPPORTED_MECHANISM",
            "HIGH_LINEAGE_NOVELTY",
            "MANUAL_CONFIRMATION_REQUIRED",
        ],
    )


def build_semantic_ui() -> SemanticUIObject:
    queue_item = QueueItem(
        job_id="job_001",
        sample_id="sample_001",
        target_drug="tetracycline",
        triage="defer_to_lab",
        severity="high",
        status="decision_ready",
        queue_priority=10,
        headline="High novelty case awaiting analyst review",
        rationale_codes=["high_lineage_novelty", "manual_confirmation_required"],
    )
    return SemanticUIObject(
        decision_card=DecisionCardBlock(
            title="Decision Overview",
            triage_decision="defer_to_lab",
            severity="high",
            summary="Prediction is present, but the case should wait for manual confirmation.",
            metrics=[
                MetricDatum(key="probability", label="Probability", value=0.83),
                MetricDatum(key="novelty_score", label="Novelty", value=0.71),
            ],
        ),
        evidence_table=EvidenceTableBlock(
            title="Mechanistic Evidence",
            columns=["signal", "support"],
            rows=[
                EvidenceTableRow(
                    row_id="row_1",
                    label="No supported marker",
                    cells={"signal": "none", "support": "screen_only"},
                    evidence_id="ev_001",
                )
            ],
        ),
        risk_charts=[
            RiskChartBlock(
                chart_id="risk_1",
                title="Risk Factors",
                chart_type="bar",
                points=[
                    RiskChartPoint(label="Novelty", value=0.71, evidence_id="ev_001"),
                    RiskChartPoint(label="QC", value=0.25),
                ],
            )
        ],
        safety_profile=SafetyProfileBlock(
            title="Safety Profile",
            axes=[
                SafetyProfileAxis(label="Evidence Support", value=0.2),
                SafetyProfileAxis(label="Novelty Risk", value=0.71),
            ],
        ),
        queue_block=QueueBlock(title="Queue", items=[queue_item]),
    )


def test_analyze_job_request_accepts_report_aliases() -> None:
    request = AnalyzeJobRequest(
        sample_id="ISO-2026-X",
        organism_hint="staphylococcus_aureus",
        target_drug="tetracycline",
        fasta_uri="s3://bucket/sample.fa",
        metadata={
            "collection_date": "2026-04-15",
            "source": "bovine_mastitis",
            "country": "IN",
        },
    )

    assert request.sample_id == "iso-2026-x"
    assert request.organism_hint == OrganismHint.S_AUREUS
    assert request.metadata.source_context == SourceContext.BOVINE_MILK


def test_novelty_assessment_requires_metrics_or_sparse_flag() -> None:
    with pytest.raises(ValueError, match="Novelty metrics or missing_reference=True must be provided"):
        NoveltyAssessment(
            job_id="job_001",
            sample_id="sample_001",
            target_drug="tetracycline",
            reference_snapshot_id="snapshot_2026_04_20",
        )


def test_novelty_assessment_allows_sparse_reference_flag() -> None:
    novelty = NoveltyAssessment(
        job_id="job_001",
        sample_id="sample_001",
        target_drug="tetracycline",
        reference_snapshot_id="snapshot_2026_04_20",
        missing_reference=True,
        novelty_bucket="unknown",
    )

    assert novelty.missing_reference is True
    assert novelty.novelty_bucket == NoveltyBucket.UNKNOWN


def test_triage_decision_requires_rationale_codes() -> None:
    with pytest.raises(ValueError, match="At least one rationale code must be present"):
        TriageDecision(
            job_id="job_001",
            sample_id="sample_001",
            target_drug="tetracycline",
            triage="review",
            severity="medium",
            recommended_next_step="collect more supporting evidence",
            threshold_version="policy_v1",
        )


def test_decision_object_rejects_nested_target_drift() -> None:
    with pytest.raises(ValueError, match="All decision-layer contracts must reference the same target_drug"):
        DecisionObject(
            sample=build_sample(),
            assembly_qc=build_qc(),
            mechanistic_evidence=[],
            phenotype_prediction=PhenotypePrediction(
                job_id="job_001",
                sample_id="sample_001",
                target_drug="ciprofloxacin",
                predicted_phenotype=PredictedPhenotype.RESISTANT,
                probability=0.83,
                calibration_status=CalibrationStatus.NOT_AVAILABLE,
                uncertainty_score=0.17,
                feature_set_version="kmers_v1",
                model_version="baseline_v1",
            ),
            novelty_assessment=build_novelty(),
            actionability_features=build_actionability(),
            triage_decision=build_triage(),
            rationale_codes=build_triage().rationale_codes,
        )


def test_decision_object_rejects_nested_triage_job_drift() -> None:
    with pytest.raises(ValueError, match="same job_id"):
        DecisionObject(
            job_id="job_001",
            sample=build_sample(),
            assembly_qc=build_qc(),
            mechanistic_evidence=[],
            phenotype_prediction=build_prediction(),
            novelty_assessment=build_novelty(),
            actionability_features=build_actionability(),
            triage_decision=build_triage().model_copy(update={"job_id": "job_999"}),
            rationale_codes=build_triage().rationale_codes,
        )


def test_decision_object_accepts_complete_demo_case() -> None:
    decision = DecisionObject(
        job_id="job_001",
        sample=build_sample(),
        assembly_qc=build_qc(),
        mechanistic_evidence=[
            MechanisticEvidence(
                job_id="job_001",
                sample_id="sample_001",
                target_drug="tetracycline",
                gene_symbol="tetA",
                mechanism_class="efflux",
                drug_association=["tetracycline"],
                support_level="supported",
                interpretation="supporting signal present in normalized output",
                raw_artifact_id="artifact_001",
            )
        ],
        phenotype_prediction=build_prediction(),
        novelty_assessment=build_novelty(),
        actionability_features=build_actionability(),
        triage_decision=build_triage(),
        rationale_codes=build_triage().rationale_codes,
        artifact_manifest_id="manifest_001",
        provenance_notes=["fixture_case"],
    )

    assert decision.job_id == "job_001"
    assert decision.triage_decision.triage == TriageOutcome.DEFER_TO_LAB
    assert decision.rationale_codes[0] == RationaleCode.NO_SUPPORTED_MECHANISM


def test_decision_object_rejects_nested_novelty_target_drift() -> None:
    with pytest.raises(ValueError, match="All decision-layer contracts must reference the same target_drug"):
        DecisionObject(
            sample=build_sample(),
            assembly_qc=build_qc(),
            mechanistic_evidence=[],
            phenotype_prediction=build_prediction(),
            novelty_assessment=build_novelty().model_copy(update={"target_drug": "ampicillin"}),
            actionability_features=build_actionability(),
            triage_decision=build_triage(),
            rationale_codes=build_triage().rationale_codes,
        )


def test_job_decision_response_rejects_mismatched_context() -> None:
    decision = DecisionObject(
        job_id="job_001",
        sample=build_sample(),
        assembly_qc=build_qc(),
        phenotype_prediction=build_prediction(),
        novelty_assessment=build_novelty(),
        actionability_features=build_actionability(),
        triage_decision=build_triage(),
        rationale_codes=build_triage().rationale_codes,
    )

    with pytest.raises(ValueError, match="sample_id must match"):
        JobDecisionResponse(
            job_status=JobStatus(
                job_id="job_001",
                sample_id="sample_999",
                target_drug="tetracycline",
                status=JobState.DECISION_READY,
                current_step="decision_ready",
            ),
            decision=decision,
        )

    with pytest.raises(ValueError, match="job_id must match"):
        JobDecisionResponse(
            job_status=JobStatus(
                job_id="job_999",
                sample_id="sample_001",
                target_drug="tetracycline",
                status=JobState.DECISION_READY,
                current_step="decision_ready",
            ),
            decision=decision,
        )

    with pytest.raises(ValueError, match="target_drug must match"):
        JobDecisionResponse(
            job_status=JobStatus(
                job_id="job_001",
                sample_id="sample_001",
                target_drug="ampicillin",
                status=JobState.DECISION_READY,
                current_step="decision_ready",
            ),
            decision=decision,
        )


def test_job_decision_response_requires_decision_job_id() -> None:
    decision = DecisionObject(
        sample=build_sample(),
        assembly_qc=build_qc(),
        phenotype_prediction=build_prediction(),
        novelty_assessment=build_novelty(),
        actionability_features=build_actionability(),
        triage_decision=build_triage(),
        rationale_codes=build_triage().rationale_codes,
    )

    with pytest.raises(ValueError, match="decision job_id is required"):
        JobDecisionResponse(
            job_status=JobStatus(
                job_id="job_001",
                sample_id="sample_001",
                target_drug="tetracycline",
                status=JobState.DECISION_READY,
                current_step="decision_ready",
            ),
            decision=decision,
        )


def test_copilot_context_requires_sections() -> None:
    with pytest.raises(ValueError, match="CopilotContext requires at least one section"):
        CopilotContext(
            sample_id="sample_001",
            job_id="job_001",
            allowed_evidence_sources=[AllowedEvidenceSource.DECISION_OBJECT],
        )


def test_copilot_response_requires_citations_when_not_refusing() -> None:
    with pytest.raises(ValueError, match="Grounded copilot responses must cite at least one evidence ID"):
        CopilotResponse(
            job_id="job_001",
            sample_id="sample_001",
            target_drug="tetracycline",
            summary="This summary is not grounded yet but looks plausible enough.",
        )


def test_copilot_response_accepts_grounded_semantic_payload() -> None:
    response = CopilotResponse(
        job_id="job_001",
        sample_id="sample_001",
        target_drug="tetracycline",
        summary="The case should remain deferred because novelty is high and supported mechanism evidence is absent.",
        next_steps=[
            "confirm phenotype in laboratory workflow",
            "review assembly quality and coverage",
        ],
        cited_evidence_ids=["ev_001", "ev_002"],
        answer_blocks=[
            {
                "block_id": "summary_1",
                "block_type": "summary",
                "content": "The system keeps the case in manual review because supporting evidence remains incomplete.",
                "cited_evidence_ids": ["ev_001"],
            }
        ],
        semantic_ui=build_semantic_ui(),
    )

    assert response.semantic_ui is not None
    assert response.semantic_ui.queue_block is not None


def test_job_copilot_response_rejects_mismatched_sample_context() -> None:
    response = CopilotResponse(
        job_id="job_001",
        sample_id="sample_001",
        target_drug="tetracycline",
        summary="The case remains grounded in saved evidence and should stay in review.",
        cited_evidence_ids=["ev_001"],
    )

    with pytest.raises(ValueError, match="job_id must match"):
        JobCopilotResponse(
            job_status=JobStatus(
                job_id="job_999",
                sample_id="sample_001",
                target_drug="tetracycline",
                status=JobState.DECISION_READY,
                current_step="decision_ready",
            ),
            output_origin=CopilotOutputOrigin(mode=CopilotOutputMode.MOCK, provider="openrouter"),
            copilot=response,
        )

    with pytest.raises(ValueError, match="sample_id must match"):
        JobCopilotResponse(
            job_status=JobStatus(
                job_id="job_001",
                sample_id="sample_999",
                target_drug="tetracycline",
                status=JobState.DECISION_READY,
                current_step="decision_ready",
            ),
            output_origin=CopilotOutputOrigin(mode=CopilotOutputMode.MOCK, provider="openrouter"),
            copilot=response,
        )

    with pytest.raises(ValueError, match="target_drug must match"):
        JobCopilotResponse(
            job_status=JobStatus(
                job_id="job_001",
                sample_id="sample_001",
                target_drug="ampicillin",
                status=JobState.DECISION_READY,
                current_step="decision_ready",
            ),
            output_origin=CopilotOutputOrigin(mode=CopilotOutputMode.MOCK, provider="openrouter"),
            copilot=response,
        )


def test_artifact_manifest_and_queue_summary_match_runtime_shapes() -> None:
    manifest = ArtifactManifest(
        job_id="job_001",
        sample_id="sample_001",
        target_drug="tetracycline",
        artifacts=[
            ArtifactRecord(
                artifact_id="artifact_001",
                job_id="job_001",
                sample_id="sample_001",
                target_drug="tetracycline",
                kind=ArtifactKind.DECISION_OBJECT,
                path="artifacts/demo/decision.json",
                media_type="application/json",
                generated_by="pipeline_runner",
            )
        ],
    )
    status = JobStatus(
        job_id="job_001",
        sample_id="sample_001",
        target_drug="tetracycline",
        status=JobState.DECISION_READY,
        current_step="decision_ready",
    )
    queue = QueueSummaryResponse(
        items=[
            QueueItem(
                job_id="job_001",
                sample_id="sample_001",
                target_drug="tetracycline",
                triage="defer_to_lab",
                severity="high",
                status="decision_ready",
                queue_priority=10,
                headline="High novelty case awaiting analyst review",
                rationale_codes=["high_lineage_novelty"],
            )
        ]
    )
    accepted = AnalyzeJobResponse(job_id="job_001", status="queued")

    assert manifest.artifacts[0].kind == ArtifactKind.DECISION_OBJECT
    assert manifest.artifacts[0].target_drug == "tetracycline"
    assert status.status == JobState.DECISION_READY
    assert queue.items[0].triage == TriageOutcome.DEFER_TO_LAB
    assert accepted.status == JobState.QUEUED


def test_artifact_manifest_rejects_target_drift() -> None:
    with pytest.raises(ValueError, match="target_drug"):
        ArtifactManifest(
            job_id="job_001",
            sample_id="sample_001",
            target_drug="tetracycline",
            artifacts=[
                ArtifactRecord(
                    artifact_id="artifact_001",
                    job_id="job_001",
                    sample_id="sample_001",
                    target_drug="ampicillin",
                    kind=ArtifactKind.DECISION_OBJECT,
                    path="artifacts/demo/decision.json",
                    media_type="application/json",
                    generated_by="pipeline_runner",
                )
            ],
        )


def test_context_section_normalizes_grounding_ids() -> None:
    context = CopilotContext(
        sample_id="sample_001",
        job_id="job_001",
        allowed_evidence_sources=["decision_object", "mechanistic_evidence"],
        sections=[
            CopilotContextSection(
                section_id="Decision-1",
                section_type="decision",
                title="Decision Summary",
                content="The case is deferred because novelty is high and mechanism support is incomplete.",
                evidence_ids=["EV_001", "EV_002"],
            )
        ],
    )

    assert context.sections[0].section_id == "decision-1"
    assert context.sections[0].evidence_ids == ["ev_001", "ev_002"]
