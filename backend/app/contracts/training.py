from __future__ import annotations

from enum import Enum

from pydantic import Field, field_validator, model_validator

from .common import (
    CalibrationStatus,
    ContractModel,
    OrganismHint,
    PredictedPhenotype,
    SplitContext,
    normalize_slug_like,
)


class FeatureStorageFormat(str, Enum):
    JSON = "json"
    JSONL = "jsonl"
    CSV = "csv"


class BaselineFeatureStrategy(ContractModel):
    feature_set_version: str = Field(min_length=3, max_length=80)
    target_scope: str = Field(min_length=3, max_length=120)
    primary_feature_family: str = Field(min_length=3, max_length=120)
    storage_format: FeatureStorageFormat = FeatureStorageFormat.JSON
    artifact_path: str = Field(min_length=3, max_length=240)
    binary_features: list[str] = Field(default_factory=list)
    numeric_features: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @field_validator("feature_set_version", "target_scope", "primary_feature_family")
    @classmethod
    def normalize_slug_fields(cls, value: str) -> str:
        return normalize_slug_like(value)

    @field_validator("artifact_path")
    @classmethod
    def normalize_artifact_path(cls, value: str) -> str:
        return value.replace("\\", "/")

    @field_validator("binary_features", "numeric_features")
    @classmethod
    def normalize_feature_names(cls, value: list[str]) -> list[str]:
        return [normalize_slug_like(item) for item in value]


