from __future__ import annotations

from datetime import date
import re

import pytest

from app.contracts import (
    ActionabilityFeatures,
    ArtifactKind,
    ArtifactManifest,
    ArtifactRecord,
    AssemblyQC,
    CalibrationStatus,
    CopilotAnswerBlock,
    CopilotResponse,
    DecisionCardBlock,
    DecisionObject,
    EvidenceTableBlock,
    EvidenceTableRow,
    ExecutionGateCheckStatus,
    ExecutionGateDecision,
    MechanisticEvidence,
    MetricDatum,
    NoveltyAssessment,
    NoveltyBucket,
    OrganismConsistency,
    OrganismHint,
    PhenotypePrediction,
    PredictedPhenotype,
    QCStatus,
    RiskChartBlock,
    RiskChartPoint,
    SafetyProfileAxis,
    SafetyProfileBlock,
    SampleInput,
    SampleMetadata,
    SemanticUIObject,
    SourceContext,
    TriageDecision,
    TriageOutcome,
)
from app.services.reasoning_trace import build_reasoning_trace
from app.services.verification import (
    build_audit_digest_bundle,
    build_audit_fingerprint,
    build_evidence_citation_checks,
    build_execution_gate_report,
    build_identity_numeric_checks,
    build_policy_hash,
    build_policy_alignment_checks,
    build_reasoning_trace_checks,
)

_SHA256_PATTERN = re.compile(r"^sha256:[a-f0-9]{64}$")


def build_decision(
    *,
    triage: str = "review",
    severity: str = "high",
    recommended_next_step: str = "Route to analyst review with evidence and uncertainty context.",
    rationale_codes: list[str] | None = None,
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
    triage = TriageDecision(
        job_id="job_001",
        sample_id="sample_001",
        target_drug="tetracycline",
        triage=triage,
        severity=severity,
        recommended_next_step=recommended_next_step,
        threshold_version="policy_v1",
        rationale_codes=rationale_codes or ["high_lineage_novelty", "manual_confirmation_required"],
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
            qc_status=QCStatus.WARN,
        ),
        mechanistic_evidence=[
            MechanisticEvidence(
                job_id="job_001",
                sample_id="sample_001",
                target_drug="tetracycline",
                gene_symbol="tetB",
                mechanism_class="efflux",
                support_level="supported",
                interpretation="Detected tetB in the smoke evidence path.",
            )
        ],
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
            novelty_score=1.0,
            novelty_percentile=100.0,
            novelty_bucket=NoveltyBucket.HIGH,
        ),
        actionability_features=ActionabilityFeatures(
            job_id="job_001",
            sample_id="sample_001",
            target_drug="tetracycline",
            actionability_score=0.535,
            mechanism_concordance=True,
            prediction_entropy=0.27,
            qc_risk=0.4,
            novelty_risk=1.0,
            metadata_completeness=0.0,
            threshold_version="policy_v1",
        ),
        triage_decision=triage,
        rationale_codes=triage.rationale_codes,
    )


def build_semantic_ui(
    *,
    probability: float = 0.953,
    actionability: float = 0.535,
    novelty: float = 1.0,
    qc_risk: float = 0.4,
    metadata: float = 0.0,
    triage: str = "review",
    severity: str = "high",
    evidence_id: str | None = None,
    evidence_label: str = "tetB efflux",
    evidence_detail: str = "Detected tetB in the smoke evidence path.",
) -> SemanticUIObject:
    return SemanticUIObject(
        decision_card=DecisionCardBlock(
            title="Decision Overview",
            triage_decision=triage,
            severity=severity,
            summary="Review case with evidence and uncertainty context.",
            metrics=[
                MetricDatum(key="probability", label="Probability", value=probability),
                MetricDatum(key="actionability_score", label="Actionability", value=actionability),
                MetricDatum(key="novelty_score", label="Novelty Score", value=novelty),
            ],
        ),
        risk_charts=[
            RiskChartBlock(
                chart_id="risk_overview",
                title="Risk Overview",
                chart_type="bar",
                points=[
                    RiskChartPoint(label="QC Risk", value=qc_risk),
                ],
            )
        ],
        safety_profile=SafetyProfileBlock(
            title="Safety Profile",
            axes=[SafetyProfileAxis(label="Metadata Completeness", value=metadata)],
        ),
        evidence_table=(
            EvidenceTableBlock(
                title="Evidence Summary",
                columns=["signal", "detail", "support"],
                rows=[
                    EvidenceTableRow(
                        row_id="mechanism_001",
                        label=evidence_label,
                        cells={
                            "signal": evidence_label,
                            "detail": evidence_detail,
                            "support": "supported",
                        },
                        evidence_id=evidence_id,
                    )
                ],
            )
            if evidence_id is not None
            else None
        ),
    )


