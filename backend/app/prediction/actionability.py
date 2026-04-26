from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from app.contracts import (
    ActionabilityFeatures,
    AssemblyQC,
    DecisionObject,
    MechanismSupportLevel,
    MechanisticEvidence,
    NoveltyAssessment,
    PhenotypePrediction,
    PredictedPhenotype,
    QCStatus,
    RationaleCode,
    SampleInput,
    SeverityLevel,
    SplitContext,
    TriageDecision,
    TriageOutcome,
)
from app.contracts.common import normalize_slug_like
from app.evidence.concordance import (
    MechanismConcordanceClassification,
    assess_mechanism_concordance,
)

DEFAULT_ACTIONABILITY_THRESHOLD_VERSION = "actionability_policy_v1"


class PredictionMechanismClass(str, Enum):
    CONCORDANT_RESISTANT = "concordant_resistant"
    UNSUPPORTED_RESISTANT = "unsupported_resistant"
    MECHANISM_ONLY_SIGNAL = "mechanism_only_signal"
    SUSCEPTIBLE_WITH_MECHANISM = "susceptible_with_mechanism"
    AMBIGUOUS = "ambiguous"
    CONFLICTING = "conflicting"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class MechanismPredictionFeature:
    classification: PredictionMechanismClass
    mechanism_concordance: bool | None
    matched_gene_symbols: tuple[str, ...]
    explanation: str


@dataclass(frozen=True)
class ActionabilityPolicy:
    threshold_version: str = DEFAULT_ACTIONABILITY_THRESHOLD_VERSION
    act_threshold: float = 0.72
    review_threshold: float = 0.45
    high_novelty_defer_threshold: float = 0.80
    high_entropy_defer_threshold: float = 0.85
    qc_defer_threshold: float = 0.90


DEFAULT_ACTIONABILITY_POLICY = ActionabilityPolicy()

_MECHANISM_COMPONENT_BY_CLASS = {
    PredictionMechanismClass.CONCORDANT_RESISTANT: 1.00,
    PredictionMechanismClass.NOT_APPLICABLE: 0.80,
    PredictionMechanismClass.MECHANISM_ONLY_SIGNAL: 0.45,
    PredictionMechanismClass.AMBIGUOUS: 0.35,
    PredictionMechanismClass.UNSUPPORTED_RESISTANT: 0.25,
    PredictionMechanismClass.SUSCEPTIBLE_WITH_MECHANISM: 0.15,
    PredictionMechanismClass.CONFLICTING: 0.10,
}


def _clamp01(value: float) -> float:
    return min(1.0, max(0.0, value))


def _metadata_completeness(qc: AssemblyQC) -> float:
    expected_metadata_fields = 3
    missing_count = min(len(qc.missing_metadata_fields), expected_metadata_fields)
    return round(1.0 - (missing_count / expected_metadata_fields), 6)


def _qc_risk(qc: AssemblyQC) -> float:
    if qc.qc_status == QCStatus.FAIL:
        return 1.0
    if qc.qc_status == QCStatus.WARN:
        return 0.4
    return 0.0


def _novelty_risk(novelty: NoveltyAssessment) -> float:
    if novelty.missing_reference:
        return 1.0
    if novelty.novelty_score is None:
        return 0.5
    return _clamp01(novelty.novelty_score)


def _matched_symbols(evidence_rows: Sequence[MechanisticEvidence]) -> tuple[str, ...]:
    return tuple(row.gene_symbol or row.mutation or row.mechanism_class for row in evidence_rows)


def _relevant_rows(
    *,
    target_drug: str,
    evidence_rows: Sequence[MechanisticEvidence],
) -> list[MechanisticEvidence]:
    normalized_drug = normalize_slug_like(target_drug)
    return [row for row in evidence_rows if normalized_drug in row.drug_association]


def _validate_prediction_evidence_context(
    *,
    prediction: PhenotypePrediction,
    evidence_rows: Sequence[MechanisticEvidence],
) -> None:
    mismatched_jobs = [row.job_id for row in evidence_rows if row.job_id != prediction.job_id]
    if mismatched_jobs:
        raise ValueError("Mechanism prediction inputs must match prediction job_id.")
    mismatched_rows = [row.sample_id for row in evidence_rows if row.sample_id != prediction.sample_id]
    if mismatched_rows:
        raise ValueError("Mechanism prediction inputs must match prediction sample_id.")
    mismatched_drugs = [row.target_drug for row in evidence_rows if row.target_drug != prediction.target_drug]
    if mismatched_drugs:
        raise ValueError("Mechanism prediction inputs must match prediction target_drug.")


