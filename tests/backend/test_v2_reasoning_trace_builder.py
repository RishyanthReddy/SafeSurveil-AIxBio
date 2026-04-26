from __future__ import annotations

from datetime import date

import pytest

from app.contracts import (
    ActionabilityFeatures,
    AssemblyQC,
    CalibrationStatus,
    DecisionObject,
    MechanisticEvidence,
    NoveltyAssessment,
    NoveltyBucket,
    OrganismConsistency,
    OrganismHint,
    PhenotypePrediction,
    PredictedPhenotype,
    QCStatus,
    ReasoningTraceStepStatus,
    ReasoningTraceStepType,
    SampleInput,
    SampleMetadata,
    SourceContext,
    TriageDecision,
)
from app.services.reasoning_trace import build_reasoning_trace


def build_decision(
    *,
    triage: str = "review",
    severity: str = "high",
    recommended_next_step: str = "Route to analyst review with evidence and uncertainty context.",
    rationale_codes: list[str] | None = None,
    include_mechanism: bool = True,
    mechanism_support: str = "supported",
    mechanism_drug_association: bool = True,
    novelty_bucket: NoveltyBucket = NoveltyBucket.KNOWN,
    novelty_score: float = 0.12,
    novelty_uncertain: bool = False,
    qc_status: QCStatus = QCStatus.PASS,
    qc_risk: float = 0.0,
    metadata_completeness: float = 1.0,
    missing_metadata_fields: list[str] | None = None,
    actionability_score: float = 0.86,
) -> DecisionObject:
    sample = SampleInput(
        sample_id="sample_001",
        organism_hint=OrganismHint.E_COLI,
        target_drug="tetracycline",
        fasta_path="data/fixtures/sample.fa",
        metadata=SampleMetadata(
            accession="acc_001",
            collection_date=date(2026, 4, 20),
            source_context=SourceContext.SURVEILLANCE_PROXY,
        ),
    )
    triage_decision = TriageDecision(
        job_id="job_001",
        sample_id="sample_001",
        target_drug="tetracycline",
        triage=triage,
        severity=severity,
        recommended_next_step=recommended_next_step,
        threshold_version="policy_v1",
        rationale_codes=rationale_codes
        or ["actionability_threshold_met", "concordant_signal_present", "supported_mechanism_present"],
    )
    mechanistic_evidence = (
        [
            MechanisticEvidence(
                job_id="job_001",
                sample_id="sample_001",
                target_drug="tetracycline",
                gene_symbol="tetB",
                mechanism_class="efflux",
                drug_association=["tetracycline"] if mechanism_drug_association else [],
                support_level=mechanism_support,
                interpretation="Detected tetB in the persisted AMRFinderPlus evidence.",
            )
        ]
        if include_mechanism
        else []
    )
    return DecisionObject(
        job_id="job_001",
        sample=sample,
        assembly_qc=AssemblyQC(
            job_id="job_001",
            sample_id="sample_001",
            target_drug="tetracycline",
            file_valid=True,
            sequence_count=4,
            total_bases=5000,
            ambiguous_base_fraction=0.01,
            organism_consistency=OrganismConsistency.MATCH,
            missing_metadata_fields=missing_metadata_fields or [],
            qc_status=qc_status,
        ),
        mechanistic_evidence=mechanistic_evidence,
        phenotype_prediction=PhenotypePrediction(
            job_id="job_001",
            sample_id="sample_001",
            target_drug="tetracycline",
            predicted_phenotype=PredictedPhenotype.RESISTANT,
            probability=0.953,
            calibration_status=CalibrationStatus.NOT_AVAILABLE,
            uncertainty_score=0.27,
            feature_set_version="features_v1",
            model_version="baseline_v1",
        ),
        novelty_assessment=NoveltyAssessment(
            job_id="job_001",
            sample_id="sample_001",
            target_drug="tetracycline",
            reference_snapshot_id="reference_snapshot_v1",
            nearest_neighbor_id="reference_ec_001",
            nearest_neighbor_distance=0.12,
            novelty_score=novelty_score,
            novelty_percentile=72.0,
            novelty_bucket=novelty_bucket,
            uncertainty_flag=novelty_uncertain,
        ),
        actionability_features=ActionabilityFeatures(
            job_id="job_001",
            sample_id="sample_001",
            target_drug="tetracycline",
            actionability_score=actionability_score,
            mechanism_concordance=include_mechanism and mechanism_support in {"supported", "partial"},
            prediction_entropy=0.27,
            qc_risk=qc_risk,
            novelty_risk=novelty_score,
            metadata_completeness=metadata_completeness,
            threshold_version="policy_v1",
        ),
        triage_decision=triage_decision,
        rationale_codes=triage_decision.rationale_codes,
    )


def step_by_type(trace, step_type: ReasoningTraceStepType):
    return next(step for step in trace.steps if step.step_type == step_type)


