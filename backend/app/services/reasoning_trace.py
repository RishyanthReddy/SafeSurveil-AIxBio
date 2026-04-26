from __future__ import annotations

from enum import Enum

from app.contracts import (
    DecisionObject,
    MechanismSupportLevel,
    NoveltyBucket,
    QCStatus,
    RationaleCode,
    ReasoningTrace,
    ReasoningTraceCaveat,
    ReasoningTraceCaveatSeverity,
    ReasoningTraceCoverage,
    REASONING_TRACE_REQUIRED_STEP_TYPES,
    ReasoningTraceSourceRef,
    ReasoningTraceStep,
    ReasoningTraceStepStatus,
    ReasoningTraceStepType,
    TriageOutcome,
)


def _enum_value(value: object) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _display_token(value: object | None) -> str:
    if value is None:
        return "unavailable"
    return _enum_value(value).replace("_", " ")


def _percent(value: float | None) -> str:
    if value is None:
        return "unavailable"
    return f"{round(value * 100)}%"


def _evidence_ref(
    evidence_id: str,
    *,
    source_type: str,
    label: str,
    detail: str | None = None,
) -> ReasoningTraceSourceRef:
    return ReasoningTraceSourceRef(
        evidence_id=evidence_id,
        source_type=source_type,
        label=label,
        detail=detail,
    )


def _mechanism_signal(evidence_index: int, decision: DecisionObject) -> str:
    evidence = decision.mechanistic_evidence[evidence_index]
    return evidence.gene_symbol or evidence.mutation or f"mechanism {evidence_index + 1}"


def _supported_mechanism_indices(decision: DecisionObject) -> list[int]:
    supported_levels = {
        MechanismSupportLevel.SUPPORTED.value,
        MechanismSupportLevel.PARTIAL.value,
    }
    return [
        index
        for index, evidence in enumerate(decision.mechanistic_evidence)
        if _enum_value(evidence.support_level) in supported_levels
    ]


def _rationale_values(decision: DecisionObject) -> set[str]:
    return {_enum_value(item) for item in decision.triage_decision.rationale_codes}


def _sample_context_text(decision: DecisionObject) -> str:
    organism = _display_token(decision.sample.organism_hint)
    source_context = _display_token(decision.sample.metadata.source_context)
    accession = decision.sample.metadata.accession or "no accession recorded"
    return (
        f"The case evaluates {organism} sample {decision.sample.sample_id} against "
        f"{decision.sample.target_drug}; input source context is {source_context} "
        f"with {accession}."
    )


def _phenotype_text(decision: DecisionObject) -> str:
    prediction = decision.phenotype_prediction
    return (
        f"The persisted baseline predicts {_display_token(prediction.predicted_phenotype)} "
        f"for {prediction.target_drug} with probability {_percent(prediction.probability)} "
        f"and calibration status {_display_token(prediction.calibration_status)}."
    )


def _mechanistic_text(decision: DecisionObject, supported_indices: list[int]) -> str:
    if not decision.mechanistic_evidence:
        return "No mechanistic evidence rows are persisted for this case."
    if not supported_indices:
        return (
            f"{len(decision.mechanistic_evidence)} mechanistic evidence row(s) are persisted, "
            "but none are marked as supported or partial support for the target context."
        )
    signals = [_mechanism_signal(index, decision) for index in supported_indices[:4]]
    signal_text = ", ".join(signals)
    return (
        f"{len(supported_indices)} supported or partially supported mechanistic evidence row(s) "
        f"are persisted, including {signal_text}."
    )


def _mechanism_drug_text(decision: DecisionObject, supported_indices: list[int]) -> str:
    if not supported_indices:
        return (
            "The actionability layer treats mechanism-drug support as absent, so the mechanism path "
            "cannot independently justify action."
        )
    associated = [
        _mechanism_signal(index, decision)
        for index in supported_indices
        if decision.sample.target_drug in decision.mechanistic_evidence[index].drug_association
    ]
    if associated:
        return (
            f"The mechanism-to-drug interpretation is concordant: {', '.join(associated[:4])} "
            f"is associated with {decision.sample.target_drug}."
        )
    return (
        "Mechanistic evidence is present, but the persisted drug-association rows do not directly "
        f"name {decision.sample.target_drug}; the trace preserves that limitation."
    )