def classify_mechanism_prediction_concordance(
    *,
    prediction: PhenotypePrediction,
    evidence_rows: Sequence[MechanisticEvidence],
) -> MechanismPredictionFeature:
    _validate_prediction_evidence_context(prediction=prediction, evidence_rows=evidence_rows)
    relevant_rows = _relevant_rows(target_drug=prediction.target_drug, evidence_rows=evidence_rows)

    if prediction.predicted_phenotype == PredictedPhenotype.RESISTANT:
        concordance = assess_mechanism_concordance(
            target_drug=prediction.target_drug,
            predicted_phenotype=prediction.predicted_phenotype,
            evidence_rows=evidence_rows,
        )
        if concordance.classification == MechanismConcordanceClassification.SUPPORTED:
            classification = PredictionMechanismClass.CONCORDANT_RESISTANT
        elif concordance.classification == MechanismConcordanceClassification.MISSING:
            classification = PredictionMechanismClass.UNSUPPORTED_RESISTANT
        elif concordance.classification == MechanismConcordanceClassification.CONFLICTING:
            classification = PredictionMechanismClass.CONFLICTING
        else:
            classification = PredictionMechanismClass.AMBIGUOUS

        return MechanismPredictionFeature(
            classification=classification,
            mechanism_concordance=concordance.mechanism_concordance,
            matched_gene_symbols=concordance.matched_gene_symbols,
            explanation=concordance.explanation,
        )

    supported_rows = [
        row
        for row in relevant_rows
        if row.support_level in {MechanismSupportLevel.SUPPORTED, MechanismSupportLevel.PARTIAL}
    ]
    if supported_rows:
        return MechanismPredictionFeature(
            classification=PredictionMechanismClass.SUSCEPTIBLE_WITH_MECHANISM,
            mechanism_concordance=False,
            matched_gene_symbols=_matched_symbols(supported_rows),
            explanation="Drug-specific evidence is present despite a susceptible prediction.",
        )
    if relevant_rows:
        return MechanismPredictionFeature(
            classification=PredictionMechanismClass.MECHANISM_ONLY_SIGNAL,
            mechanism_concordance=None,
            matched_gene_symbols=_matched_symbols(relevant_rows),
            explanation="Only weak or screen-only drug-specific evidence is present.",
        )
    return MechanismPredictionFeature(
        classification=PredictionMechanismClass.NOT_APPLICABLE,
        mechanism_concordance=None,
        matched_gene_symbols=(),
        explanation="No drug-specific mechanism evidence was present for the prediction.",
    )


def build_actionability_features(
    *,
    qc: AssemblyQC,
    prediction: PhenotypePrediction,
    novelty: NoveltyAssessment,
    evidence_rows: Sequence[MechanisticEvidence],
    threshold_version: str = DEFAULT_ACTIONABILITY_THRESHOLD_VERSION,
) -> ActionabilityFeatures:
    _validate_actionability_context(
        qc=qc,
        prediction=prediction,
        novelty=novelty,
        evidence_rows=evidence_rows,
    )
    mechanism_feature = classify_mechanism_prediction_concordance(
        prediction=prediction,
        evidence_rows=evidence_rows,
    )
    entropy = _clamp01(prediction.uncertainty_score if prediction.uncertainty_score is not None else 0.5)
    qc_component = _qc_risk(qc)
    novelty_component = _novelty_risk(novelty)
    metadata_component = _metadata_completeness(qc)
    mechanism_component = _MECHANISM_COMPONENT_BY_CLASS[mechanism_feature.classification]
    actionability_score = round(
        (
            0.30 * mechanism_component
            + 0.20 * (1.0 - entropy)
            + 0.20 * (1.0 - novelty_component)
            + 0.15 * (1.0 - qc_component)
            + 0.15 * metadata_component
        ),
        6,
    )

    warnings = [
        *prediction.warnings,
        *novelty.warnings,
        *qc.warnings,
    ]
    if prediction.uncertainty_score is None:
        warnings.append("prediction_uncertainty_missing")
    if novelty.missing_reference:
        warnings.append("novelty_reference_sparse")
    if mechanism_feature.classification in {
        PredictionMechanismClass.UNSUPPORTED_RESISTANT,
        PredictionMechanismClass.SUSCEPTIBLE_WITH_MECHANISM,
        PredictionMechanismClass.CONFLICTING,
    }:
        warnings.append(f"mechanism_prediction_{mechanism_feature.classification.value}")

    return ActionabilityFeatures(
        job_id=prediction.job_id,
        sample_id=prediction.sample_id,
        target_drug=prediction.target_drug,
        actionability_score=actionability_score,
        mechanism_concordance=mechanism_feature.mechanism_concordance,
        prediction_entropy=entropy,
        qc_risk=qc_component,
        novelty_risk=novelty_component,
        metadata_completeness=metadata_component,
        threshold_version=threshold_version,
        warnings=sorted(set(warnings)),
    )