def test_reasoning_trace_builder_emits_required_order_and_refs() -> None:
    trace = build_reasoning_trace(build_decision())

    assert [step.step_type for step in trace.steps] == list(trace.coverage.required_step_types)
    assert [step.step_number for step in trace.steps] == list(range(1, 9))
    assert trace.coverage.coverage_ratio == pytest.approx(1.0)
    assert trace.metadata["provider_calls_triggered"] is False
    assert all(step.evidence_refs for step in trace.steps)
    assert trace.steps[0].evidence_refs[0].evidence_id == "decision_object__summary"


@pytest.mark.parametrize(
    ("triage", "severity", "next_step"),
    [
        ("act", "low", "Escalate as an actionable case with analyst oversight."),
        ("review", "medium", "Route to analyst review with evidence and uncertainty context."),
        ("defer_to_lab", "high", "Defer decision until confirmation or additional evidence is available."),
    ],
)
def test_reasoning_trace_builder_preserves_triage_variants(
    triage: str,
    severity: str,
    next_step: str,
) -> None:
    trace = build_reasoning_trace(
        build_decision(
            triage=triage,
            severity=severity,
            recommended_next_step=next_step,
            rationale_codes=[
                "actionability_threshold_not_met",
                "manual_confirmation_required",
            ]
            if triage == "defer_to_lab"
            else ["actionability_threshold_met", "concordant_signal_present"],
        )
    )

    final_step = step_by_type(trace, ReasoningTraceStepType.FINAL_TRIAGE)
    assert trace.decision.value == triage
    assert trace.severity.value == severity
    assert next_step in final_step.text
    assert triage.replace("_", " ") in final_step.text


def test_reasoning_trace_builder_handles_missing_mechanism_without_invention() -> None:
    trace = build_reasoning_trace(
        build_decision(
            include_mechanism=False,
            rationale_codes=["no_supported_mechanism", "manual_confirmation_required"],
        )
    )
    mechanism_step = step_by_type(trace, ReasoningTraceStepType.MECHANISTIC_EVIDENCE)
    interpretation_step = step_by_type(trace, ReasoningTraceStepType.MECHANISM_DRUG_INTERPRETATION)

    assert mechanism_step.status == ReasoningTraceStepStatus.CAVEATED
    assert "No mechanistic evidence rows are persisted" in mechanism_step.text
    assert mechanism_step.evidence_refs[0].evidence_id == "mechanistic_evidence__none"
    assert "mechanism_missing_or_weak" in mechanism_step.caveat_ids
    assert "cannot independently justify action" in interpretation_step.text
    assert "tetB" not in mechanism_step.text


def test_reasoning_trace_builder_marks_high_novelty_caveat() -> None:
    trace = build_reasoning_trace(
        build_decision(
            novelty_bucket=NoveltyBucket.HIGH,
            novelty_score=1.0,
            rationale_codes=["high_lineage_novelty", "manual_confirmation_required"],
        )
    )
    novelty_step = step_by_type(trace, ReasoningTraceStepType.NOVELTY_LINEAGE_SHIFT)

    assert novelty_step.status == ReasoningTraceStepStatus.CAVEATED
    assert "novelty_uncertainty" in novelty_step.caveat_ids
    assert "bucket high" in novelty_step.text
    assert any(caveat.caveat_id == "novelty_uncertainty" for caveat in trace.caveats)


def test_reasoning_trace_builder_marks_qc_warning_caveat() -> None:
    trace = build_reasoning_trace(
        build_decision(
            qc_status=QCStatus.WARN,
            qc_risk=0.4,
            rationale_codes=["qc_warning_present", "manual_confirmation_required"],
        )
    )
    qc_step = step_by_type(trace, ReasoningTraceStepType.QC_METADATA_LIMITATIONS)

    assert qc_step.status == ReasoningTraceStepStatus.CAVEATED
    assert "qc_warning_present" in qc_step.caveat_ids
    assert "QC risk 40%" in qc_step.text
    assert any(caveat.caveat_id == "qc_warning_present" for caveat in trace.caveats)


def test_reasoning_trace_builder_marks_missing_metadata_caveat() -> None:
    trace = build_reasoning_trace(
        build_decision(
            metadata_completeness=0.0,
            missing_metadata_fields=["collection_date"],
            rationale_codes=["metadata_incomplete", "manual_confirmation_required"],
        )
    )
    qc_step = step_by_type(trace, ReasoningTraceStepType.QC_METADATA_LIMITATIONS)

    assert qc_step.status == ReasoningTraceStepStatus.CAVEATED
    assert "metadata_incomplete" in qc_step.caveat_ids
    assert "metadata completeness is 0%" in qc_step.text
    assert "collection_date" in qc_step.text
    assert any(caveat.caveat_id == "metadata_incomplete" for caveat in trace.caveats)
