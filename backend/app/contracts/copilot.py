from __future__ import annotations

from enum import Enum

from pydantic import Field, field_validator, model_validator

from .common import ContractModel, normalize_slug_like
from .decision import SeverityLevel, TriageOutcome
from .runtime import QueueItem


class AllowedEvidenceSource(str, Enum):
    DECISION_OBJECT = "decision_object"
    MECHANISTIC_EVIDENCE = "mechanistic_evidence"
    NOVELTY_ASSESSMENT = "novelty_assessment"
    PHENOTYPE_PREDICTION = "phenotype_prediction"
    ACTIONABILITY_FEATURES = "actionability_features"
    ARTIFACT_MANIFEST = "artifact_manifest"


class ContextSectionType(str, Enum):
    DECISION = "decision"
    EVIDENCE = "evidence"
    NOVELTY = "novelty"
    ACTIONABILITY = "actionability"
    LIMITATION = "limitation"
    QUESTION = "question"


class AnswerBlockType(str, Enum):
    SUMMARY = "summary"
    BULLETS = "bullets"
    NEXT_STEPS = "next_steps"
    REFUSAL = "refusal"


class ChartType(str, Enum):
    BAR = "bar"
    LINE = "line"
    AREA = "area"
    RADIAL = "radial"


class CopilotContextSection(ContractModel):
    section_id: str = Field(min_length=3, max_length=80)
    section_type: ContextSectionType
    title: str = Field(min_length=3, max_length=120)
    content: str = Field(min_length=10, max_length=4000)
    evidence_ids: list[str] = Field(default_factory=list)

    @field_validator("section_id")
    @classmethod
    def normalize_section_id(cls, value: str) -> str:
        return normalize_slug_like(value)

    @field_validator("section_type", mode="before")
    @classmethod
    def normalize_section_type(cls, value: ContextSectionType | str) -> ContextSectionType | str:
        if isinstance(value, ContextSectionType):
            return value
        return normalize_slug_like(str(value))

    @field_validator("evidence_ids")
    @classmethod
    def normalize_evidence_ids(cls, value: list[str]) -> list[str]:
        return [normalize_slug_like(item) for item in value]


class CopilotContext(ContractModel):
    sample_id: str = Field(min_length=3, max_length=80)
    job_id: str = Field(min_length=3, max_length=80)
    allowed_evidence_sources: list[AllowedEvidenceSource] = Field(default_factory=list)
    sections: list[CopilotContextSection] = Field(default_factory=list)
    user_question: str | None = Field(default=None, min_length=3, max_length=400)
    warnings: list[str] = Field(default_factory=list)

    @field_validator("sample_id", "job_id")
    @classmethod
    def normalize_slug_fields(cls, value: str) -> str:
        return normalize_slug_like(value)

    @field_validator("allowed_evidence_sources", mode="before")
    @classmethod
    def normalize_evidence_sources(
        cls,
        value: list[AllowedEvidenceSource] | list[str],
    ) -> list[AllowedEvidenceSource] | list[str]:
        normalized_values: list[AllowedEvidenceSource] | list[str] = []
        for item in value:
            if isinstance(item, AllowedEvidenceSource):
                normalized_values.append(item)
                continue
            normalized_values.append(normalize_slug_like(str(item)))
        return normalized_values

    @model_validator(mode="after")
    def require_context_sections(self) -> "CopilotContext":
        if not self.sections:
            raise ValueError("CopilotContext requires at least one section.")
        return self


class MetricDatum(ContractModel):
    key: str = Field(min_length=2, max_length=60)
    label: str = Field(min_length=2, max_length=60)
    value: float | int | str | bool
    unit: str | None = Field(default=None, max_length=24)

    @field_validator("key")
    @classmethod
    def normalize_metric_key(cls, value: str) -> str:
        return normalize_slug_like(value)


class DecisionCardBlock(ContractModel):
    title: str = Field(min_length=3, max_length=120)
    triage_decision: TriageOutcome
    severity: SeverityLevel
    summary: str = Field(min_length=10, max_length=400)
    metrics: list[MetricDatum] = Field(default_factory=list)

    @field_validator("triage_decision", "severity", mode="before")
    @classmethod
    def normalize_enum_tokens(
        cls,
        value: TriageOutcome | SeverityLevel | str,
    ) -> TriageOutcome | SeverityLevel | str:
        if isinstance(value, (TriageOutcome, SeverityLevel)):
            return value
        return normalize_slug_like(str(value))