def build_copilot(
    *,
    job_id: str = "job_001",
    cited_evidence_ids: list[str] | None = None,
    summary: str = "Grounded summary cites the persisted decision and evidence.",
    next_steps: list[str] | None = None,
    answer_content: str = "Mechanistic evidence and novelty are cited for review.",
) -> CopilotResponse:
    return CopilotResponse(
        job_id=job_id,
        sample_id="sample_001",
        target_drug="tetracycline",
        summary=summary,
        next_steps=(
            next_steps
            if next_steps is not None
            else ["Route to analyst review with evidence and uncertainty context."]
        ),
        cited_evidence_ids=cited_evidence_ids or ["decision_object__summary"],
        answer_blocks=[
            CopilotAnswerBlock(
                block_id="evidence_summary",
                block_type="summary",
                title="Evidence summary",
                content=answer_content,
                cited_evidence_ids=["mechanistic_evidence__1", "novelty_assessment__summary"],
            )
        ],
    )


def build_artifact_manifest() -> ArtifactManifest:
    return ArtifactManifest(
        job_id="job_001",
        sample_id="sample_001",
        target_drug="tetracycline",
        artifacts=[
            ArtifactRecord(
                artifact_id="job_001_amrfinder_raw",
                job_id="job_001",
                sample_id="sample_001",
                target_drug="tetracycline",
                kind=ArtifactKind.MECHANISTIC_EVIDENCE,
                path="artifacts/runs/jobs/job_001/evidence/sample_001.amrfinder.tsv",
                media_type="text/tab-separated-values",
                generated_by="amrfinderplus",
            )
        ],
    )


def check_statuses(result) -> dict[str, ExecutionGateCheckStatus]:
    return {check.check_id: check.status for check in result.checks}


def test_identity_numeric_checks_allow_exact_match() -> None:
    result = build_identity_numeric_checks(
        build_decision(),
        semantic_ui=build_semantic_ui(),
        copilot=build_copilot(),
    )

    assert result.gate_decision == ExecutionGateDecision.ALLOW
    assert result.numeric_consistency.consistency_ratio == pytest.approx(1.0)
    assert all(check.status == ExecutionGateCheckStatus.PASS for check in result.checks)


def test_identity_numeric_checks_allow_display_rounding_and_percent_values() -> None:
    result = build_identity_numeric_checks(
        build_decision(),
        semantic_ui=build_semantic_ui(
            probability=95.0,
            actionability=54.0,
            novelty=100.0,
            qc_risk=40.0,
            metadata=0.0,
        ),
        copilot=build_copilot(),
    )

    assert result.gate_decision == ExecutionGateDecision.ALLOW
    assert result.numeric_consistency.matched_fields == [
        "probability",
        "actionability_score",
        "novelty_score",
        "qc_risk",
        "metadata_completeness",
    ]


def test_identity_numeric_checks_block_swapped_metric_values() -> None:
    result = build_identity_numeric_checks(
        build_decision(),
        semantic_ui=build_semantic_ui(probability=1.0, novelty=0.953),
        copilot=build_copilot(),
    )
    statuses = check_statuses(result)

    assert result.gate_decision == ExecutionGateDecision.BLOCK
    assert statuses["numeric_probability"] == ExecutionGateCheckStatus.FAIL
    assert statuses["numeric_novelty_score"] == ExecutionGateCheckStatus.FAIL


