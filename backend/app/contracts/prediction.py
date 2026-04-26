from __future__ import annotations

from pydantic import AliasChoices, Field, field_validator

from .common import (
    CalibrationStatus,
    ContractModel,
    PredictedPhenotype,
    ProvenanceSource,
    SourceContext,
    SplitContext,
    normalize_slug_like,
)


class PhenotypePrediction(ContractModel):
    job_id: str = Field(min_length=3, max_length=80)
    sample_id: str = Field(min_length=3, max_length=80)
    target_drug: str = Field(min_length=3, max_length=80)
    predicted_phenotype: PredictedPhenotype
    probability: float = Field(ge=0.0, le=1.0)
    calibration_status: CalibrationStatus = CalibrationStatus.NOT_AVAILABLE
    uncertainty_score: float | None = Field(default=None, ge=0.0, le=1.0)
    feature_set_version: str = Field(min_length=3, max_length=80)
    model_version: str = Field(min_length=3, max_length=80)
    model_training_split_context: SplitContext = Field(
        default=SplitContext.SMOKE,
        validation_alias=AliasChoices("model_training_split_context", "split_context"),
    )
    input_source_context: SourceContext = SourceContext.OTHER
    input_provenance_source: ProvenanceSource = ProvenanceSource.OTHER
    warnings: list[str] = Field(default_factory=list)

    @field_validator("job_id", "sample_id", "target_drug", "feature_set_version", "model_version")
    @classmethod
    def normalize_slug_fields(cls, value: str) -> str:
        return normalize_slug_like(value)

    @property
    def split_context(self) -> SplitContext:
        return self.model_training_split_context
