from __future__ import annotations

from enum import Enum

from pydantic import Field, field_validator, model_validator

from .common import ContractModel, normalize_slug_like
from .evidence import MechanisticEvidence
from .novelty import NoveltyAssessment
from .prediction import PhenotypePrediction
from .sample import AssemblyQC, SampleInput


class TriageOutcome(str, Enum):
    ACT = "act"
    REVIEW = "review"
    DEFER_TO_LAB = "defer_to_lab"


class SeverityLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RationaleCode(str, Enum):
    ACTIONABILITY_THRESHOLD_MET = "actionability_threshold_met"
    ACTIONABILITY_THRESHOLD_NOT_MET = "actionability_threshold_not_met"
    CONCORDANT_SIGNAL_PRESENT = "concordant_signal_present"
    HIGH_LINEAGE_NOVELTY = "high_lineage_novelty"
    MANUAL_CONFIRMATION_REQUIRED = "manual_confirmation_required"
    METADATA_INCOMPLETE = "metadata_incomplete"
    MODEL_UNCERTAINTY_HIGH = "model_uncertainty_high"
    NO_SUPPORTED_MECHANISM = "no_supported_mechanism"
    NOVELTY_REFERENCE_SPARSE = "novelty_reference_sparse"
    QC_WARNING_PRESENT = "qc_warning_present"
    SUPPORTED_MECHANISM_PRESENT = "supported_mechanism_present"


def _normalize_rationale_values(values: list[RationaleCode] | list[str]) -> list[RationaleCode] | list[str]:
    normalized_values: list[RationaleCode] | list[str] = []
    for value in values:
        if isinstance(value, RationaleCode):
            normalized_values.append(value)
            continue
        normalized_values.append(normalize_slug_like(str(value)))
    return normalized_values


class ActionabilityFeatures(ContractModel):
    job_id: str = Field(min_length=3, max_length=80)
    sample_id: str = Field(min_length=3, max_length=80)
    target_drug: str = Field(min_length=3, max_length=80)
    actionability_score: float = Field(ge=0.0, le=1.0)
    mechanism_concordance: bool | None = None
    prediction_entropy: float | None = Field(default=None, ge=0.0, le=1.0)
    qc_risk: float = Field(ge=0.0, le=1.0)
    novelty_risk: float = Field(ge=0.0, le=1.0)
    metadata_completeness: float = Field(ge=0.0, le=1.0)
    threshold_version: str = Field(min_length=3, max_length=80)
    warnings: list[str] = Field(default_factory=list)

    @field_validator("job_id", "sample_id", "target_drug", "threshold_version")
    @classmethod
    def normalize_slug_fields(cls, value: str) -> str:
        return normalize_slug_like(value)


class TriageDecision(ContractModel):
    job_id: str = Field(min_length=3, max_length=80)
    sample_id: str = Field(min_length=3, max_length=80)
    target_drug: str = Field(min_length=3, max_length=80)
    triage: TriageOutcome
    severity: SeverityLevel
    recommended_next_step: str = Field(min_length=5, max_length=240)
    threshold_version: str = Field(min_length=3, max_length=80)
    rationale_codes: list[RationaleCode] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @field_validator("job_id", "sample_id", "target_drug", "threshold_version")
    @classmethod
    def normalize_slug_fields(cls, value: str) -> str:
        return normalize_slug_like(value)

    @field_validator("triage", "severity", mode="before")
    @classmethod
    def normalize_enum_tokens(cls, value: TriageOutcome | SeverityLevel | str) -> TriageOutcome | SeverityLevel | str:
        if isinstance(value, (TriageOutcome, SeverityLevel)):
            return value
        return normalize_slug_like(str(value))

    @field_validator("rationale_codes", mode="before")
    @classmethod
    def normalize_rationale_codes(
        cls,
        value: list[RationaleCode] | list[str],
    ) -> list[RationaleCode] | list[str]:
        return _normalize_rationale_values(value)

    @model_validator(mode="after")
    def require_rationale_codes(self) -> "TriageDecision":
        if not self.rationale_codes:
            raise ValueError("At least one rationale code must be present.")
        return self


class DecisionObject(ContractModel):
    job_id: str | None = Field(default=None, min_length=3, max_length=80)
    sample: SampleInput
    assembly_qc: AssemblyQC
    mechanistic_evidence: list[MechanisticEvidence] = Field(default_factory=list)
    phenotype_prediction: PhenotypePrediction
    novelty_assessment: NoveltyAssessment
    actionability_features: ActionabilityFeatures
    triage_decision: TriageDecision
    rationale_codes: list[RationaleCode] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    artifact_manifest_id: str | None = Field(default=None, min_length=3, max_length=120)
    provenance_notes: list[str] = Field(default_factory=list)

    @field_validator("job_id", "artifact_manifest_id")
    @classmethod
    def normalize_optional_slug_fields(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_slug_like(value)

    @field_validator("rationale_codes", mode="before")
    @classmethod
    def normalize_rationale_codes(
        cls,
        value: list[RationaleCode] | list[str],
    ) -> list[RationaleCode] | list[str]:
        return _normalize_rationale_values(value)

    @model_validator(mode="after")
    def ensure_consistent_nested_context(self) -> "DecisionObject":
        sample_id = self.sample.sample_id
        target_drug = self.sample.target_drug

        nested_sample_ids = [
            self.assembly_qc.sample_id,
            self.phenotype_prediction.sample_id,
            self.novelty_assessment.sample_id,
            self.actionability_features.sample_id,
            self.triage_decision.sample_id,
            *(item.sample_id for item in self.mechanistic_evidence),
        ]
        if any(item != sample_id for item in nested_sample_ids):
            raise ValueError("All nested contracts must reference the same sample_id.")

        nested_drugs = [
            self.assembly_qc.target_drug,
            self.novelty_assessment.target_drug,
            self.phenotype_prediction.target_drug,
            self.actionability_features.target_drug,
            self.triage_decision.target_drug,
            *(item.target_drug for item in self.mechanistic_evidence),
        ]
        if any(item != target_drug for item in nested_drugs):
            raise ValueError("All decision-layer contracts must reference the same target_drug.")

        nested_job_ids = [
            self.assembly_qc.job_id,
            self.phenotype_prediction.job_id,
            self.novelty_assessment.job_id,
            self.actionability_features.job_id,
            self.triage_decision.job_id,
            *(item.job_id for item in self.mechanistic_evidence),
        ]
        expected_job_id = nested_job_ids[0] if nested_job_ids else self.job_id
        if any(item != expected_job_id for item in nested_job_ids):
            raise ValueError("All job-scoped contracts must reference the same job_id.")
        if self.job_id is not None and any(item != self.job_id for item in nested_job_ids):
            raise ValueError("DecisionObject job_id must match nested job-scoped contracts.")

        if self.rationale_codes != self.triage_decision.rationale_codes:
            raise ValueError("DecisionObject rationale_codes must mirror triage_decision rationale_codes.")
        return self