def test_identity_numeric_checks_block_wrong_triage() -> None:
    result = build_identity_numeric_checks(
        build_decision(),
        semantic_ui=build_semantic_ui(triage="defer_to_lab"),
        copilot=build_copilot(),
    )

    assert result.gate_decision == ExecutionGateDecision.BLOCK
    assert check_statuses(result)["decision_card_triage_decision"] == ExecutionGateCheckStatus.FAIL


def test_identity_numeric_checks_block_wrong_severity() -> None:
    result = build_identity_numeric_checks(
        build_decision(),
        semantic_ui=build_semantic_ui(severity="critical"),
        copilot=build_copilot(),
    )

    assert result.gate_decision == ExecutionGateDecision.BLOCK
    assert check_statuses(result)["decision_card_severity"] == ExecutionGateCheckStatus.FAIL


def test_identity_numeric_checks_block_wrong_copilot_job_id() -> None:
    result = build_identity_numeric_checks(
        build_decision(),
        semantic_ui=build_semantic_ui(),
        copilot=build_copilot(job_id="job_999"),
    )

    assert result.gate_decision == ExecutionGateDecision.BLOCK
    assert check_statuses(result)["copilot_job_id"] == ExecutionGateCheckStatus.FAIL


def test_evidence_citation_checks_allow_complete_grounded_coverage() -> None:
    result = build_evidence_citation_checks(
        build_decision(),
        artifact_manifest=build_artifact_manifest(),
        semantic_ui=build_semantic_ui(evidence_id="mechanistic_evidence__1"),
        copilot=build_copilot(
            cited_evidence_ids=[
                "decision_object__summary",
                "decision_object__triage",
                "decision_object__warnings",
                "phenotype_prediction__summary",
                "actionability_features__summary",
                "novelty_assessment__summary",
            ]
        ),
    )

    assert result.gate_decision == ExecutionGateDecision.ALLOW
    assert result.evidence_coverage.coverage_ratio == pytest.approx(1.0)
    assert result.citation_validity.validity_ratio == pytest.approx(1.0)
    assert all(check.status == ExecutionGateCheckStatus.PASS for check in result.checks)


def test_evidence_citation_checks_count_decision_card_as_triage_citation() -> None:
    result = build_evidence_citation_checks(
        build_decision(),
        artifact_manifest=build_artifact_manifest(),
        semantic_ui=build_semantic_ui(evidence_id="mechanistic_evidence__1"),
        copilot=build_copilot(
            cited_evidence_ids=[
                "decision_object__summary",
                "decision_object__warnings",
                "phenotype_prediction__summary",
                "actionability_features__summary",
                "novelty_assessment__summary",
            ]
        ),
    )

    assert result.gate_decision == ExecutionGateDecision.ALLOW
    assert "decision_object__triage" not in result.evidence_coverage.missing_evidence_ids


def test_evidence_citation_checks_block_invented_citation() -> None:
    result = build_evidence_citation_checks(
        build_decision(),
        semantic_ui=build_semantic_ui(),
        copilot=build_copilot(cited_evidence_ids=["invented_evidence_123"]),
    )

    assert result.gate_decision == ExecutionGateDecision.BLOCK
    assert result.citation_validity.invalid_evidence_ids == ["invented_evidence_123"]
    assert check_statuses(result)["citation_ids_allowed"] == ExecutionGateCheckStatus.FAIL


def test_evidence_citation_checks_review_missing_required_coverage() -> None:
    result = build_evidence_citation_checks(
        build_decision(),
        semantic_ui=None,
        copilot=build_copilot(cited_evidence_ids=["decision_object__summary"]),
    )

    assert result.gate_decision == ExecutionGateDecision.REVIEW
    assert "decision_object__triage" in result.evidence_coverage.missing_evidence_ids
    assert check_statuses(result)["required_evidence_coverage"] == ExecutionGateCheckStatus.WARN


