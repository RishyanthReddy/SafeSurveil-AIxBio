from __future__ import annotations

from datetime import date

from app.contracts import (
    ActionabilityFeatures,
    ArtifactKind,
    ArtifactManifest,
    ArtifactRecord,
    AssemblyQC,
    CalibrationStatus,
    CopilotContext,
    DecisionObject,
    MechanisticEvidence,
    NoveltyAssessment,
    NoveltyBucket,
    OrganismConsistency,
    OrganismHint,
    PhenotypePrediction,
    PredictedPhenotype,
    QCStatus,
    RationaleCode,
    SampleInput,
    SampleMetadata,
    SeverityLevel,
    SourceContext,
    TriageDecision,
    TriageOutcome,
)
from app.llm.context import CopilotContextBuilder


def _build_decision() -> DecisionObject:
    sample = SampleInput(
        sample_id="sample_001",
        organism_hint=OrganismHint.E_COLI,
        target_drug="tetracycline",
        fasta_path="data/fixtures/sample.fa",
        metadata=SampleMetadata(
            accession="GCF_000005845.2",
            collection_date=date(2026, 4, 22),
            source_context=SourceContext.BOVINE_MILK,
            country="IN",
        ),
    )
    qc = AssemblyQC(
        job_id="job_001",
        sample_id="sample_001",
        target_drug="tetracycline",
        file_valid=True,
        sequence_count=10,
        total_bases=5012310,
        ambiguous_base_fraction=0.01,
        organism_consistency=OrganismConsistency.MATCH,
        missing_metadata_fields=[],
        qc_status=QCStatus.WARN,
        warnings=["coverage check pending"],
    )
    prediction = PhenotypePrediction(
        job_id="job_001",
        sample_id="sample_001",
        target_drug="tetracycline",
        predicted_phenotype=PredictedPhenotype.RESISTANT,
        probability=0.84,
        calibration_status=CalibrationStatus.NOT_AVAILABLE,
        uncertainty_score=0.16,
        feature_set_version="kmers_v1",
        model_version="baseline_v1",
        warnings=["calibration unavailable"],
    )
    novelty = NoveltyAssessment(
        job_id="job_001",
        sample_id="sample_001",
        target_drug="tetracycline",
        reference_snapshot_id="snapshot_2026_04_22",
        nearest_neighbor_id="ref_001",
        nearest_neighbor_distance=0.14,
        novelty_score=0.73,
        novelty_percentile=89.0,
        novelty_bucket=NoveltyBucket.HIGH,
        warnings=["reference panel limited"],
    )
    actionability = ActionabilityFeatures(
        job_id="job_001",
        sample_id="sample_001",
        target_drug="tetracycline",
        actionability_score=0.21,
        mechanism_concordance=False,
        prediction_entropy=0.33,
        qc_risk=0.25,
        novelty_risk=0.73,
        metadata_completeness=1.0,
        threshold_version="policy_v1",
        warnings=["novelty elevated"],
    )
    triage = TriageDecision(
        job_id="job_001",
        sample_id="sample_001",
        target_drug="tetracycline",
        triage=TriageOutcome.DEFER_TO_LAB,
        severity=SeverityLevel.HIGH,
        recommended_next_step="confirm phenotype in downstream review flow",
        threshold_version="policy_v1",
        rationale_codes=[
            RationaleCode.NO_SUPPORTED_MECHANISM,
            RationaleCode.HIGH_LINEAGE_NOVELTY,
            RationaleCode.MANUAL_CONFIRMATION_REQUIRED,
        ],
        warnings=["manual confirmation required"],
    )
    return DecisionObject(
        job_id="job_001",
        sample=sample,
        assembly_qc=qc,
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
        phenotype_prediction=prediction,
        novelty_assessment=novelty,
        actionability_features=actionability,
        triage_decision=triage,
        rationale_codes=triage.rationale_codes,
        warnings=["live evidence path present"],
        artifact_manifest_id="manifest_001",
        provenance_notes=["live_mode"],
    )


def _build_manifest() -> ArtifactManifest:
    return ArtifactManifest(
        job_id="job_001",
        sample_id="sample_001",
        target_drug="tetracycline",
        artifact_root="artifacts/runs/jobs/job_001",
        artifacts=[
            ArtifactRecord(
                artifact_id="artifact_001",
                job_id="job_001",
                sample_id="sample_001",
                target_drug="tetracycline",
                kind=ArtifactKind.MECHANISTIC_EVIDENCE,
                path="artifacts/runs/jobs/job_001/mechanism.tsv",
                media_type="text/tab-separated-values",
                generated_by="amrfinderplus_runner",
            ),
            ArtifactRecord(
                artifact_id="artifact_002",
                job_id="job_001",
                sample_id="sample_001",
                target_drug="tetracycline",
                kind=ArtifactKind.DECISION_OBJECT,
                path="artifacts/runs/jobs/job_001/decision.json",
                media_type="application/json",
                generated_by="decision_writer",
            ),
        ],
    )


def test_copilot_context_builder_creates_grounded_sections() -> None:
    context = CopilotContextBuilder(
        local_policy_notes=("Keep language evidence-bound.",),
    ).build(
        _build_decision(),
        artifact_manifest=_build_manifest(),
        user_question="Why was this case deferred?",
    )

    assert isinstance(context, CopilotContext)
    assert context.job_id == "job_001"
    assert context.sample_id == "sample_001"
    assert len(context.sections) == 7
    assert context.sections[0].section_id == "decision_summary"
    assert context.sections[-1].section_id == "question"
    assert any(
        source.value == "artifact_manifest" for source in context.allowed_evidence_sources
    )
    assert any("manual confirmation required" in warning for warning in context.warnings)


def test_copilot_context_builder_omits_private_threshold_notes_by_default() -> None:
    context = CopilotContextBuilder().build(
        _build_decision(),
        private_threshold_notes=("Internal threshold = 0.37",),
    )

    actionability_section = next(
        section for section in context.sections if section.section_id == "actionability_summary"
    )
    assert "Internal threshold = 0.37" not in actionability_section.content
    assert "threshold version" in actionability_section.content.lower()


def test_copilot_context_builder_can_include_private_threshold_notes_when_enabled() -> None:
    context = CopilotContextBuilder(include_private_thresholds=True).build(
        _build_decision(),
        private_threshold_notes=("Internal threshold = 0.37",),
    )

    actionability_section = next(
        section for section in context.sections if section.section_id == "actionability_summary"
    )
    assert "Internal threshold = 0.37" in actionability_section.content


def test_copilot_context_builder_records_absent_mechanistic_evidence_explicitly() -> None:
    decision = _build_decision().model_copy(update={"mechanistic_evidence": []})

    context = CopilotContextBuilder().build(decision)

    evidence_section = next(
        section for section in context.sections if section.section_id == "evidence_summary"
    )
    assert "No mechanistic evidence entries were recorded" in evidence_section.content
    assert evidence_section.evidence_ids == ["mechanistic_evidence__none"]


def test_copilot_context_builder_merges_extra_policy_warnings() -> None:
    context = CopilotContextBuilder().build(
        _build_decision(),
        extra_warnings=("Evidence policy: Artifact provenance is unavailable.",),
    )

    assert "Evidence policy: Artifact provenance is unavailable." in context.warnings
