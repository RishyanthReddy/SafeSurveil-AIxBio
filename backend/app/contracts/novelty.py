from __future__ import annotations

from enum import Enum

from pydantic import Field, field_validator, model_validator

from .common import ContractModel, normalize_slug_like


class NoveltyBucket(str, Enum):
    KNOWN = "known"
    ELEVATED = "elevated"
    HIGH = "high"
    UNKNOWN = "unknown"


class NoveltyAssessment(ContractModel):
    job_id: str = Field(min_length=3, max_length=80)
    sample_id: str = Field(min_length=3, max_length=80)
    target_drug: str = Field(min_length=3, max_length=80)
    reference_snapshot_id: str = Field(min_length=3, max_length=120)
    nearest_neighbor_id: str | None = Field(default=None, min_length=3, max_length=120)
    nearest_neighbor_distance: float | None = Field(default=None, ge=0.0)
    novelty_score: float | None = Field(default=None, ge=0.0, le=1.0)
    novelty_percentile: float | None = Field(default=None, ge=0.0, le=100.0)
    novelty_bucket: NoveltyBucket = NoveltyBucket.UNKNOWN
    missing_reference: bool = False
    uncertainty_flag: bool = False
    warnings: list[str] = Field(default_factory=list)

    @field_validator("job_id", "sample_id", "target_drug", "reference_snapshot_id")
    @classmethod
    def normalize_slug_fields(cls, value: str) -> str:
        return normalize_slug_like(value)

    @field_validator("nearest_neighbor_id")
    @classmethod
    def normalize_optional_neighbor(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_slug_like(value)

    @field_validator("novelty_bucket", mode="before")
    @classmethod
    def normalize_novelty_bucket(cls, value: NoveltyBucket | str) -> NoveltyBucket | str:
        if isinstance(value, NoveltyBucket):
            return value
        return normalize_slug_like(str(value))

    @model_validator(mode="after")
    def require_metrics_or_sparse_flag(self) -> "NoveltyAssessment":
        has_metric = any(
            metric is not None
            for metric in (
                self.nearest_neighbor_distance,
                self.novelty_score,
                self.novelty_percentile,
            )
        )
        if not has_metric and not self.missing_reference:
            raise ValueError("Novelty metrics or missing_reference=True must be provided.")
        return self