def test_evidence_citation_checks_block_semantic_ui_invented_evidence_id() -> None:
    result = build_evidence_citation_checks(
        build_decision(),
        semantic_ui=build_semantic_ui(evidence_id="invented_evidence_123"),
        copilot=build_copilot(cited_evidence_ids=["decision_object__summary"]),
    )

    assert result.gate_decision == ExecutionGateDecision.BLOCK
    assert result.citation_validity.invalid_evidence_ids == ["invented_evidence_123"]


def test_evidence_citation_checks_block_mechanism_row_drift() -> None:
    result = build_evidence_citation_checks(
        build_decision(),
        semantic_ui=build_semantic_ui(
            evidence_id="mechanistic_evidence__1",
            evidence_label="tetA efflux",
            evidence_detail="Detected tetA in the generated semantic UI row.",
        ),
        copilot=build_copilot(
            cited_evidence_ids=[
                "decision_object__summary",
                "decision_object__triage",
                "decision_object__warnings",
                "phenotype_prediction__summary",
                "actionability_features__summary",
                "novelty_assessment__summary",
            ]
        ),
    )

    assert result.gate_decision == ExecutionGateDecision.BLOCK
    assert check_statuses(result)["semantic_mechanism_row_0"] == ExecutionGateCheckStatus.FAIL


def test_policy_alignment_checks_allow_neutral_valid_summary() -> None:
    result = build_policy_alignment_checks(
        build_decision(),
        semantic_ui=build_semantic_ui(),
        copilot=build_copilot(
            summary="The case remains in analyst review because novelty and QC limits need interpretation.",
            next_steps=["Route to analyst review with evidence and uncertainty context."],
        ),
    )

    assert result.gate_decision == ExecutionGateDecision.ALLOW
    assert result.policy_alignment.triage_matches_decision is True
    assert result.policy_alignment.next_step_matches_decision is True
    assert result.policy_alignment.unsafe_claims_detected is False
    assert all(check.status == ExecutionGateCheckStatus.PASS for check in result.checks)


def test_next_step_matching_allows_case_and_terminal_punctuation() -> None:
    expected_next_step = "defer decision until confirmation or additional evidence is available"
    observed_next_step = "Defer decision until confirmation or additional evidence is available."
    decision = build_decision(
        triage="defer_to_lab",
        recommended_next_step=expected_next_step,
        rationale_codes=["manual_confirmation_required"],
    )
    semantic_ui = build_semantic_ui(triage="defer_to_lab")
    copilot = build_copilot(next_steps=[observed_next_step])

    identity_result = build_identity_numeric_checks(
        decision,
        semantic_ui=semantic_ui,
        copilot=copilot,
    )
    policy_result = build_policy_alignment_checks(
        decision,
        semantic_ui=semantic_ui,
        copilot=copilot,
    )

    assert check_statuses(identity_result)["copilot_next_step"] == ExecutionGateCheckStatus.PASS
    assert check_statuses(policy_result)["policy_next_step_alignment"] == ExecutionGateCheckStatus.PASS


def test_policy_alignment_checks_block_action_override() -> None:
    result = build_policy_alignment_checks(
        build_decision(),
        semantic_ui=build_semantic_ui(),
        copilot=build_copilot(
            summary="The case is safe to act now and proceed with operational action.",
            next_steps=["Route to analyst review with evidence and uncertainty context."],
        ),
    )

    assert result.gate_decision == ExecutionGateDecision.BLOCK
    assert result.policy_alignment.unsafe_claims_detected is True
    assert "action_override_detected" in result.policy_alignment.notes
    assert check_statuses(result)["policy_action_override"] == ExecutionGateCheckStatus.FAIL


