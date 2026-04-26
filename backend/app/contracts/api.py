from __future__ import annotations

from enum import Enum

from pydantic import Field, field_validator, model_validator

from .common import ContractModel, normalize_slug_like
from .copilot import CopilotResponse, SemanticUIObject
from .decision import DecisionObject
from .runtime import JobState, JobStatus, QueueItem
from .sample import SampleInput


class AnalyzeJobRequest(SampleInput):
    """API request mirror of the first local analyze endpoint."""


class AnalyzeJobResponse(ContractModel):
    job_id: str = Field(min_length=3, max_length=80)
    status: JobState

    @field_validator("job_id")
    @classmethod
    def normalize_job_id(cls, value: str) -> str:
        return normalize_slug_like(value)

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status_token(cls, value: JobState | str) -> JobState | str:
        if isinstance(value, JobState):
            return value
        return normalize_slug_like(str(value))


class JobDecisionResponse(ContractModel):
    job_status: JobStatus
    decision: DecisionObject

    @model_validator(mode="after")
    def ensure_status_matches_decision(self) -> "JobDecisionResponse":
        if self.job_status.sample_id != self.decision.sample.sample_id:
            raise ValueError("JobDecisionResponse job_status sample_id must match decision sample_id.")
        if self.job_status.target_drug != self.decision.sample.target_drug:
            raise ValueError("JobDecisionResponse job_status target_drug must match decision target_drug.")
        if self.decision.job_id is None:
            raise ValueError("JobDecisionResponse decision job_id is required for a job-scoped response.")
        if self.job_status.job_id != self.decision.job_id:
            raise ValueError("JobDecisionResponse job_status job_id must match decision job_id.")
        return self


class ArtifactPreviewResponse(ContractModel):
    job_id: str = Field(min_length=3, max_length=80)
    artifact_id: str = Field(min_length=3, max_length=120)
    media_type: str = Field(min_length=3, max_length=120)
    encoding: str = Field(min_length=3, max_length=40)
    content: str = Field(max_length=400000)
    truncated: bool = False
    size_bytes: int | None = Field(default=None, ge=0)

    @field_validator("job_id", "artifact_id", "encoding")
    @classmethod
    def normalize_slug_fields(cls, value: str) -> str:
        return normalize_slug_like(value)


class CopilotOutputMode(str, Enum):
    LIVE_LLM = "live_llm"
    MOCK = "mock"
    CACHED = "cached"
    FALLBACK = "fallback"


class CopilotOutputOrigin(ContractModel):
    mode: CopilotOutputMode
    provider: str | None = Field(default=None, min_length=2, max_length=80)
    detail: str | None = Field(default=None, min_length=3, max_length=160)

    @field_validator("mode", mode="before")
    @classmethod
    def normalize_mode_token(cls, value: CopilotOutputMode | str) -> CopilotOutputMode | str:
        if isinstance(value, CopilotOutputMode):
            return value
        return normalize_slug_like(str(value))

    @field_validator("provider")
    @classmethod
    def normalize_optional_provider(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_slug_like(value)


class JobCopilotResponse(ContractModel):
    job_status: JobStatus
    output_origin: CopilotOutputOrigin
    copilot: CopilotResponse

    @model_validator(mode="after")
    def ensure_status_matches_copilot(self) -> "JobCopilotResponse":
        if self.job_status.job_id != self.copilot.job_id:
            raise ValueError("JobCopilotResponse job_status job_id must match copilot job_id.")
        if self.job_status.sample_id != self.copilot.sample_id:
            raise ValueError("JobCopilotResponse job_status sample_id must match copilot sample_id.")
        if self.job_status.target_drug != self.copilot.target_drug:
            raise ValueError("JobCopilotResponse job_status target_drug must match copilot target_drug.")
        return self


class JobSemanticUIResponse(ContractModel):
    job_id: str = Field(min_length=3, max_length=80)
    output_origin: CopilotOutputOrigin
    semantic_ui: SemanticUIObject

    @field_validator("job_id")
    @classmethod
    def normalize_job_id(cls, value: str) -> str:
        return normalize_slug_like(value)


class ThesysC1RenderStatus(str, Enum):
    RENDERED = "rendered"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


class JobThesysC1Response(ContractModel):
    job_id: str = Field(min_length=3, max_length=80)
    status: ThesysC1RenderStatus
    output_origin: CopilotOutputOrigin
    semantic_ui: SemanticUIObject
    c1_response: str | None = Field(default=None, max_length=1_000_000)
    model: str | None = Field(default=None, min_length=3, max_length=160)
    reason: str | None = Field(default=None, min_length=3, max_length=240)
    fallback_required: bool = True

    @field_validator("job_id")
    @classmethod
    def normalize_job_id(cls, value: str) -> str:
        return normalize_slug_like(value)

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status_token(
        cls,
        value: ThesysC1RenderStatus | str,
    ) -> ThesysC1RenderStatus | str:
        if isinstance(value, ThesysC1RenderStatus):
            return value
        return normalize_slug_like(str(value))


class QueueSummaryResponse(ContractModel):
    items: list[QueueItem] = Field(default_factory=list)