def apply_triage_policy(
    *,
    features: ActionabilityFeatures,
    policy: ActionabilityPolicy = DEFAULT_ACTIONABILITY_POLICY,
) -> TriageDecision:
    hard_defer_reasons = _hard_defer_reasons(features=features, policy=policy)
    rationale_codes = _rationale_codes_for_features(features=features, policy=policy)

    if hard_defer_reasons:
        triage = TriageOutcome.DEFER_TO_LAB
        severity = SeverityLevel.HIGH
        next_step = "defer decision until confirmation or additional evidence is available"
        rationale_codes.append(RationaleCode.MANUAL_CONFIRMATION_REQUIRED)
    elif features.actionability_score >= policy.act_threshold and features.mechanism_concordance is not False:
        triage = TriageOutcome.ACT
        severity = SeverityLevel.LOW
        next_step = "continue standard review workflow with saved evidence artifacts"
    elif features.actionability_score >= policy.review_threshold:
        triage = TriageOutcome.REVIEW
        severity = SeverityLevel.MEDIUM
        next_step = "route to analyst review with evidence and uncertainty context"
        rationale_codes.append(RationaleCode.MANUAL_CONFIRMATION_REQUIRED)
    else:
        triage = TriageOutcome.DEFER_TO_LAB
        severity = SeverityLevel.HIGH
        next_step = "defer decision until confirmation or additional evidence is available"
        rationale_codes.append(RationaleCode.MANUAL_CONFIRMATION_REQUIRED)

    return TriageDecision(
        job_id=features.job_id,
        sample_id=features.sample_id,
        target_drug=features.target_drug,
        triage=triage,
        severity=severity,
        recommended_next_step=next_step,
        threshold_version=policy.threshold_version,
        rationale_codes=sorted(set(rationale_codes), key=lambda code: code.value),
        warnings=sorted(set([*features.warnings, *hard_defer_reasons])),
    )


def build_decision_object(
    *,
    sample: SampleInput,
    qc: AssemblyQC,
    evidence_rows: Sequence[MechanisticEvidence],
    prediction: PhenotypePrediction,
    novelty: NoveltyAssessment,
    job_id: str | None = None,
    artifact_manifest_id: str | None = None,
    policy: ActionabilityPolicy = DEFAULT_ACTIONABILITY_POLICY,
) -> DecisionObject:
    resolved_job_id = job_id or prediction.job_id
    if sample.sample_id != prediction.sample_id:
        raise ValueError("Decision inputs must match sample_id.")
    if sample.target_drug != prediction.target_drug:
        raise ValueError("Decision inputs must match target_drug.")
    if novelty.job_id != prediction.job_id:
        raise ValueError("Decision inputs must match job_id.")
    if resolved_job_id != prediction.job_id:
        raise ValueError("Decision job_id must match prediction job_id.")

    features = build_actionability_features(
        qc=qc,
        prediction=prediction,
        novelty=novelty,
        evidence_rows=evidence_rows,
        threshold_version=policy.threshold_version,
    )
    triage = apply_triage_policy(features=features, policy=policy)

    return DecisionObject(
        job_id=resolved_job_id,
        sample=sample,
        assembly_qc=qc,
        mechanistic_evidence=list(evidence_rows),
        phenotype_prediction=prediction,
        novelty_assessment=novelty,
        actionability_features=features,
        triage_decision=triage,
        rationale_codes=triage.rationale_codes,
        warnings=sorted(set([*features.warnings, *triage.warnings])),
        artifact_manifest_id=artifact_manifest_id,
        provenance_notes=build_decision_provenance_notes(sample=sample, prediction=prediction),
    )