def test_policy_alignment_checks_block_lab_confirmation_bypass() -> None:
    next_step = "Defer decision until confirmation or additional evidence is available."
    result = build_policy_alignment_checks(
        build_decision(
            triage="defer_to_lab",
            recommended_next_step=next_step,
            rationale_codes=["manual_confirmation_required"],
        ),
        semantic_ui=build_semantic_ui(triage="defer_to_lab"),
        copilot=build_copilot(
            summary="No lab confirmation is required before using this result.",
            next_steps=[next_step],
        ),
    )

    assert result.gate_decision == ExecutionGateDecision.BLOCK
    assert "lab_confirmation_bypass_detected" in result.policy_alignment.notes
    assert check_statuses(result)["policy_lab_confirmation_boundary"] == ExecutionGateCheckStatus.FAIL


def test_policy_alignment_checks_review_high_confidence_overclaim() -> None:
    result = build_policy_alignment_checks(
        build_decision(),
        semantic_ui=build_semantic_ui(),
        copilot=build_copilot(
            summary="This is a definitive clinically validated resistance result.",
            next_steps=["Route to analyst review with evidence and uncertainty context."],
        ),
    )

    assert result.gate_decision == ExecutionGateDecision.REVIEW
    assert result.policy_alignment.unsafe_claims_detected is False
    assert "certainty_overclaim_detected" in result.policy_alignment.notes
    assert check_statuses(result)["policy_certainty_language"] == ExecutionGateCheckStatus.WARN


def test_policy_hash_is_stable_sorted_and_policy_sensitive() -> None:
    result = build_policy_alignment_checks(
        build_decision(),
        semantic_ui=build_semantic_ui(),
        copilot=build_copilot(),
    )
    reversed_checks = tuple(reversed(result.checks))

    policy_hash = build_policy_hash(policy_version="policy_v1", check_definitions=result.checks)
    reordered_policy_hash = build_policy_hash(policy_version="policy_v1", check_definitions=reversed_checks)
    changed_policy_hash = build_policy_hash(policy_version="policy_v2", check_definitions=result.checks)

    assert _SHA256_PATTERN.fullmatch(policy_hash)
    assert policy_hash == reordered_policy_hash
    assert policy_hash != changed_policy_hash


def test_policy_hash_uses_definitions_not_case_outcomes() -> None:
    result = build_policy_alignment_checks(
        build_decision(),
        semantic_ui=build_semantic_ui(),
        copilot=build_copilot(),
    )
    changed_outcome_checks = tuple(
        check.model_copy(update={"status": ExecutionGateCheckStatus.FAIL})
        for check in result.checks
    )

    assert build_policy_hash(policy_version="policy_v1", check_definitions=result.checks) == build_policy_hash(
        policy_version="policy_v1",
        check_definitions=changed_outcome_checks,
    )


def test_audit_fingerprint_is_stable_and_ignores_secret_or_volatile_metadata() -> None:
    policy_result = build_policy_alignment_checks(
        build_decision(),
        semantic_ui=build_semantic_ui(),
        copilot=build_copilot(),
    )
    policy_hash = build_policy_hash(
        policy_version="policy_v1",
        check_definitions=policy_result.checks,
    )

    fingerprint = build_audit_fingerprint(
        job_id="job_001",
        sample_id="sample_001",
        target_drug="tetracycline",
        gate_decision=policy_result.gate_decision,
        checks=policy_result.checks,
        policy_hash=policy_hash,
        policy_alignment=policy_result.policy_alignment,
        metadata={
            "operator_note": "same benign note",
            "created_at": "2026-04-25T01:00:00Z",
            "api_key": "secret-one",
        },
    )
    same_effective_payload = build_audit_fingerprint(
        job_id="job_001",
        sample_id="sample_001",
        target_drug="tetracycline",
        gate_decision=policy_result.gate_decision,
        checks=policy_result.checks,
        policy_hash=policy_hash,
        policy_alignment=policy_result.policy_alignment,
        metadata={
            "operator_note": "same benign note",
            "created_at": "2026-04-25T02:00:00Z",
            "api_key": "secret-two",
        },
    )
    changed_benign_payload = build_audit_fingerprint(
        job_id="job_001",
        sample_id="sample_001",
        target_drug="tetracycline",
        gate_decision=policy_result.gate_decision,
        checks=policy_result.checks,
        policy_hash=policy_hash,
        policy_alignment=policy_result.policy_alignment,
        metadata={"operator_note": "different benign note"},
    )

    assert _SHA256_PATTERN.fullmatch(fingerprint)
    assert fingerprint == same_effective_payload
    assert fingerprint != changed_benign_payload


