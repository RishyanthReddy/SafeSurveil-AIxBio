from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

from pydantic import Field, computed_field, field_validator, model_validator

from .common import ContractModel, normalize_slug_like
from .decision import RationaleCode, SeverityLevel, TriageOutcome


class JobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    EVIDENCE_READY = "evidence_ready"
    DECISION_READY = "decision_ready"
    FAILED = "failed"
    DEGRADED = "degraded"
    COMPLETED = "completed"


class ArtifactKind(str, Enum):
    INPUT_FASTA = "input_fasta"
    MECHANISTIC_EVIDENCE = "mechanistic_evidence"
    NOVELTY_SUMMARY = "novelty_summary"
    PREDICTION_SUMMARY = "prediction_summary"
    DECISION_OBJECT = "decision_object"
    COPILOT_OUTPUT = "copilot_output"
    SEMANTIC_UI = "semantic_ui"
    PLOT = "plot"
    SCREENSHOT = "screenshot"
    OTHER = "other"


class ArtifactRecord(ContractModel):
    artifact_id: str = Field(min_length=3, max_length=120)
    job_id: str = Field(min_length=3, max_length=80)
    sample_id: str = Field(min_length=3, max_length=80)
    target_drug: str = Field(min_length=3, max_length=80)
    kind: ArtifactKind
    path: str = Field(min_length=1, max_length=512)
    media_type: str = Field(min_length=3, max_length=120)
    generated_by: str = Field(min_length=3, max_length=80)
    sha256: str | None = Field(default=None, min_length=16, max_length=128)
    size_bytes: int | None = Field(default=None, ge=0)

    @field_validator("artifact_id", "job_id", "sample_id", "target_drug", "generated_by")
    @classmethod
    def normalize_slug_fields(cls, value: str) -> str:
        return normalize_slug_like(value)

    @field_validator("kind", mode="before")
    @classmethod
    def normalize_artifact_kind(cls, value: ArtifactKind | str) -> ArtifactKind | str:
        if isinstance(value, ArtifactKind):
            return value
        return normalize_slug_like(str(value))

    @computed_field
    @property
    def preview_eligible(self) -> bool:
        if self.media_type.startswith("image/"):
            return True
        if self.media_type.startswith("text/"):
            return True
        return self.media_type in {"application/json", "application/pdf"}


class ArtifactManifest(ContractModel):
    job_id: str = Field(min_length=3, max_length=80)
    sample_id: str = Field(min_length=3, max_length=80)
    target_drug: str = Field(min_length=3, max_length=80)
    artifact_root: str | None = Field(default=None, min_length=1, max_length=512)
    artifacts: list[ArtifactRecord] = Field(default_factory=list)

    @field_validator("job_id", "sample_id", "target_drug")
    @classmethod
    def normalize_slug_fields(cls, value: str) -> str:
        return normalize_slug_like(value)

    @model_validator(mode="after")
    def ensure_artifacts_match_manifest_context(self) -> "ArtifactManifest":
        for artifact in self.artifacts:
            if (
                artifact.job_id != self.job_id
                or artifact.sample_id != self.sample_id
                or artifact.target_drug != self.target_drug
            ):
                raise ValueError(
                    "ArtifactManifest artifacts must match manifest job_id, sample_id, and target_drug."
                )
        return self


class JobStatus(ContractModel):
    job_id: str = Field(min_length=3, max_length=80)
    sample_id: str = Field(min_length=3, max_length=80)
    target_drug: str = Field(min_length=3, max_length=80)
    status: JobState
    current_step: str | None = Field(default=None, min_length=3, max_length=120)
    failure_code: str | None = Field(default=None, min_length=3, max_length=80)
    warnings: list[str] = Field(default_factory=list)
    submitted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None

    @field_validator("job_id", "sample_id", "target_drug", "failure_code")
    @classmethod
    def normalize_optional_slug_fields(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_slug_like(value)

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status_token(cls, value: JobState | str) -> JobState | str:
        if isinstance(value, JobState):
            return value
        return normalize_slug_like(str(value))

    @model_validator(mode="after")
    def require_failure_code_for_failed_state(self) -> "JobStatus":
        if self.status == JobState.FAILED and self.failure_code is None:
            raise ValueError("failure_code is required when status is failed.")
        return self


class QueueItem(ContractModel):
    job_id: str = Field(min_length=3, max_length=80)
    sample_id: str = Field(min_length=3, max_length=80)
    target_drug: str = Field(min_length=3, max_length=80)
    triage: TriageOutcome
    severity: SeverityLevel
    status: JobState
    queue_priority: int = Field(ge=0)
    headline: str = Field(min_length=5, max_length=160)
    rationale_codes: list[RationaleCode] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("job_id", "sample_id", "target_drug")
    @classmethod
    def normalize_slug_fields(cls, value: str) -> str:
        return normalize_slug_like(value)

    @field_validator("triage", "severity", "status", mode="before")
    @classmethod
    def normalize_enum_tokens(
        cls,
        value: TriageOutcome | SeverityLevel | JobState | str,
    ) -> TriageOutcome | SeverityLevel | JobState | str:
        if isinstance(value, (TriageOutcome, SeverityLevel, JobState)):
            return value
        return normalize_slug_like(str(value))

    @field_validator("rationale_codes", mode="before")
    @classmethod
    def normalize_rationale_codes(
        cls,
        value: list[RationaleCode] | list[str],
    ) -> list[RationaleCode] | list[str]:
        normalized_values: list[RationaleCode] | list[str] = []
        for item in value:
            if isinstance(item, RationaleCode):
                normalized_values.append(item)
                continue
            normalized_values.append(normalize_slug_like(str(item)))
        return normalized_values