def build_decision_provenance_notes(
    *,
    sample: SampleInput,
    prediction: PhenotypePrediction,
) -> list[str]:
    notes = [
        f"analysis_input_source_context_{sample.metadata.source_context.value}",
        f"analysis_input_provenance_source_{sample.metadata.provenance_source.value}",
        f"prediction_model_training_split_context_{prediction.model_training_split_context.value}",
    ]

    if sample.fasta_uri:
        notes.append("analysis_input_remote_fasta_uri")
    elif sample.fasta_path:
        normalized_fasta_path = sample.fasta_path.replace("\\", "/")
        if normalized_fasta_path.startswith("data/fixtures/"):
            notes.append("analysis_input_fixture_fasta_path")
        elif "/live_data/downloads/fasta/" in normalized_fasta_path:
            notes.append("analysis_input_phase6b_live_retrieval_fasta_path")
        else:
            notes.append("analysis_input_local_fasta_path")

    if prediction.model_training_split_context == SplitContext.FIXTURE:
        notes.append("prediction_model_fixture_backed_baseline")

    return notes


def _validate_actionability_context(
    *,
    qc: AssemblyQC,
    prediction: PhenotypePrediction,
    novelty: NoveltyAssessment,
    evidence_rows: Sequence[MechanisticEvidence],
) -> None:
    if qc.sample_id != prediction.sample_id or novelty.sample_id != prediction.sample_id:
        raise ValueError("Actionability inputs must match prediction sample_id.")
    if qc.job_id != prediction.job_id or novelty.job_id != prediction.job_id:
        raise ValueError("Actionability inputs must match prediction job_id.")
    if qc.target_drug != prediction.target_drug or novelty.target_drug != prediction.target_drug:
        raise ValueError("Actionability inputs must match prediction target_drug.")
    _validate_prediction_evidence_context(prediction=prediction, evidence_rows=evidence_rows)


def _hard_defer_reasons(
    *,
    features: ActionabilityFeatures,
    policy: ActionabilityPolicy,
) -> list[str]:
    reasons: list[str] = []
    if features.qc_risk >= policy.qc_defer_threshold:
        reasons.append("qc_risk_hard_defer")
    if features.novelty_risk >= policy.high_novelty_defer_threshold:
        reasons.append("novelty_risk_hard_defer")
    entropy = features.prediction_entropy if features.prediction_entropy is not None else 1.0
    if entropy >= policy.high_entropy_defer_threshold:
        reasons.append("prediction_entropy_hard_defer")
    return reasons


def _rationale_codes_for_features(
    *,
    features: ActionabilityFeatures,
    policy: ActionabilityPolicy,
) -> list[RationaleCode]:
    codes = [
        (
            RationaleCode.ACTIONABILITY_THRESHOLD_MET
            if features.actionability_score >= policy.act_threshold
            else RationaleCode.ACTIONABILITY_THRESHOLD_NOT_MET
        )
    ]
    if features.mechanism_concordance is True:
        codes.extend(
            [
                RationaleCode.CONCORDANT_SIGNAL_PRESENT,
                RationaleCode.SUPPORTED_MECHANISM_PRESENT,
            ]
        )
    elif features.mechanism_concordance is False:
        codes.append(RationaleCode.NO_SUPPORTED_MECHANISM)
    if features.novelty_risk >= policy.high_novelty_defer_threshold:
        codes.append(RationaleCode.HIGH_LINEAGE_NOVELTY)
    if any("novelty_reference" in warning for warning in features.warnings):
        codes.append(RationaleCode.NOVELTY_REFERENCE_SPARSE)
    if features.qc_risk > 0:
        codes.append(RationaleCode.QC_WARNING_PRESENT)
    if (features.prediction_entropy or 0.0) >= policy.high_entropy_defer_threshold:
        codes.append(RationaleCode.MODEL_UNCERTAINTY_HIGH)
    if features.metadata_completeness < 1.0:
        codes.append(RationaleCode.METADATA_INCOMPLETE)
    return codes