def test_audit_fingerprint_changes_when_evidence_summary_changes() -> None:
    complete_evidence = build_evidence_citation_checks(
        build_decision(),
        semantic_ui=build_semantic_ui(evidence_id="mechanistic_evidence__1"),
        copilot=build_copilot(
            cited_evidence_ids=[
                "decision_object__summary",
                "decision_object__triage",
                "decision_object__warnings",
                "phenotype_prediction__summary",
                "actionability_features__summary",
                "novelty_assessment__summary",
            ]
        ),
    )
    partial_evidence = build_evidence_citation_checks(
        build_decision(),
        semantic_ui=build_semantic_ui(),
        copilot=build_copilot(cited_evidence_ids=["decision_object__summary"]),
    )
    policy_hash = build_policy_hash(
        policy_version="policy_v1",
        check_definitions=complete_evidence.checks,
    )

    complete_fingerprint = build_audit_fingerprint(
        job_id="job_001",
        sample_id="sample_001",
        target_drug="tetracycline",
        gate_decision=complete_evidence.gate_decision,
        checks=complete_evidence.checks,
        policy_hash=policy_hash,
        evidence_coverage=complete_evidence.evidence_coverage,
        citation_validity=complete_evidence.citation_validity,
    )
    partial_fingerprint = build_audit_fingerprint(
        job_id="job_001",
        sample_id="sample_001",
        target_drug="tetracycline",
        gate_decision=partial_evidence.gate_decision,
        checks=partial_evidence.checks,
        policy_hash=policy_hash,
        evidence_coverage=partial_evidence.evidence_coverage,
        citation_validity=partial_evidence.citation_validity,
    )

    assert complete_fingerprint != partial_fingerprint


def test_audit_digest_bundle_returns_policy_hash_and_fingerprint() -> None:
    policy_result = build_policy_alignment_checks(
        build_decision(),
        semantic_ui=build_semantic_ui(),
        copilot=build_copilot(),
    )
    bundle = build_audit_digest_bundle(
        job_id="job_001",
        sample_id="sample_001",
        target_drug="tetracycline",
        gate_decision=policy_result.gate_decision,
        checks=policy_result.checks,
        policy_version="policy_v1",
        policy_alignment=policy_result.policy_alignment,
    )

    assert _SHA256_PATTERN.fullmatch(bundle.policy_hash)
    assert _SHA256_PATTERN.fullmatch(bundle.audit_fingerprint)


def test_reasoning_trace_checks_allow_deterministic_trace() -> None:
    decision = build_decision()
    trace = build_reasoning_trace(decision)

    result = build_reasoning_trace_checks(decision, trace=trace)

    assert result.gate_decision == ExecutionGateDecision.ALLOW
    assert all(check.status == ExecutionGateCheckStatus.PASS for check in result.checks)
    assert check_statuses(result)["trace_required_steps_present"] == ExecutionGateCheckStatus.PASS


def test_reasoning_trace_checks_block_missing_required_step() -> None:
    decision = build_decision()
    trace = build_reasoning_trace(decision)
    broken_trace = trace.model_copy(update={"steps": trace.steps[:-1]})

    result = build_reasoning_trace_checks(decision, trace=broken_trace)

    statuses = check_statuses(result)
    assert result.gate_decision == ExecutionGateDecision.BLOCK
    assert statuses["trace_required_steps_present"] == ExecutionGateCheckStatus.FAIL
    assert statuses["trace_coverage_matches_steps"] == ExecutionGateCheckStatus.FAIL