class FeatureVectorRecord(ContractModel):
    job_id: str | None = Field(default=None, min_length=3, max_length=80)
    sample_id: str = Field(min_length=3, max_length=80)
    target_drug: str | None = Field(default=None, min_length=3, max_length=80)
    feature_set_version: str = Field(min_length=3, max_length=80)
    values: dict[str, float] = Field(default_factory=dict)

    @field_validator("job_id", "sample_id", "target_drug", "feature_set_version")
    @classmethod
    def normalize_slug_fields(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_slug_like(value)

    @field_validator("values")
    @classmethod
    def normalize_feature_values(cls, value: dict[str, float]) -> dict[str, float]:
        normalized: dict[str, float] = {}
        for key, item in value.items():
            normalized_key = normalize_slug_like(key)
            numeric_value = float(item)
            if numeric_value < 0.0 or numeric_value > 1.0:
                raise ValueError("Feature values must stay within the normalized range [0.0, 1.0].")
            normalized[normalized_key] = round(numeric_value, 6)
        return normalized


class FeatureMatrixArtifact(ContractModel):
    feature_set_version: str = Field(min_length=3, max_length=80)
    target_scope: str = Field(min_length=3, max_length=120)
    organism: OrganismHint
    target_drug: str = Field(min_length=3, max_length=80)
    split_context: SplitContext
    split_id: str = Field(min_length=3, max_length=80)
    snapshot_id: str = Field(min_length=3, max_length=120)
    storage_format: FeatureStorageFormat = FeatureStorageFormat.JSON
    feature_names: list[str] = Field(default_factory=list)
    rows: list[FeatureVectorRecord] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @field_validator("feature_set_version", "target_scope", "target_drug", "split_id", "snapshot_id")
    @classmethod
    def normalize_slug_fields(cls, value: str) -> str:
        return normalize_slug_like(value)

    @field_validator("feature_names")
    @classmethod
    def normalize_feature_names(cls, value: list[str]) -> list[str]:
        return [normalize_slug_like(item) for item in value]

    @model_validator(mode="after")
    def ensure_rows_match_strategy(self) -> "FeatureMatrixArtifact":
        feature_name_set = set(self.feature_names)
        if not self.rows:
            raise ValueError("FeatureMatrixArtifact requires at least one row.")
        for row in self.rows:
            if row.feature_set_version != self.feature_set_version:
                raise ValueError("Every feature row must use the same feature_set_version as the matrix.")
            if row.target_drug is not None and row.target_drug != self.target_drug:
                raise ValueError("Every feature row target_drug must match the matrix target_drug when present.")
            if set(row.values) != feature_name_set:
                raise ValueError("Every feature row must provide exactly the declared feature_names.")
        return self


class TrainingLabelRow(ContractModel):
    sample_id: str = Field(min_length=3, max_length=80)
    organism: OrganismHint
    target_drug: str = Field(min_length=3, max_length=80)
    phenotype_label: PredictedPhenotype
    split_context: SplitContext
    split_id: str = Field(min_length=3, max_length=80)
    snapshot_id: str = Field(min_length=3, max_length=120)
    source_record_id: str = Field(min_length=3, max_length=120)
    label_source: str = Field(min_length=3, max_length=80)
    included_in_training: bool = True
    exclusion_reason: str | None = Field(default=None, min_length=3, max_length=160)

    @field_validator("sample_id", "target_drug", "split_id", "snapshot_id", "source_record_id", "label_source")
    @classmethod
    def normalize_slug_fields(cls, value: str) -> str:
        return normalize_slug_like(value)

    @field_validator("phenotype_label", mode="before")
    @classmethod
    def normalize_phenotype_label(
        cls,
        value: PredictedPhenotype | str,
    ) -> PredictedPhenotype | str:
        if isinstance(value, PredictedPhenotype):
            return value
        return normalize_slug_like(str(value))

    @model_validator(mode="after")
    def require_exclusion_reason_for_excluded_rows(self) -> "TrainingLabelRow":
        if not self.included_in_training and not self.exclusion_reason:
            raise ValueError("Excluded training rows must record an exclusion_reason.")
        return self


class TrainingLabelTable(ContractModel):
    organism: OrganismHint
    target_drug: str = Field(min_length=3, max_length=80)
    split_context: SplitContext
    split_id: str = Field(min_length=3, max_length=80)
    snapshot_id: str = Field(min_length=3, max_length=120)
    rows: list[TrainingLabelRow] = Field(default_factory=list)

    @field_validator("target_drug", "split_id", "snapshot_id")
    @classmethod
    def normalize_slug_fields(cls, value: str) -> str:
        return normalize_slug_like(value)

    @model_validator(mode="after")
    def ensure_consistent_rows(self) -> "TrainingLabelTable":
        if not self.rows:
            raise ValueError("TrainingLabelTable requires at least one row.")
        for row in self.rows:
            if row.organism != self.organism:
                raise ValueError("TrainingLabelTable rows must share the same organism.")
            if row.target_drug != self.target_drug:
                raise ValueError("TrainingLabelTable rows must share the same target_drug.")
            if row.split_context != self.split_context:
                raise ValueError("TrainingLabelTable rows must share the same split_context.")
            if row.split_id != self.split_id:
                raise ValueError("TrainingLabelTable rows must share the same split_id.")
            if row.snapshot_id != self.snapshot_id:
                raise ValueError("TrainingLabelTable rows must share the same snapshot_id.")
        return self


class BaselineModelArtifact(ContractModel):
    model_version: str = Field(min_length=3, max_length=80)
    feature_set_version: str = Field(min_length=3, max_length=80)
    organism: OrganismHint
    target_drug: str = Field(min_length=3, max_length=80)
    split_context: SplitContext
    split_id: str = Field(min_length=3, max_length=80)
    snapshot_id: str = Field(min_length=3, max_length=120)
    algorithm: str = Field(min_length=3, max_length=80)
    decision_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    bias: float
    feature_weights: dict[str, float] = Field(default_factory=dict)
    feature_centers: dict[str, float] = Field(default_factory=dict)
    training_sample_count: int = Field(ge=1)
    resistant_sample_count: int = Field(ge=0)
    susceptible_sample_count: int = Field(ge=0)
    calibration_status: CalibrationStatus = CalibrationStatus.NOT_AVAILABLE
    warnings: list[str] = Field(default_factory=list)

    @field_validator("model_version", "feature_set_version", "target_drug", "split_id", "snapshot_id", "algorithm")
    @classmethod
    def normalize_slug_fields(cls, value: str) -> str:
        return normalize_slug_like(value)

    @field_validator("feature_weights", "feature_centers")
    @classmethod
    def normalize_feature_maps(cls, value: dict[str, float]) -> dict[str, float]:
        normalized: dict[str, float] = {}
        for key, item in value.items():
            normalized[normalize_slug_like(key)] = round(float(item), 6)
        return normalized

    @model_validator(mode="after")
    def ensure_training_counts_are_usable(self) -> "BaselineModelArtifact":
        total = self.resistant_sample_count + self.susceptible_sample_count
        if total != self.training_sample_count:
            raise ValueError("Training sample counts must sum to training_sample_count.")
        if set(self.feature_weights) != set(self.feature_centers):
            raise ValueError("Model artifact must record centers for every weighted feature.")
        return self


class CalibrationPolicyArtifact(ContractModel):
    policy_version: str = Field(min_length=3, max_length=80)
    model_version: str = Field(min_length=3, max_length=80)
    feature_set_version: str = Field(min_length=3, max_length=80)
    calibration_status: CalibrationStatus = CalibrationStatus.NOT_AVAILABLE
    method: str = Field(min_length=3, max_length=120)
    minimum_samples_required: int = Field(ge=1)
    observed_training_sample_count: int = Field(ge=0)
    uncertainty_measure: str = Field(min_length=3, max_length=80)
    notes: list[str] = Field(default_factory=list)

    @field_validator("policy_version", "model_version", "feature_set_version", "method", "uncertainty_measure")
    @classmethod
    def normalize_slug_fields(cls, value: str) -> str:
        return normalize_slug_like(value)