def _novelty_text(decision: DecisionObject) -> str:
    novelty = decision.novelty_assessment
    nearest = novelty.nearest_neighbor_id or "no nearest neighbor recorded"
    return (
        f"The novelty assessment reports bucket {_display_token(novelty.novelty_bucket)} "
        f"with score {_percent(novelty.novelty_score)} and nearest neighbor {nearest}."
    )


def _qc_metadata_text(decision: DecisionObject) -> str:
    qc = decision.assembly_qc
    features = decision.actionability_features
    missing_fields = ", ".join(qc.missing_metadata_fields) if qc.missing_metadata_fields else "none listed"
    return (
        f"Assembly QC status is {_display_token(qc.qc_status)} with QC risk {_percent(features.qc_risk)}; "
        f"metadata completeness is {_percent(features.metadata_completeness)} and missing fields are {missing_fields}."
    )


def _actionability_text(decision: DecisionObject) -> str:
    features = decision.actionability_features
    return (
        f"Actionability policy {features.threshold_version} records score "
        f"{_percent(features.actionability_score)} with mechanism concordance "
        f"{'present' if features.mechanism_concordance else 'absent'}."
    )


def _final_triage_text(decision: DecisionObject) -> str:
    triage = decision.triage_decision
    rationale = ", ".join(_display_token(item) for item in triage.rationale_codes)
    return (
        f"The final persisted triage is {_display_token(triage.triage)} with "
        f"{_display_token(triage.severity)} severity. Recommended next step: "
        f"{triage.recommended_next_step} Rationale: {rationale}."
    )


def _build_caveats(decision: DecisionObject, supported_indices: list[int]) -> list[ReasoningTraceCaveat]:
    caveats: dict[str, ReasoningTraceCaveat] = {}
    rationale = _rationale_values(decision)
    novelty = decision.novelty_assessment
    qc = decision.assembly_qc
    features = decision.actionability_features
    triage = decision.triage_decision

    if not supported_indices or RationaleCode.NO_SUPPORTED_MECHANISM.value in rationale:
        caveats["mechanism_missing_or_weak"] = ReasoningTraceCaveat(
            caveat_id="mechanism_missing_or_weak",
            severity=ReasoningTraceCaveatSeverity.LIMITATION,
            title="Mechanistic support is missing or weak",
            detail="The persisted decision does not contain supported mechanism evidence for the target context.",
            evidence_refs=["mechanistic_evidence__none", "actionability_features__summary"],
        )

    high_or_sparse_novelty = (
        _enum_value(novelty.novelty_bucket) in {NoveltyBucket.HIGH.value, NoveltyBucket.UNKNOWN.value}
        or novelty.uncertainty_flag
        or novelty.missing_reference
        or RationaleCode.HIGH_LINEAGE_NOVELTY.value in rationale
        or RationaleCode.NOVELTY_REFERENCE_SPARSE.value in rationale
    )
    if high_or_sparse_novelty:
        caveats["novelty_uncertainty"] = ReasoningTraceCaveat(
            caveat_id="novelty_uncertainty",
            severity=ReasoningTraceCaveatSeverity.LIMITATION,
            title="Novelty evidence needs analyst review",
            detail="Novelty or reference-sparsity evidence limits confidence in immediate operational action.",
            evidence_refs=["novelty_assessment__summary"],
        )

    qc_not_clean = (
        qc.qc_status != QCStatus.PASS
        or bool(qc.warnings)
        or RationaleCode.QC_WARNING_PRESENT.value in rationale
        or features.qc_risk > 0.0
    )
    if qc_not_clean:
        caveats["qc_warning_present"] = ReasoningTraceCaveat(
            caveat_id="qc_warning_present",
            severity=ReasoningTraceCaveatSeverity.WARNING,
            title="QC warning present",
            detail="The persisted decision contains QC risk, QC warnings, or a non-passing QC status.",
            evidence_refs=["decision_object__assembly_qc", "actionability_features__summary"],
        )

    metadata_incomplete = (
        features.metadata_completeness < 1.0
        or bool(qc.missing_metadata_fields)
        or RationaleCode.METADATA_INCOMPLETE.value in rationale
    )
    if metadata_incomplete:
        caveats["metadata_incomplete"] = ReasoningTraceCaveat(
            caveat_id="metadata_incomplete",
            severity=ReasoningTraceCaveatSeverity.LIMITATION,
            title="Metadata is incomplete",
            detail="The persisted metadata completeness score or missing-field list indicates incomplete context.",
            evidence_refs=["decision_object__warnings", "actionability_features__summary"],
        )

    if RationaleCode.ACTIONABILITY_THRESHOLD_NOT_MET.value in rationale:
        caveats["actionability_threshold_not_met"] = ReasoningTraceCaveat(
            caveat_id="actionability_threshold_not_met",
            severity=ReasoningTraceCaveatSeverity.LIMITATION,
            title="Actionability threshold not met",
            detail="The persisted policy decision records that the actionability threshold was not met.",
            evidence_refs=["actionability_features__summary", "decision_object__triage"],
        )

    if (
        triage.triage == TriageOutcome.DEFER_TO_LAB
        or RationaleCode.MANUAL_CONFIRMATION_REQUIRED.value in rationale
    ):
        caveats["manual_confirmation_required"] = ReasoningTraceCaveat(
            caveat_id="manual_confirmation_required",
            severity=ReasoningTraceCaveatSeverity.WARNING,
            title="Manual confirmation required",
            detail="The persisted triage requires analyst or laboratory confirmation before action.",
            evidence_refs=["decision_object__triage"],
        )

    return list(caveats.values())