def test_reasoning_trace_checks_block_missing_step_evidence_ref() -> None:
    decision = build_decision()
    trace = build_reasoning_trace(decision)
    first_step_without_refs = trace.steps[0].model_copy(update={"evidence_refs": []})
    broken_trace = trace.model_copy(update={"steps": [first_step_without_refs, *trace.steps[1:]]})

    result = build_reasoning_trace_checks(decision, trace=broken_trace)

    assert result.gate_decision == ExecutionGateDecision.BLOCK
    assert check_statuses(result)["trace_step_evidence_refs"] == ExecutionGateCheckStatus.FAIL


def test_reasoning_trace_checks_block_triage_mismatch() -> None:
    decision = build_decision()
    trace = build_reasoning_trace(decision)
    broken_trace = trace.model_copy(update={"decision": TriageOutcome.ACT})

    result = build_reasoning_trace_checks(decision, trace=broken_trace)

    assert result.gate_decision == ExecutionGateDecision.BLOCK
    assert check_statuses(result)["trace_identity_matches_decision"] == ExecutionGateCheckStatus.FAIL


def test_execution_gate_report_composes_all_verifier_sections() -> None:
    decision = build_decision()
    semantic_ui = build_semantic_ui(evidence_id="mechanistic_evidence__1")
    copilot = build_copilot(
        cited_evidence_ids=[
            "decision_object__summary",
            "decision_object__triage",
            "decision_object__warnings",
            "phenotype_prediction__summary",
            "actionability_features__summary",
            "novelty_assessment__summary",
        ]
    )
    report = build_execution_gate_report(
        decision,
        artifact_manifest=build_artifact_manifest(),
        semantic_ui=semantic_ui,
        copilot=copilot,
        reasoning_trace=build_reasoning_trace(decision),
        metadata={"provider_calls_triggered": False},
    )

    assert report.gate_decision == ExecutionGateDecision.ALLOW
    assert report.evidence_coverage.coverage_ratio == pytest.approx(1.0)
    assert report.numeric_consistency.consistency_ratio == pytest.approx(1.0)
    assert report.citation_validity.validity_ratio == pytest.approx(1.0)
    assert report.policy_alignment.unsafe_claims_detected is False
    assert _SHA256_PATTERN.fullmatch(report.policy_hash)
    assert _SHA256_PATTERN.fullmatch(report.audit_fingerprint)
    assert report.metadata["provider_calls_triggered"] is False
    assert check_statuses(report)["trace_required_steps_present"] == ExecutionGateCheckStatus.PASS
    assert report.issues == []


def test_execution_gate_report_blocks_broken_reasoning_trace() -> None:
    decision = build_decision()
    trace = build_reasoning_trace(decision)
    broken_trace = trace.model_copy(update={"steps": trace.steps[:-1]})
    report = build_execution_gate_report(
        decision,
        semantic_ui=build_semantic_ui(evidence_id="mechanistic_evidence__1"),
        copilot=build_copilot(
            cited_evidence_ids=[
                "decision_object__summary",
                "decision_object__triage",
                "decision_object__warnings",
                "phenotype_prediction__summary",
                "actionability_features__summary",
                "novelty_assessment__summary",
            ]
        ),
        reasoning_trace=broken_trace,
    )

    assert report.gate_decision == ExecutionGateDecision.BLOCK
    assert check_statuses(report)["trace_required_steps_present"] == ExecutionGateCheckStatus.FAIL
    assert any(issue.issue_id == "issue_trace_required_steps_present" for issue in report.issues)


def test_execution_gate_report_surfaces_warning_and_blocking_issues() -> None:
    report = build_execution_gate_report(
        build_decision(),
        semantic_ui=build_semantic_ui(triage="defer_to_lab"),
        copilot=build_copilot(cited_evidence_ids=["decision_object__summary"]),
    )

    assert report.gate_decision == ExecutionGateDecision.BLOCK
    assert any(issue.severity.value == "blocking" for issue in report.issues)
    assert any(issue.severity.value == "warning" for issue in report.issues)
    assert {issue.issue_id for issue in report.issues} == {
        f"issue_{check.check_id}"
        for check in report.checks
        if check.status != ExecutionGateCheckStatus.PASS
    }