class EvidenceTableRow(ContractModel):
    row_id: str = Field(min_length=3, max_length=80)
    label: str = Field(min_length=2, max_length=120)
    cells: dict[str, str | float | int | bool | None]
    evidence_id: str | None = Field(default=None, min_length=3, max_length=120)

    @field_validator("row_id", "evidence_id")
    @classmethod
    def normalize_optional_slug_fields(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_slug_like(value)


class EvidenceTableBlock(ContractModel):
    title: str = Field(min_length=3, max_length=120)
    columns: list[str] = Field(default_factory=list)
    rows: list[EvidenceTableRow] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_columns_and_rows(self) -> "EvidenceTableBlock":
        if not self.columns or not self.rows:
            raise ValueError("EvidenceTableBlock requires at least one column and one row.")
        return self


class RiskChartPoint(ContractModel):
    label: str = Field(min_length=2, max_length=80)
    value: float
    evidence_id: str | None = Field(default=None, min_length=3, max_length=120)

    @field_validator("evidence_id")
    @classmethod
    def normalize_optional_evidence_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_slug_like(value)


class RiskChartBlock(ContractModel):
    chart_id: str = Field(min_length=3, max_length=80)
    title: str = Field(min_length=3, max_length=120)
    chart_type: ChartType
    points: list[RiskChartPoint] = Field(default_factory=list)

    @field_validator("chart_id")
    @classmethod
    def normalize_chart_id(cls, value: str) -> str:
        return normalize_slug_like(value)

    @field_validator("chart_type", mode="before")
    @classmethod
    def normalize_chart_type(cls, value: ChartType | str) -> ChartType | str:
        if isinstance(value, ChartType):
            return value
        return normalize_slug_like(str(value))

    @model_validator(mode="after")
    def require_points(self) -> "RiskChartBlock":
        if not self.points:
            raise ValueError("RiskChartBlock requires at least one point.")
        return self


class SafetyProfileAxis(ContractModel):
    label: str = Field(min_length=2, max_length=80)
    value: float = Field(ge=0.0, le=1.0)


class SafetyProfileBlock(ContractModel):
    title: str = Field(min_length=3, max_length=120)
    axes: list[SafetyProfileAxis] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_axes(self) -> "SafetyProfileBlock":
        if not self.axes:
            raise ValueError("SafetyProfileBlock requires at least one axis.")
        return self


class QueueBlock(ContractModel):
    title: str = Field(min_length=3, max_length=120)
    items: list[QueueItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_queue_items(self) -> "QueueBlock":
        if not self.items:
            raise ValueError("QueueBlock requires at least one queue item.")
        return self


class SemanticUIObject(ContractModel):
    decision_card: DecisionCardBlock | None = None
    evidence_table: EvidenceTableBlock | None = None
    risk_charts: list[RiskChartBlock] = Field(default_factory=list)
    safety_profile: SafetyProfileBlock | None = None
    queue_block: QueueBlock | None = None
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_primary_block(self) -> "SemanticUIObject":
        has_primary_block = any(
            block is not None
            for block in (
                self.decision_card,
                self.evidence_table,
                self.safety_profile,
                self.queue_block,
            )
        ) or bool(self.risk_charts)
        if not has_primary_block:
            raise ValueError("SemanticUIObject requires at least one renderable block.")
        return self


class CopilotAnswerBlock(ContractModel):
    block_id: str = Field(min_length=3, max_length=80)
    block_type: AnswerBlockType
    title: str | None = Field(default=None, min_length=3, max_length=120)
    content: str = Field(min_length=5, max_length=2000)
    cited_evidence_ids: list[str] = Field(default_factory=list)

    @field_validator("block_id")
    @classmethod
    def normalize_block_id(cls, value: str) -> str:
        return normalize_slug_like(value)

    @field_validator("block_type", mode="before")
    @classmethod
    def normalize_block_type(cls, value: AnswerBlockType | str) -> AnswerBlockType | str:
        if isinstance(value, AnswerBlockType):
            return value
        return normalize_slug_like(str(value))

    @field_validator("cited_evidence_ids")
    @classmethod
    def normalize_cited_evidence_ids(cls, value: list[str]) -> list[str]:
        return [normalize_slug_like(item) for item in value]


class CopilotResponse(ContractModel):
    job_id: str = Field(min_length=3, max_length=80)
    sample_id: str = Field(min_length=3, max_length=80)
    target_drug: str = Field(min_length=3, max_length=80)
    summary: str | None = Field(default=None, min_length=10, max_length=2000)
    next_steps: list[str] = Field(default_factory=list)
    refusal_required: bool = False
    refusal_reason: str | None = Field(default=None, min_length=10, max_length=400)
    cited_evidence_ids: list[str] = Field(default_factory=list)
    answer_blocks: list[CopilotAnswerBlock] = Field(default_factory=list)
    semantic_ui: SemanticUIObject | None = None
    warnings: list[str] = Field(default_factory=list)

    @field_validator("job_id", "sample_id", "target_drug")
    @classmethod
    def normalize_slug_fields(cls, value: str) -> str:
        return normalize_slug_like(value)

    @field_validator("cited_evidence_ids")
    @classmethod
    def normalize_cited_evidence_ids(cls, value: list[str]) -> list[str]:
        return [normalize_slug_like(item) for item in value]

    @model_validator(mode="after")
    def validate_grounding_and_refusal(self) -> "CopilotResponse":
        if self.refusal_required and self.refusal_reason is None:
            raise ValueError("refusal_reason is required when refusal_required is true.")
        if not self.refusal_required and not self.cited_evidence_ids:
            raise ValueError("Grounded copilot responses must cite at least one evidence ID.")
        if not self.refusal_required and self.summary is None and not self.answer_blocks:
            raise ValueError("CopilotResponse requires a summary or answer block when not refusing.")
        return self