def _step_status(caveat_ids: list[str]) -> ReasoningTraceStepStatus:
    return ReasoningTraceStepStatus.CAVEATED if caveat_ids else ReasoningTraceStepStatus.GROUNDED


def _mechanism_evidence_refs(decision: DecisionObject) -> list[ReasoningTraceSourceRef]:
    if not decision.mechanistic_evidence:
        return [
            _evidence_ref(
                "mechanistic_evidence__none",
                source_type="mechanistic_evidence",
                label="No persisted mechanism evidence",
            )
        ]
    return [
        _evidence_ref(
            f"mechanistic_evidence__{index}",
            source_type="mechanistic_evidence",
            label=_mechanism_signal(index - 1, decision),
            detail=evidence.interpretation,
        )
        for index, evidence in enumerate(decision.mechanistic_evidence, start=1)
    ]


def build_reasoning_trace(decision: DecisionObject) -> ReasoningTrace:
    supported_indices = _supported_mechanism_indices(decision)
    caveats = _build_caveats(decision, supported_indices)
    caveat_ids = {caveat.caveat_id for caveat in caveats}

    novelty_caveats = [
        caveat_id for caveat_id in ("novelty_uncertainty",) if caveat_id in caveat_ids
    ]
    qc_metadata_caveats = [
        caveat_id
        for caveat_id in ("qc_warning_present", "metadata_incomplete")
        if caveat_id in caveat_ids
    ]
    mechanism_caveats = [
        caveat_id for caveat_id in ("mechanism_missing_or_weak",) if caveat_id in caveat_ids
    ]
    actionability_caveats = [
        caveat_id
        for caveat_id in ("actionability_threshold_not_met", "manual_confirmation_required")
        if caveat_id in caveat_ids
    ]
    final_caveats = [
        caveat_id
        for caveat_id in (
            "novelty_uncertainty",
            "qc_warning_present",
            "metadata_incomplete",
            "actionability_threshold_not_met",
            "manual_confirmation_required",
            "mechanism_missing_or_weak",
        )
        if caveat_id in caveat_ids
    ]

    steps = [
        ReasoningTraceStep(
            step_number=1,
            step_type=ReasoningTraceStepType.SAMPLE_CONTEXT,
            title="Sample and target context",
            text=_sample_context_text(decision),
            evidence_refs=[
                _evidence_ref(
                    "decision_object__summary",
                    source_type="decision_object",
                    label="Decision summary",
                )
            ],
        ),
        ReasoningTraceStep(
            step_number=2,
            step_type=ReasoningTraceStepType.PHENOTYPE_PREDICTION,
            title="Phenotype prediction",
            text=_phenotype_text(decision),
            evidence_refs=[
                _evidence_ref(
                    "phenotype_prediction__summary",
                    source_type="phenotype_prediction",
                    label="Phenotype prediction",
                )
            ],
        ),
        ReasoningTraceStep(
            step_number=3,
            step_type=ReasoningTraceStepType.MECHANISTIC_EVIDENCE,
            title="Mechanistic evidence",
            text=_mechanistic_text(decision, supported_indices),
            status=_step_status(mechanism_caveats),
            evidence_refs=_mechanism_evidence_refs(decision),
            caveat_ids=mechanism_caveats,
        ),
        ReasoningTraceStep(
            step_number=4,
            step_type=ReasoningTraceStepType.MECHANISM_DRUG_INTERPRETATION,
            title="Mechanism to drug interpretation",
            text=_mechanism_drug_text(decision, supported_indices),
            status=_step_status(mechanism_caveats),
            evidence_refs=[
                *_mechanism_evidence_refs(decision),
                _evidence_ref(
                    "actionability_features__summary",
                    source_type="actionability_features",
                    label="Actionability features",
                ),
            ],
            caveat_ids=mechanism_caveats,
        ),
        ReasoningTraceStep(
            step_number=5,
            step_type=ReasoningTraceStepType.NOVELTY_LINEAGE_SHIFT,
            title="Novelty and lineage shift",
            text=_novelty_text(decision),
            status=_step_status(novelty_caveats),
            evidence_refs=[
                _evidence_ref(
                    "novelty_assessment__summary",
                    source_type="novelty_assessment",
                    label="Novelty assessment",
                )
            ],
            caveat_ids=novelty_caveats,
        ),
        ReasoningTraceStep(
            step_number=6,
            step_type=ReasoningTraceStepType.QC_METADATA_LIMITATIONS,
            title="QC and metadata limitations",
            text=_qc_metadata_text(decision),
            status=_step_status(qc_metadata_caveats),
            evidence_refs=[
                _evidence_ref(
                    "decision_object__assembly_qc",
                    source_type="assembly_qc",
                    label="Assembly QC",
                ),
                _evidence_ref(
                    "decision_object__warnings",
                    source_type="decision_object",
                    label="Decision warnings",
                ),
                _evidence_ref(
                    "actionability_features__summary",
                    source_type="actionability_features",
                    label="Actionability features",
                ),
            ],
            caveat_ids=qc_metadata_caveats,
        ),
        ReasoningTraceStep(
            step_number=7,
            step_type=ReasoningTraceStepType.ACTIONABILITY_POLICY,
            title="Actionability policy interpretation",
            text=_actionability_text(decision),
            status=_step_status(actionability_caveats),
            evidence_refs=[
                _evidence_ref(
                    "actionability_features__summary",
                    source_type="actionability_features",
                    label="Actionability features",
                ),
                _evidence_ref(
                    "decision_object__triage",
                    source_type="decision_object",
                    label="Persisted triage",
                ),
            ],
            caveat_ids=actionability_caveats,
        ),
        ReasoningTraceStep(
            step_number=8,
            step_type=ReasoningTraceStepType.FINAL_TRIAGE,
            title="Final triage and next step",
            text=_final_triage_text(decision),
            status=_step_status(final_caveats),
            evidence_refs=[
                _evidence_ref(
                    "decision_object__triage",
                    source_type="decision_object",
                    label="Persisted triage",
                ),
                _evidence_ref(
                    "decision_object__summary",
                    source_type="decision_object",
                    label="Decision summary",
                ),
            ],
            caveat_ids=final_caveats,
        ),
    ]

    coverage = ReasoningTraceCoverage(
        required_step_types=list(REASONING_TRACE_REQUIRED_STEP_TYPES),
        present_step_types=[step.step_type for step in steps],
        missing_step_types=[],
        required_steps=len(REASONING_TRACE_REQUIRED_STEP_TYPES),
        present_steps=len(steps),
        coverage_ratio=1.0,
    )
    triage = decision.triage_decision
    summary = (
        f"Deterministic reasoning trace for {decision.sample.sample_id}: "
        f"{_display_token(triage.triage)} triage with {_display_token(triage.severity)} severity, "
        "derived from persisted sample, prediction, mechanism, novelty, QC, and policy fields."
    )
    return ReasoningTrace(
        job_id=decision.job_id or triage.job_id,
        sample_id=decision.sample.sample_id,
        target_drug=decision.sample.target_drug,
        decision=triage.triage,
        severity=triage.severity,
        summary=summary,
        steps=steps,
        coverage=coverage,
        caveats=caveats,
        metadata={
            "provider_calls_triggered": False,
            "builder": "deterministic_decision_object_trace",
        },
    )
