from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .common import ProvenanceSource, SourceContext, SplitContext, normalize_slug_like
from .decision import SeverityLevel, TriageOutcome

EXECUTION_GATE_SCHEMA_VERSION = "v2.execution_gate.v1"
REASONING_TRACE_SCHEMA_VERSION = "v2.reasoning_trace.v1"
EVIDENCE_GRAPH_SCHEMA_VERSION = "v2.evidence_graph.v1"
V2_AUDIT_BUNDLE_SCHEMA_VERSION = "v2.audit_bundle.v1"
_SHA256_DIGEST_PATTERN = re.compile(r"^sha256:[a-f0-9]{64}$")
_V2_SCHEMA_PATTERN = re.compile(r"^v2\.[a-z0-9_]+\.[v][0-9]+$")
_SECRET_LIKE_PATTERN = re.compile(
    r"(api[_-]?key|authorization|bearer|credential|password|secret|token)",
    re.IGNORECASE,
)


class V2ContractModel(BaseModel):
    """Shared base for V2 audit contracts that use named schema identifiers."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        str_strip_whitespace=True,
        validate_assignment=True,
        use_enum_values=False,
    )

    schema_version: str = Field(min_length=8, max_length=80)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("schema_version")
    @classmethod
    def validate_v2_schema_version(cls, value: str) -> str:
        if not _V2_SCHEMA_PATTERN.fullmatch(value):
            raise ValueError("schema_version must use a V2 named schema such as v2.execution_gate.v1.")
        return value


class ReasoningTraceStepType(str, Enum):
    SAMPLE_CONTEXT = "sample_context"
    PHENOTYPE_PREDICTION = "phenotype_prediction"
    MECHANISTIC_EVIDENCE = "mechanistic_evidence"
    MECHANISM_DRUG_INTERPRETATION = "mechanism_drug_interpretation"
    NOVELTY_LINEAGE_SHIFT = "novelty_lineage_shift"
    QC_METADATA_LIMITATIONS = "qc_metadata_limitations"
    ACTIONABILITY_POLICY = "actionability_policy"
    FINAL_TRIAGE = "final_triage"


REASONING_TRACE_REQUIRED_STEP_TYPES: tuple[ReasoningTraceStepType, ...] = (
    ReasoningTraceStepType.SAMPLE_CONTEXT,
    ReasoningTraceStepType.PHENOTYPE_PREDICTION,
    ReasoningTraceStepType.MECHANISTIC_EVIDENCE,
    ReasoningTraceStepType.MECHANISM_DRUG_INTERPRETATION,
    ReasoningTraceStepType.NOVELTY_LINEAGE_SHIFT,
    ReasoningTraceStepType.QC_METADATA_LIMITATIONS,
    ReasoningTraceStepType.ACTIONABILITY_POLICY,
    ReasoningTraceStepType.FINAL_TRIAGE,
)


class ReasoningTraceStepStatus(str, Enum):
    GROUNDED = "grounded"
    CAVEATED = "caveated"


class ReasoningTraceCaveatSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    LIMITATION = "limitation"


class ReasoningTraceSourceRef(V2ContractModel):
    schema_version: str = Field(default=REASONING_TRACE_SCHEMA_VERSION)
    evidence_id: str = Field(min_length=3, max_length=120)
    source_type: str = Field(default="context", min_length=3, max_length=80)
    label: str | None = Field(default=None, min_length=3, max_length=160)
    detail: str | None = Field(default=None, min_length=3, max_length=400)

    @field_validator("evidence_id", "source_type")
    @classmethod
    def normalize_slug_fields(cls, value: str) -> str:
        return normalize_slug_like(value)


class ReasoningTraceCaveat(V2ContractModel):
    schema_version: str = Field(default=REASONING_TRACE_SCHEMA_VERSION)
    caveat_id: str = Field(min_length=3, max_length=80)
    severity: ReasoningTraceCaveatSeverity
    title: str = Field(min_length=3, max_length=120)
    detail: str = Field(min_length=5, max_length=1000)
    evidence_refs: list[str] = Field(default_factory=list)

    @field_validator("caveat_id")
    @classmethod
    def normalize_caveat_id(cls, value: str) -> str:
        return normalize_slug_like(value)

    @field_validator("severity", mode="before")
    @classmethod
    def normalize_severity(cls, value: ReasoningTraceCaveatSeverity | str) -> ReasoningTraceCaveatSeverity | str:
        if isinstance(value, ReasoningTraceCaveatSeverity):
            return value
        return normalize_slug_like(str(value))

    @field_validator("evidence_refs")
    @classmethod
    def normalize_evidence_refs(cls, value: list[str]) -> list[str]:
        return [normalize_slug_like(item) for item in value]


class ReasoningTraceStep(V2ContractModel):
    schema_version: str = Field(default=REASONING_TRACE_SCHEMA_VERSION)
    step_number: int = Field(ge=1, le=20)
    step_type: ReasoningTraceStepType
    title: str = Field(min_length=3, max_length=120)
    text: str = Field(min_length=10, max_length=1200)
    status: ReasoningTraceStepStatus = ReasoningTraceStepStatus.GROUNDED
    evidence_refs: list[ReasoningTraceSourceRef] = Field(default_factory=list)
    caveat_ids: list[str] = Field(default_factory=list)

    @field_validator("step_type", mode="before")
    @classmethod
    def normalize_step_type(cls, value: ReasoningTraceStepType | str) -> ReasoningTraceStepType | str:
        if isinstance(value, ReasoningTraceStepType):
            return value
        return normalize_slug_like(str(value))

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value: ReasoningTraceStepStatus | str) -> ReasoningTraceStepStatus | str:
        if isinstance(value, ReasoningTraceStepStatus):
            return value
        return normalize_slug_like(str(value))

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def coerce_evidence_refs(
        cls,
        value: list[str | dict[str, Any] | ReasoningTraceSourceRef] | None,
    ) -> list[str | dict[str, Any] | ReasoningTraceSourceRef]:
        if value is None:
            return []
        return [{"evidence_id": item} if isinstance(item, str) else item for item in value]

    @field_validator("caveat_ids")
    @classmethod
    def normalize_caveat_ids(cls, value: list[str]) -> list[str]:
        return [normalize_slug_like(item) for item in value]

    @model_validator(mode="after")
    def require_grounded_source_refs(self) -> "ReasoningTraceStep":
        if not self.evidence_refs:
            raise ValueError("ReasoningTraceStep requires at least one evidence reference.")
        return self


class ReasoningTraceCoverage(V2ContractModel):
    schema_version: str = Field(default=REASONING_TRACE_SCHEMA_VERSION)
    required_step_types: list[ReasoningTraceStepType] = Field(
        default_factory=lambda: list(REASONING_TRACE_REQUIRED_STEP_TYPES)
    )
    present_step_types: list[ReasoningTraceStepType] = Field(default_factory=list)
    missing_step_types: list[ReasoningTraceStepType] = Field(default_factory=list)
    required_steps: int = Field(ge=1, le=20)
    present_steps: int = Field(ge=0, le=20)
    coverage_ratio: float = Field(ge=0.0, le=1.0)

    @field_validator("required_step_types", "present_step_types", "missing_step_types", mode="before")
    @classmethod
    def normalize_step_type_list(
        cls,
        value: list[ReasoningTraceStepType | str],
    ) -> list[ReasoningTraceStepType | str]:
        return [item if isinstance(item, ReasoningTraceStepType) else normalize_slug_like(str(item)) for item in value]

    @model_validator(mode="after")
    def validate_coverage(self) -> "ReasoningTraceCoverage":
        required = list(self.required_step_types)
        present = list(self.present_step_types)
        missing = list(self.missing_step_types)

        if len(set(required)) != len(required):
            raise ValueError("required_step_types must not contain duplicates.")
        if len(set(present)) != len(present):
            raise ValueError("present_step_types must not contain duplicates.")
        if len(set(missing)) != len(missing):
            raise ValueError("missing_step_types must not contain duplicates.")

        required_set = set(required)
        present_set = set(present)
        missing_set = set(missing)
        if not present_set.issubset(required_set):
            raise ValueError("present_step_types must be a subset of required_step_types.")
        if not missing_set.issubset(required_set):
            raise ValueError("missing_step_types must be a subset of required_step_types.")
        if present_set & missing_set:
            raise ValueError("present_step_types and missing_step_types must not overlap.")
        if required_set != present_set | missing_set:
            raise ValueError("present and missing step types must account for all required step types.")
        if self.required_steps != len(required):
            raise ValueError("required_steps must match required_step_types length.")
        if self.present_steps != len(present):
            raise ValueError("present_steps must match present_step_types length.")

        expected_ratio = 1.0 if not required else len(present) / len(required)
        if abs(self.coverage_ratio - expected_ratio) > 0.001:
            raise ValueError("coverage_ratio must match present_steps / required_steps.")
        return self


class ReasoningTrace(V2ContractModel):
    schema_version: str = Field(default=REASONING_TRACE_SCHEMA_VERSION)
    job_id: str = Field(min_length=3, max_length=80)
    sample_id: str = Field(min_length=3, max_length=80)
    target_drug: str = Field(min_length=3, max_length=80)
    decision: TriageOutcome
    severity: SeverityLevel
    summary: str = Field(min_length=10, max_length=1000)
    steps: list[ReasoningTraceStep] = Field(min_length=1)
    coverage: ReasoningTraceCoverage
    caveats: list[ReasoningTraceCaveat] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("job_id", "sample_id", "target_drug")
    @classmethod
    def normalize_slug_fields(cls, value: str) -> str:
        return normalize_slug_like(value)

    @field_validator("decision", "severity", mode="before")
    @classmethod
    def normalize_enum_tokens(cls, value: TriageOutcome | SeverityLevel | str) -> TriageOutcome | SeverityLevel | str:
        if isinstance(value, (TriageOutcome, SeverityLevel)):
            return value
        return normalize_slug_like(str(value))

    @model_validator(mode="after")
    def validate_trace_structure(self) -> "ReasoningTrace":
        step_numbers = [step.step_number for step in self.steps]
        expected_numbers = list(range(1, len(self.steps) + 1))
        if step_numbers != expected_numbers:
            raise ValueError("ReasoningTrace steps must use sequential step_number values starting at 1.")

        step_types = [step.step_type for step in self.steps]
        if len(set(step_types)) != len(step_types):
            raise ValueError("ReasoningTrace steps must not repeat step_type values.")

        required_order = {step_type: index for index, step_type in enumerate(REASONING_TRACE_REQUIRED_STEP_TYPES)}
        observed_order = [required_order[step_type] for step_type in step_types]
        if observed_order != sorted(observed_order):
            raise ValueError("ReasoningTrace steps must follow the required biological reasoning order.")

        if set(step_types) != set(self.coverage.present_step_types):
            raise ValueError("ReasoningTrace coverage present_step_types must match the trace steps.")

        caveat_ids = [caveat.caveat_id for caveat in self.caveats]
        if len(set(caveat_ids)) != len(caveat_ids):
            raise ValueError("ReasoningTrace caveats must not repeat caveat_id values.")
        unknown_caveat_ids = {
            caveat_id for step in self.steps for caveat_id in step.caveat_ids if caveat_id not in set(caveat_ids)
        }
        if unknown_caveat_ids:
            raise ValueError("ReasoningTrace step caveat_ids must reference declared caveats.")
        return self


class EvidenceGraphNodeClass(str, Enum):
    SAMPLE = "sample"
    ORGANISM = "organism"
    DRUG = "drug"
    GENE = "gene"
    MECHANISM = "mechanism"
    PHENOTYPE_PREDICTION = "phenotype_prediction"
    NOVELTY = "novelty"
    QUALITY_CONTROL = "quality_control"
    ACTIONABILITY = "actionability"
    RATIONALE = "rationale"
    DECISION = "decision"
    ARTIFACT = "artifact"
    CITATION = "citation"
    COPILOT = "copilot"
    EXECUTION_GATE = "execution_gate"
    REASONING_TRACE = "reasoning_trace"
    POLICY = "policy"
    WARNING = "warning"


EVIDENCE_GRAPH_REQUIRED_NODE_CLASSES: tuple[EvidenceGraphNodeClass, ...] = (
    EvidenceGraphNodeClass.SAMPLE,
    EvidenceGraphNodeClass.ORGANISM,
    EvidenceGraphNodeClass.DRUG,
    EvidenceGraphNodeClass.PHENOTYPE_PREDICTION,
    EvidenceGraphNodeClass.NOVELTY,
    EvidenceGraphNodeClass.QUALITY_CONTROL,
    EvidenceGraphNodeClass.ACTIONABILITY,
    EvidenceGraphNodeClass.DECISION,
)


class EvidenceGraphEdgeClass(str, Enum):
    HAS_CONTEXT = "has_context"
    TARGETS = "targets"
    PREDICTS = "predicts"
    DETECTS = "detects"
    ASSOCIATED_WITH = "associated_with"
    SUPPORTS = "supports"
    INFORMS = "informs"
    CONSTRAINS = "constrains"
    TRIAGES_AS = "triages_as"
    CITES = "cites"
    GENERATED_ARTIFACT = "generated_artifact"
    VERIFIED_BY = "verified_by"
    EXPLAINS = "explains"
    CAVEATS = "caveats"
    LINKED_TO = "linked_to"


class EvidenceGraphClusterClass(str, Enum):
    CASE_CONTEXT = "case_context"
    MECHANISTIC_EVIDENCE = "mechanistic_evidence"
    RISK_SIGNALS = "risk_signals"
    POLICY_AND_TRIAGE = "policy_and_triage"
    AI_SIDECARS = "ai_sidecars"
    AUDIT = "audit"


class EvidenceGraphStyleTone(str, Enum):
    NEUTRAL = "neutral"
    SAMPLE = "sample"
    EVIDENCE = "evidence"
    RISK = "risk"
    POLICY = "policy"
    AI = "ai"
    GATE = "gate"
    DECISION = "decision"
    CAVEAT = "caveat"


class EvidenceGraphDetailField(V2ContractModel):
    schema_version: str = Field(default=EVIDENCE_GRAPH_SCHEMA_VERSION)
    key: str = Field(min_length=2, max_length=80)
    label: str = Field(min_length=2, max_length=120)
    value: str | float | int | bool | None = None
    value_kind: str = Field(default="text", min_length=2, max_length=40)

    @field_validator("key", "value_kind")
    @classmethod
    def normalize_slug_fields(cls, value: str) -> str:
        return normalize_slug_like(value)

    @field_validator("key", "label", "value_kind")
    @classmethod
    def reject_secret_like_text(cls, value: str) -> str:
        if _SECRET_LIKE_PATTERN.search(value):
            raise ValueError("Evidence graph detail fields must not expose secret-like keys or labels.")
        return value

    @field_validator("value")
    @classmethod
    def reject_secret_like_values(cls, value: str | float | int | bool | None) -> str | float | int | bool | None:
        if isinstance(value, str):
            if len(value) > 600:
                raise ValueError("Evidence graph detail string values must be concise for browser display.")
            if _SECRET_LIKE_PATTERN.search(value):
                raise ValueError("Evidence graph detail values must not expose secret-like content.")
        return value


class EvidenceGraphStyleHint(V2ContractModel):
    schema_version: str = Field(default=EVIDENCE_GRAPH_SCHEMA_VERSION)
    tone: EvidenceGraphStyleTone = EvidenceGraphStyleTone.NEUTRAL
    color_token: str | None = Field(default=None, min_length=2, max_length=80)
    icon: str | None = Field(default=None, min_length=2, max_length=80)
    importance: int = Field(default=1, ge=1, le=5)

    @field_validator("tone", mode="before")
    @classmethod
    def normalize_tone(cls, value: EvidenceGraphStyleTone | str) -> EvidenceGraphStyleTone | str:
        if isinstance(value, EvidenceGraphStyleTone):
            return value
        return normalize_slug_like(str(value))

    @field_validator("color_token", "icon")
    @classmethod
    def normalize_optional_tokens(cls, value: str | None) -> str | None:
        return normalize_slug_like(value) if value else value


class EvidenceGraphNode(V2ContractModel):
    schema_version: str = Field(default=EVIDENCE_GRAPH_SCHEMA_VERSION)
    node_id: str = Field(min_length=3, max_length=120)
    node_class: EvidenceGraphNodeClass
    label: str = Field(min_length=1, max_length=160)
    summary: str | None = Field(default=None, min_length=3, max_length=500)
    details: list[EvidenceGraphDetailField] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    artifact_refs: list[str] = Field(default_factory=list)
    style: EvidenceGraphStyleHint = Field(default_factory=EvidenceGraphStyleHint)

    @field_validator("node_id")
    @classmethod
    def normalize_node_id(cls, value: str) -> str:
        return normalize_slug_like(value)

    @field_validator("node_class", mode="before")
    @classmethod
    def normalize_node_class(cls, value: EvidenceGraphNodeClass | str) -> EvidenceGraphNodeClass | str:
        if isinstance(value, EvidenceGraphNodeClass):
            return value
        return normalize_slug_like(str(value))

    @field_validator("evidence_refs", "artifact_refs")
    @classmethod
    def normalize_ref_ids(cls, value: list[str]) -> list[str]:
        return [normalize_slug_like(item) for item in value]


class EvidenceGraphEdge(V2ContractModel):
    schema_version: str = Field(default=EVIDENCE_GRAPH_SCHEMA_VERSION)
    edge_id: str = Field(min_length=3, max_length=140)
    edge_class: EvidenceGraphEdgeClass
    source: str = Field(min_length=3, max_length=120)
    target: str = Field(min_length=3, max_length=120)
    label: str = Field(min_length=2, max_length=160)
    summary: str | None = Field(default=None, min_length=3, max_length=500)
    evidence_refs: list[str] = Field(default_factory=list)
    weight: float = Field(default=1.0, ge=0.0, le=1.0)
    directed: bool = True
    style: EvidenceGraphStyleHint = Field(default_factory=EvidenceGraphStyleHint)

    @field_validator("edge_id", "source", "target")
    @classmethod
    def normalize_edge_tokens(cls, value: str) -> str:
        return normalize_slug_like(value)

    @field_validator("edge_class", mode="before")
    @classmethod
    def normalize_edge_class(cls, value: EvidenceGraphEdgeClass | str) -> EvidenceGraphEdgeClass | str:
        if isinstance(value, EvidenceGraphEdgeClass):
            return value
        return normalize_slug_like(str(value))

    @field_validator("evidence_refs")
    @classmethod
    def normalize_evidence_refs(cls, value: list[str]) -> list[str]:
        return [normalize_slug_like(item) for item in value]


class EvidenceGraphCluster(V2ContractModel):
    schema_version: str = Field(default=EVIDENCE_GRAPH_SCHEMA_VERSION)
    cluster_id: str = Field(min_length=3, max_length=120)
    cluster_class: EvidenceGraphClusterClass
    label: str = Field(min_length=2, max_length=160)
    summary: str | None = Field(default=None, min_length=3, max_length=500)
    node_ids: list[str] = Field(min_length=1)
    style: EvidenceGraphStyleHint = Field(default_factory=EvidenceGraphStyleHint)

    @field_validator("cluster_id")
    @classmethod
    def normalize_cluster_id(cls, value: str) -> str:
        return normalize_slug_like(value)

    @field_validator("cluster_class", mode="before")
    @classmethod
    def normalize_cluster_class(cls, value: EvidenceGraphClusterClass | str) -> EvidenceGraphClusterClass | str:
        if isinstance(value, EvidenceGraphClusterClass):
            return value
        return normalize_slug_like(str(value))

    @field_validator("node_ids")
    @classmethod
    def normalize_node_ids(cls, value: list[str]) -> list[str]:
        return [normalize_slug_like(item) for item in value]

    @model_validator(mode="after")
    def reject_duplicate_node_ids(self) -> "EvidenceGraphCluster":
        if len(set(self.node_ids)) != len(self.node_ids):
            raise ValueError("EvidenceGraphCluster node_ids must not contain duplicates.")
        return self


class EvidenceGraphStats(V2ContractModel):
    schema_version: str = Field(default=EVIDENCE_GRAPH_SCHEMA_VERSION)
    node_count: int = Field(ge=0)
    edge_count: int = Field(ge=0)
    cluster_count: int = Field(ge=0)
    evidence_nodes: int = Field(ge=0)
    citation_nodes: int = Field(ge=0)
    artifact_nodes: int = Field(default=0, ge=0)
    linked_artifact_nodes: int = Field(default=0, ge=0)
    artifact_linkage_ratio: float = Field(default=1.0, ge=0.0, le=1.0)
    linked_citation_nodes: int = Field(default=0, ge=0)
    citation_linkage_ratio: float = Field(default=1.0, ge=0.0, le=1.0)
    warning_nodes: int = Field(default=0, ge=0)
    connected_component_count: int = Field(default=0, ge=0)
    weakly_connected: bool = True
    isolated_node_count: int = Field(default=0, ge=0)
    isolated_node_ids: list[str] = Field(default_factory=list)
    required_node_classes: list[EvidenceGraphNodeClass] = Field(
        default_factory=lambda: list(EVIDENCE_GRAPH_REQUIRED_NODE_CLASSES)
    )
    present_node_classes: list[EvidenceGraphNodeClass] = Field(default_factory=list)
    missing_node_classes: list[EvidenceGraphNodeClass] = Field(default_factory=list)
    completeness_ratio: float = Field(ge=0.0, le=1.0)

    @field_validator("required_node_classes", "present_node_classes", "missing_node_classes", mode="before")
    @classmethod
    def normalize_node_class_list(
        cls,
        value: list[EvidenceGraphNodeClass | str],
    ) -> list[EvidenceGraphNodeClass | str]:
        return [item if isinstance(item, EvidenceGraphNodeClass) else normalize_slug_like(str(item)) for item in value]

    @field_validator("isolated_node_ids")
    @classmethod
    def normalize_isolated_node_ids(cls, value: list[str]) -> list[str]:
        return [normalize_slug_like(item) for item in value]

    @model_validator(mode="after")
    def validate_completeness_sets(self) -> "EvidenceGraphStats":
        required = list(self.required_node_classes)
        present = list(self.present_node_classes)
        missing = list(self.missing_node_classes)

        if len(set(required)) != len(required):
            raise ValueError("required_node_classes must not contain duplicates.")
        if len(set(present)) != len(present):
            raise ValueError("present_node_classes must not contain duplicates.")
        if len(set(missing)) != len(missing):
            raise ValueError("missing_node_classes must not contain duplicates.")

        required_set = set(required)
        present_set = set(present)
        missing_set = set(missing)
        if not present_set.issubset(required_set):
            raise ValueError("present_node_classes must be a subset of required_node_classes.")
        if not missing_set.issubset(required_set):
            raise ValueError("missing_node_classes must be a subset of required_node_classes.")
        if present_set & missing_set:
            raise ValueError("present_node_classes and missing_node_classes must not overlap.")
        if required_set != present_set | missing_set:
            raise ValueError("present and missing node classes must account for all required node classes.")

        expected_ratio = 1.0 if not required else len(present) / len(required)
        if abs(self.completeness_ratio - expected_ratio) > 0.001:
            raise ValueError("completeness_ratio must match present required node classes / required node classes.")
        if self.linked_artifact_nodes > self.artifact_nodes:
            raise ValueError("linked_artifact_nodes cannot exceed artifact_nodes.")
        expected_artifact_ratio = 1.0 if self.artifact_nodes == 0 else self.linked_artifact_nodes / self.artifact_nodes
        if abs(self.artifact_linkage_ratio - expected_artifact_ratio) > 0.001:
            raise ValueError("artifact_linkage_ratio must match linked_artifact_nodes / artifact_nodes.")
        if self.linked_citation_nodes > self.citation_nodes:
            raise ValueError("linked_citation_nodes cannot exceed citation_nodes.")
        expected_citation_ratio = 1.0 if self.citation_nodes == 0 else self.linked_citation_nodes / self.citation_nodes
        if abs(self.citation_linkage_ratio - expected_citation_ratio) > 0.001:
            raise ValueError("citation_linkage_ratio must match linked_citation_nodes / citation_nodes.")
        if self.connected_component_count > self.node_count:
            raise ValueError("connected_component_count cannot exceed node_count.")
        if self.node_count == 0 and self.connected_component_count != 0:
            raise ValueError("connected_component_count must be zero when node_count is zero.")
        if self.node_count > 0 and self.connected_component_count == 0:
            raise ValueError("connected_component_count must be positive when node_count is positive.")
        if self.weakly_connected != (self.connected_component_count <= 1):
            raise ValueError("weakly_connected must match connected_component_count.")
        if self.isolated_node_count != len(self.isolated_node_ids):
            raise ValueError("isolated_node_count must match isolated_node_ids length.")
        if len(set(self.isolated_node_ids)) != len(self.isolated_node_ids):
            raise ValueError("isolated_node_ids must not contain duplicates.")
        if self.isolated_node_count > self.node_count:
            raise ValueError("isolated_node_count cannot exceed node_count.")
        return self


class EvidenceGraph(V2ContractModel):
    schema_version: str = Field(default=EVIDENCE_GRAPH_SCHEMA_VERSION)
    job_id: str = Field(min_length=3, max_length=80)
    sample_id: str = Field(min_length=3, max_length=80)
    target_drug: str = Field(min_length=3, max_length=80)
    nodes: list[EvidenceGraphNode] = Field(min_length=1)
    edges: list[EvidenceGraphEdge] = Field(default_factory=list)
    clusters: list[EvidenceGraphCluster] = Field(default_factory=list)
    stats: EvidenceGraphStats
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("job_id", "sample_id", "target_drug")
    @classmethod
    def normalize_slug_fields(cls, value: str) -> str:
        return normalize_slug_like(value)

    @model_validator(mode="after")
    def validate_graph_references_and_stats(self) -> "EvidenceGraph":
        node_ids = [node.node_id for node in self.nodes]
        if len(set(node_ids)) != len(node_ids):
            raise ValueError("EvidenceGraph nodes must not repeat node_id values.")

        edge_ids = [edge.edge_id for edge in self.edges]
        if len(set(edge_ids)) != len(edge_ids):
            raise ValueError("EvidenceGraph edges must not repeat edge_id values.")

        node_id_set = set(node_ids)
        unknown_edge_refs = {
            node_id
            for edge in self.edges
            for node_id in (edge.source, edge.target)
            if node_id not in node_id_set
        }
        if unknown_edge_refs:
            raise ValueError("EvidenceGraph edges must reference existing node IDs.")
        if any(edge.source == edge.target for edge in self.edges):
            raise ValueError("EvidenceGraph edges must not be self-referential.")

        unknown_cluster_refs = {
            node_id for cluster in self.clusters for node_id in cluster.node_ids if node_id not in node_id_set
        }
        if unknown_cluster_refs:
            raise ValueError("EvidenceGraph clusters must reference existing node IDs.")

        if self.stats.node_count != len(self.nodes):
            raise ValueError("EvidenceGraphStats node_count must match graph nodes.")
        if self.stats.edge_count != len(self.edges):
            raise ValueError("EvidenceGraphStats edge_count must match graph edges.")
        if self.stats.cluster_count != len(self.clusters):
            raise ValueError("EvidenceGraphStats cluster_count must match graph clusters.")

        evidence_node_classes = {
            EvidenceGraphNodeClass.GENE,
            EvidenceGraphNodeClass.MECHANISM,
            EvidenceGraphNodeClass.ARTIFACT,
        }
        evidence_node_count = sum(1 for node in self.nodes if node.node_class in evidence_node_classes)
        if self.stats.evidence_nodes != evidence_node_count:
            raise ValueError("EvidenceGraphStats evidence_nodes must match evidence-like graph nodes.")

        citation_node_count = sum(1 for node in self.nodes if node.node_class == EvidenceGraphNodeClass.CITATION)
        if self.stats.citation_nodes != citation_node_count:
            raise ValueError("EvidenceGraphStats citation_nodes must match citation graph nodes.")

        artifact_node_count = sum(1 for node in self.nodes if node.node_class == EvidenceGraphNodeClass.ARTIFACT)
        if self.stats.artifact_nodes != artifact_node_count:
            raise ValueError("EvidenceGraphStats artifact_nodes must match artifact graph nodes.")

        warning_node_count = sum(1 for node in self.nodes if node.node_class == EvidenceGraphNodeClass.WARNING)
        if self.stats.warning_nodes != warning_node_count:
            raise ValueError("EvidenceGraphStats warning_nodes must match warning graph nodes.")

        incident_node_ids = {
            node_id
            for edge in self.edges
            for node_id in (edge.source, edge.target)
        }
        artifact_node_ids = {node.node_id for node in self.nodes if node.node_class == EvidenceGraphNodeClass.ARTIFACT}
        linked_artifact_node_count = len(artifact_node_ids & incident_node_ids)
        if self.stats.linked_artifact_nodes != linked_artifact_node_count:
            raise ValueError("EvidenceGraphStats linked_artifact_nodes must match linked artifact graph nodes.")

        citation_node_ids = {node.node_id for node in self.nodes if node.node_class == EvidenceGraphNodeClass.CITATION}
        linked_citation_node_count = len(citation_node_ids & incident_node_ids)
        if self.stats.linked_citation_nodes != linked_citation_node_count:
            raise ValueError("EvidenceGraphStats linked_citation_nodes must match linked citation graph nodes.")

        adjacency = {node_id: set() for node_id in node_ids}
        for edge in self.edges:
            adjacency[edge.source].add(edge.target)
            adjacency[edge.target].add(edge.source)

        isolated_node_ids = sorted(node_id for node_id, neighbors in adjacency.items() if not neighbors)
        if self.stats.isolated_node_ids != isolated_node_ids:
            raise ValueError("EvidenceGraphStats isolated_node_ids must match graph nodes with no incident edges.")

        visited: set[str] = set()
        connected_component_count = 0
        for node_id in node_ids:
            if node_id in visited:
                continue
            connected_component_count += 1
            stack = [node_id]
            while stack:
                current = stack.pop()
                if current in visited:
                    continue
                visited.add(current)
                stack.extend(sorted(adjacency[current] - visited))

        if self.stats.connected_component_count != connected_component_count:
            raise ValueError("EvidenceGraphStats connected_component_count must match graph topology.")
        if self.stats.weakly_connected != (connected_component_count <= 1):
            raise ValueError("EvidenceGraphStats weakly_connected must match graph topology.")

        graph_classes = {node.node_class for node in self.nodes}
        if set(self.stats.present_node_classes) != graph_classes & set(self.stats.required_node_classes):
            raise ValueError("EvidenceGraphStats present_node_classes must match required classes present in nodes.")
        return self


class ExecutionGateDecision(str, Enum):
    ALLOW = "allow"
    REVIEW = "review"
    BLOCK = "block"


class ExecutionGateCheckStatus(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


class ExecutionGateCheckCategory(str, Enum):
    IDENTITY = "identity"
    EVIDENCE_COVERAGE = "evidence_coverage"
    NUMERIC_CONSISTENCY = "numeric_consistency"
    CITATION_VALIDITY = "citation_validity"
    POLICY_ALIGNMENT = "policy_alignment"
    REASONING_TRACE = "reasoning_trace"
    ARTIFACT_COVERAGE = "artifact_coverage"
    SAFETY_BOUNDARY = "safety_boundary"


class ExecutionGateIssueSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    BLOCKING = "blocking"


class ExecutionGateCheck(V2ContractModel):
    schema_version: str = Field(default=EXECUTION_GATE_SCHEMA_VERSION)
    check_id: str = Field(min_length=3, max_length=80)
    category: ExecutionGateCheckCategory
    status: ExecutionGateCheckStatus
    title: str = Field(min_length=3, max_length=120)
    detail: str = Field(min_length=5, max_length=1000)
    evidence_refs: list[str] = Field(default_factory=list)
    observed_value: str | float | int | bool | None = None
    expected_value: str | float | int | bool | None = None

    @field_validator("check_id")
    @classmethod
    def normalize_check_id(cls, value: str) -> str:
        return normalize_slug_like(value)

    @field_validator("category", "status", mode="before")
    @classmethod
    def normalize_enum_tokens(
        cls,
        value: ExecutionGateCheckCategory | ExecutionGateCheckStatus | str,
    ) -> ExecutionGateCheckCategory | ExecutionGateCheckStatus | str:
        if isinstance(value, (ExecutionGateCheckCategory, ExecutionGateCheckStatus)):
            return value
        return normalize_slug_like(str(value))

    @field_validator("evidence_refs")
    @classmethod
    def normalize_evidence_refs(cls, value: list[str]) -> list[str]:
        return [normalize_slug_like(item) for item in value]


class EvidenceCoverageSummary(V2ContractModel):
    schema_version: str = Field(default=EXECUTION_GATE_SCHEMA_VERSION)
    required_evidence_ids: list[str] = Field(default_factory=list)
    covered_evidence_ids: list[str] = Field(default_factory=list)
    missing_evidence_ids: list[str] = Field(default_factory=list)
    coverage_ratio: float = Field(ge=0.0, le=1.0)

    @field_validator("required_evidence_ids", "covered_evidence_ids", "missing_evidence_ids")
    @classmethod
    def normalize_evidence_ids(cls, value: list[str]) -> list[str]:
        return [normalize_slug_like(item) for item in value]

    @model_validator(mode="after")
    def validate_coverage_sets(self) -> "EvidenceCoverageSummary":
        required = set(self.required_evidence_ids)
        covered = set(self.covered_evidence_ids)
        missing = set(self.missing_evidence_ids)
        if not covered.issubset(required):
            raise ValueError("covered_evidence_ids must be a subset of required_evidence_ids.")
        if not missing.issubset(required):
            raise ValueError("missing_evidence_ids must be a subset of required_evidence_ids.")
        if covered & missing:
            raise ValueError("covered_evidence_ids and missing_evidence_ids must not overlap.")
        if required and required != covered | missing:
            raise ValueError("covered and missing evidence IDs must account for all required evidence IDs.")
        expected_ratio = 1.0 if not required else len(covered) / len(required)
        if abs(self.coverage_ratio - expected_ratio) > 0.001:
            raise ValueError("coverage_ratio must match covered_evidence_ids / required_evidence_ids.")
        return self


class NumericConsistencySummary(V2ContractModel):
    schema_version: str = Field(default=EXECUTION_GATE_SCHEMA_VERSION)
    checked_fields: list[str] = Field(default_factory=list)
    matched_fields: list[str] = Field(default_factory=list)
    mismatched_fields: list[str] = Field(default_factory=list)
    tolerance: float = Field(default=0.001, ge=0.0, le=1.0)
    consistency_ratio: float = Field(ge=0.0, le=1.0)

    @field_validator("checked_fields", "matched_fields", "mismatched_fields")
    @classmethod
    def normalize_field_names(cls, value: list[str]) -> list[str]:
        return [normalize_slug_like(item) for item in value]

    @model_validator(mode="after")
    def validate_numeric_sets(self) -> "NumericConsistencySummary":
        checked = set(self.checked_fields)
        matched = set(self.matched_fields)
        mismatched = set(self.mismatched_fields)
        if not matched.issubset(checked):
            raise ValueError("matched_fields must be a subset of checked_fields.")
        if not mismatched.issubset(checked):
            raise ValueError("mismatched_fields must be a subset of checked_fields.")
        if matched & mismatched:
            raise ValueError("matched_fields and mismatched_fields must not overlap.")
        if checked and checked != matched | mismatched:
            raise ValueError("matched and mismatched fields must account for all checked fields.")
        expected_ratio = 1.0 if not checked else len(matched) / len(checked)
        if abs(self.consistency_ratio - expected_ratio) > 0.001:
            raise ValueError("consistency_ratio must match matched_fields / checked_fields.")
        return self


class CitationValiditySummary(V2ContractModel):
    schema_version: str = Field(default=EXECUTION_GATE_SCHEMA_VERSION)
    allowed_evidence_ids: list[str] = Field(default_factory=list)
    cited_evidence_ids: list[str] = Field(default_factory=list)
    invalid_evidence_ids: list[str] = Field(default_factory=list)
    missing_required_evidence_ids: list[str] = Field(default_factory=list)
    validity_ratio: float = Field(ge=0.0, le=1.0)

    @field_validator(
        "allowed_evidence_ids",
        "cited_evidence_ids",
        "invalid_evidence_ids",
        "missing_required_evidence_ids",
    )
    @classmethod
    def normalize_evidence_ids(cls, value: list[str]) -> list[str]:
        return [normalize_slug_like(item) for item in value]

    @model_validator(mode="after")
    def validate_citation_sets(self) -> "CitationValiditySummary":
        allowed = set(self.allowed_evidence_ids)
        cited = set(self.cited_evidence_ids)
        invalid = set(self.invalid_evidence_ids)
        if invalid != cited - allowed:
            raise ValueError("invalid_evidence_ids must equal cited_evidence_ids outside allowed_evidence_ids.")
        valid_count = len(cited - invalid)
        expected_ratio = 1.0 if not cited else valid_count / len(cited)
        if abs(self.validity_ratio - expected_ratio) > 0.001:
            raise ValueError("validity_ratio must match valid cited evidence over total cited evidence.")
        return self


class PolicyAlignmentSummary(V2ContractModel):
    schema_version: str = Field(default=EXECUTION_GATE_SCHEMA_VERSION)
    policy_version: str = Field(min_length=3, max_length=80)
    triage_matches_decision: bool
    severity_matches_decision: bool
    next_step_matches_decision: bool
    unsafe_claims_detected: bool = False
    notes: list[str] = Field(default_factory=list)

    @field_validator("policy_version")
    @classmethod
    def normalize_policy_version(cls, value: str) -> str:
        return normalize_slug_like(value)


class ExecutionGateIssue(V2ContractModel):
    schema_version: str = Field(default=EXECUTION_GATE_SCHEMA_VERSION)
    issue_id: str = Field(min_length=3, max_length=80)
    category: ExecutionGateCheckCategory
    severity: ExecutionGateIssueSeverity
    title: str = Field(min_length=3, max_length=120)
    detail: str = Field(min_length=5, max_length=1000)
    evidence_refs: list[str] = Field(default_factory=list)

    @field_validator("issue_id")
    @classmethod
    def normalize_issue_id(cls, value: str) -> str:
        return normalize_slug_like(value)

    @field_validator("category", "severity", mode="before")
    @classmethod
    def normalize_enum_tokens(
        cls,
        value: ExecutionGateCheckCategory | ExecutionGateIssueSeverity | str,
    ) -> ExecutionGateCheckCategory | ExecutionGateIssueSeverity | str:
        if isinstance(value, (ExecutionGateCheckCategory, ExecutionGateIssueSeverity)):
            return value
        return normalize_slug_like(str(value))

    @field_validator("evidence_refs")
    @classmethod
    def normalize_evidence_refs(cls, value: list[str]) -> list[str]:
        return [normalize_slug_like(item) for item in value]


class ExecutionGateReport(V2ContractModel):
    schema_version: str = Field(default=EXECUTION_GATE_SCHEMA_VERSION)
    job_id: str = Field(min_length=3, max_length=80)
    sample_id: str = Field(min_length=3, max_length=80)
    decision: TriageOutcome
    severity: SeverityLevel
    gate_decision: ExecutionGateDecision
    summary: str = Field(min_length=10, max_length=1000)
    checks: list[ExecutionGateCheck] = Field(default_factory=list)
    evidence_coverage: EvidenceCoverageSummary
    numeric_consistency: NumericConsistencySummary
    citation_validity: CitationValiditySummary
    policy_alignment: PolicyAlignmentSummary
    issues: list[ExecutionGateIssue] = Field(default_factory=list)
    policy_hash: str
    audit_fingerprint: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("job_id", "sample_id")
    @classmethod
    def normalize_slug_fields(cls, value: str) -> str:
        return normalize_slug_like(value)

    @field_validator("decision", "severity", "gate_decision", mode="before")
    @classmethod
    def normalize_enum_tokens(
        cls,
        value: TriageOutcome | SeverityLevel | ExecutionGateDecision | str,
    ) -> TriageOutcome | SeverityLevel | ExecutionGateDecision | str:
        if isinstance(value, (TriageOutcome, SeverityLevel, ExecutionGateDecision)):
            return value
        return normalize_slug_like(str(value))

    @field_validator("policy_hash", "audit_fingerprint")
    @classmethod
    def validate_sha256_digest(cls, value: str) -> str:
        if not _SHA256_DIGEST_PATTERN.fullmatch(value):
            raise ValueError("Digest fields must use sha256:<64 lowercase hex characters>.")
        return value

    @model_validator(mode="after")
    def validate_gate_decision_consistency(self) -> "ExecutionGateReport":
        if not self.checks:
            raise ValueError("ExecutionGateReport requires at least one check.")
        has_failed_check = any(check.status == ExecutionGateCheckStatus.FAIL for check in self.checks)
        has_warning_check = any(check.status == ExecutionGateCheckStatus.WARN for check in self.checks)
        has_blocking_issue = any(issue.severity == ExecutionGateIssueSeverity.BLOCKING for issue in self.issues)
        has_warning_issue = any(issue.severity == ExecutionGateIssueSeverity.WARNING for issue in self.issues)

        if self.gate_decision == ExecutionGateDecision.ALLOW and (
            has_failed_check or has_warning_check or has_blocking_issue or has_warning_issue
        ):
            raise ValueError("ALLOW gate reports cannot contain warning or failed checks/issues.")
        if self.gate_decision == ExecutionGateDecision.BLOCK and not (has_failed_check or has_blocking_issue):
            raise ValueError("BLOCK gate reports require at least one failed check or blocking issue.")
        return self


class V2AuditStatus(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    PENDING = "pending"


class V2AuditSectionId(str, Enum):
    RUNTIME_MODE = "runtime_mode"
    LIVE_INPUT_PROOF = "live_input_proof"
    PREDICTIVE_BASELINE = "predictive_baseline"
    OPENROUTER_PROOF = "openrouter_proof"
    THESYS_PROOF = "thesys_proof"
    EXECUTION_GATE = "execution_gate"
    REASONING_TRACE = "reasoning_trace"
    EVIDENCE_GRAPH = "evidence_graph"
    ARTIFACT_COVERAGE = "artifact_coverage"


class V2AuditBaselineProvenance(str, Enum):
    FIXTURE_TRAINED_SMOKE = "fixture_trained_smoke"
    PUBLIC_SNAPSHOT = "public_snapshot"
    LIVE_TRAINED = "live_trained"
    UNKNOWN = "unknown"


V2_AUDIT_REQUIRED_SECTIONS: tuple[V2AuditSectionId, ...] = (
    V2AuditSectionId.RUNTIME_MODE,
    V2AuditSectionId.LIVE_INPUT_PROOF,
    V2AuditSectionId.PREDICTIVE_BASELINE,
    V2AuditSectionId.OPENROUTER_PROOF,
    V2AuditSectionId.THESYS_PROOF,
    V2AuditSectionId.EXECUTION_GATE,
    V2AuditSectionId.REASONING_TRACE,
    V2AuditSectionId.EVIDENCE_GRAPH,
    V2AuditSectionId.ARTIFACT_COVERAGE,
)


def _roll_up_v2_audit_status(statuses: list[V2AuditStatus]) -> V2AuditStatus:
    if any(status == V2AuditStatus.FAIL for status in statuses):
        return V2AuditStatus.FAIL
    if any(status == V2AuditStatus.WARN for status in statuses):
        return V2AuditStatus.WARN
    if any(status == V2AuditStatus.PENDING for status in statuses):
        return V2AuditStatus.PENDING
    return V2AuditStatus.PASS


class V2AuditProvenance(V2ContractModel):
    schema_version: str = Field(default=V2_AUDIT_BUNDLE_SCHEMA_VERSION)
    job_id: str = Field(min_length=3, max_length=80)
    sample_id: str = Field(min_length=3, max_length=80)
    target_drug: str = Field(min_length=3, max_length=80)
    input_provenance: ProvenanceSource
    source_context: SourceContext
    split_context: SplitContext
    baseline_provenance: V2AuditBaselineProvenance
    live_input: bool
    fixture_trained_baseline: bool
    provenance_split_label: str = Field(min_length=10, max_length=160)
    detail: str = Field(min_length=20, max_length=1000)

    @field_validator("job_id", "sample_id", "target_drug")
    @classmethod
    def normalize_slug_fields(cls, value: str) -> str:
        return normalize_slug_like(value)

    @field_validator("input_provenance", "source_context", "split_context", "baseline_provenance", mode="before")
    @classmethod
    def normalize_enum_tokens(
        cls,
        value: ProvenanceSource | SourceContext | SplitContext | V2AuditBaselineProvenance | str,
    ) -> ProvenanceSource | SourceContext | SplitContext | V2AuditBaselineProvenance | str:
        if isinstance(value, (ProvenanceSource, SourceContext, SplitContext, V2AuditBaselineProvenance)):
            return value
        return normalize_slug_like(str(value))

    @model_validator(mode="after")
    def validate_provenance_split(self) -> "V2AuditProvenance":
        if self.live_input and self.input_provenance == ProvenanceSource.FIXTURE:
            raise ValueError("live_input cannot be true when input_provenance is fixture.")
        if self.live_input and self.source_context == SourceContext.FIXTURE:
            raise ValueError("live_input cannot be true when source_context is fixture.")
        if (
            self.fixture_trained_baseline
            and self.baseline_provenance != V2AuditBaselineProvenance.FIXTURE_TRAINED_SMOKE
        ):
            raise ValueError("fixture_trained_baseline must use fixture_trained_smoke baseline_provenance.")
        if (
            self.baseline_provenance == V2AuditBaselineProvenance.FIXTURE_TRAINED_SMOKE
            and not self.fixture_trained_baseline
        ):
            raise ValueError("fixture_trained_smoke baseline_provenance requires fixture_trained_baseline=true.")

        label = self.provenance_split_label.lower()
        if self.live_input and self.fixture_trained_baseline and ("live" not in label or "fixture" not in label):
            raise ValueError("provenance_split_label must explicitly name live input and fixture baseline.")
        return self


class V2AuditCheck(V2ContractModel):
    schema_version: str = Field(default=V2_AUDIT_BUNDLE_SCHEMA_VERSION)
    check_id: str = Field(min_length=3, max_length=100)
    section_id: V2AuditSectionId
    status: V2AuditStatus
    title: str = Field(min_length=3, max_length=140)
    detail: str = Field(min_length=5, max_length=1000)
    evidence_refs: list[str] = Field(default_factory=list)
    endpoint: str | None = Field(default=None, min_length=1, max_length=240)
    observed_value: str | float | int | bool | None = None
    expected_value: str | float | int | bool | None = None
    blocking: bool = False

    @field_validator("check_id")
    @classmethod
    def normalize_check_id(cls, value: str) -> str:
        return normalize_slug_like(value)

    @field_validator("section_id", "status", mode="before")
    @classmethod
    def normalize_enum_tokens(
        cls,
        value: V2AuditSectionId | V2AuditStatus | str,
    ) -> V2AuditSectionId | V2AuditStatus | str:
        if isinstance(value, (V2AuditSectionId, V2AuditStatus)):
            return value
        return normalize_slug_like(str(value))

    @field_validator("evidence_refs")
    @classmethod
    def normalize_evidence_refs(cls, value: list[str]) -> list[str]:
        return [normalize_slug_like(item) for item in value]


class V2AuditSection(V2ContractModel):
    schema_version: str = Field(default=V2_AUDIT_BUNDLE_SCHEMA_VERSION)
    section_id: V2AuditSectionId
    status: V2AuditStatus
    title: str = Field(min_length=3, max_length=140)
    summary: str = Field(min_length=10, max_length=1000)
    checks: list[V2AuditCheck] = Field(min_length=1)
    evidence_refs: list[str] = Field(default_factory=list)

    @field_validator("section_id", "status", mode="before")
    @classmethod
    def normalize_enum_tokens(
        cls,
        value: V2AuditSectionId | V2AuditStatus | str,
    ) -> V2AuditSectionId | V2AuditStatus | str:
        if isinstance(value, (V2AuditSectionId, V2AuditStatus)):
            return value
        return normalize_slug_like(str(value))

    @field_validator("evidence_refs")
    @classmethod
    def normalize_evidence_refs(cls, value: list[str]) -> list[str]:
        return [normalize_slug_like(item) for item in value]

    @model_validator(mode="after")
    def validate_section_status(self) -> "V2AuditSection":
        if any(check.section_id != self.section_id for check in self.checks):
            raise ValueError("V2AuditSection checks must use the parent section_id.")
        check_ids = [check.check_id for check in self.checks]
        if len(set(check_ids)) != len(check_ids):
            raise ValueError("V2AuditSection checks must not repeat check_id values.")
        expected_status = _roll_up_v2_audit_status([check.status for check in self.checks])
        if self.status != expected_status:
            raise ValueError("V2AuditSection status must match the rollup of its checks.")
        if any(check.blocking and check.status != V2AuditStatus.FAIL for check in self.checks):
            raise ValueError("blocking audit checks must use fail status.")
        return self


class V2AuditSummary(V2ContractModel):
    schema_version: str = Field(default=V2_AUDIT_BUNDLE_SCHEMA_VERSION)
    overall_status: V2AuditStatus
    section_count: int = Field(ge=1)
    total_checks: int = Field(ge=1)
    passing_checks: int = Field(ge=0)
    warning_checks: int = Field(ge=0)
    failed_checks: int = Field(ge=0)
    pending_checks: int = Field(ge=0)
    live_ready: bool
    provider_proof_required: bool = True

    @field_validator("overall_status", mode="before")
    @classmethod
    def normalize_status(cls, value: V2AuditStatus | str) -> V2AuditStatus | str:
        if isinstance(value, V2AuditStatus):
            return value
        return normalize_slug_like(str(value))


class V2AuditBundle(V2ContractModel):
    schema_version: str = Field(default=V2_AUDIT_BUNDLE_SCHEMA_VERSION)
    job_id: str = Field(min_length=3, max_length=80)
    sample_id: str = Field(min_length=3, max_length=80)
    target_drug: str = Field(min_length=3, max_length=80)
    provenance: V2AuditProvenance
    summary: V2AuditSummary
    sections: list[V2AuditSection] = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("job_id", "sample_id", "target_drug")
    @classmethod
    def normalize_slug_fields(cls, value: str) -> str:
        return normalize_slug_like(value)

    @model_validator(mode="after")
    def validate_audit_bundle(self) -> "V2AuditBundle":
        if (
            self.job_id != self.provenance.job_id
            or self.sample_id != self.provenance.sample_id
            or self.target_drug != self.provenance.target_drug
        ):
            raise ValueError("V2AuditBundle identity must match provenance identity.")

        section_ids = [section.section_id for section in self.sections]
        if len(set(section_ids)) != len(section_ids):
            raise ValueError("V2AuditBundle sections must not repeat section_id values.")

        required_sections = set(V2_AUDIT_REQUIRED_SECTIONS)
        present_sections = set(section_ids)
        if required_sections - present_sections:
            raise ValueError("V2AuditBundle must include every required V2 audit section.")

        all_checks = [check for section in self.sections for check in section.checks]
        check_ids = [f"{check.section_id.value}__{check.check_id}" for check in all_checks]
        if len(set(check_ids)) != len(check_ids):
            raise ValueError("V2AuditBundle checks must not repeat within the same section.")

        status_counts = {
            V2AuditStatus.PASS: sum(1 for check in all_checks if check.status == V2AuditStatus.PASS),
            V2AuditStatus.WARN: sum(1 for check in all_checks if check.status == V2AuditStatus.WARN),
            V2AuditStatus.FAIL: sum(1 for check in all_checks if check.status == V2AuditStatus.FAIL),
            V2AuditStatus.PENDING: sum(1 for check in all_checks if check.status == V2AuditStatus.PENDING),
        }
        if self.summary.section_count != len(self.sections):
            raise ValueError("V2AuditSummary section_count must match sections length.")
        if self.summary.total_checks != len(all_checks):
            raise ValueError("V2AuditSummary total_checks must match bundled checks.")
        if self.summary.passing_checks != status_counts[V2AuditStatus.PASS]:
            raise ValueError("V2AuditSummary passing_checks must match bundled checks.")
        if self.summary.warning_checks != status_counts[V2AuditStatus.WARN]:
            raise ValueError("V2AuditSummary warning_checks must match bundled checks.")
        if self.summary.failed_checks != status_counts[V2AuditStatus.FAIL]:
            raise ValueError("V2AuditSummary failed_checks must match bundled checks.")
        if self.summary.pending_checks != status_counts[V2AuditStatus.PENDING]:
            raise ValueError("V2AuditSummary pending_checks must match bundled checks.")

        expected_overall = _roll_up_v2_audit_status([section.status for section in self.sections])
        if self.summary.overall_status != expected_overall:
            raise ValueError("V2AuditSummary overall_status must match section status rollup.")
        if self.summary.live_ready and self.summary.overall_status in {V2AuditStatus.FAIL, V2AuditStatus.PENDING}:
            raise ValueError("live_ready cannot be true when the V2 audit is failed or pending.")
        return self
