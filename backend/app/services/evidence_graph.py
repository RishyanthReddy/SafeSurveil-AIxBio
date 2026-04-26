from __future__ import annotations

from enum import Enum
import hashlib
import re
from typing import Iterable

import networkx as nx

from app.contracts import (
    ArtifactKind,
    ArtifactManifest,
    CopilotResponse,
    DecisionObject,
    EvidenceGraph,
    EvidenceGraphCluster,
    EvidenceGraphClusterClass,
    EvidenceGraphDetailField,
    EvidenceGraphEdge,
    EvidenceGraphEdgeClass,
    EvidenceGraphNode,
    EvidenceGraphNodeClass,
    EvidenceGraphStats,
    EvidenceGraphStyleHint,
    EvidenceGraphStyleTone,
    ExecutionGateReport,
    EVIDENCE_GRAPH_REQUIRED_NODE_CLASSES,
    MechanismSupportLevel,
    NoveltyBucket,
    ReasoningTrace,
)
from app.contracts.common import normalize_slug_like


_UNSAFE_TOKEN_PATTERN = re.compile(r"[^a-z0-9._-]+")
_DUPLICATE_UNDERSCORE_PATTERN = re.compile(r"_+")


def _enum_value(value: object) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _display_token(value: object | None) -> str:
    if value is None:
        return "Unavailable"
    return _enum_value(value).replace("_", " ").title()


def _percent(value: float | None) -> str:
    if value is None:
        return "Unavailable"
    return f"{round(value * 100)}%"


def _slug_token_part(value: object) -> str:
    token = _UNSAFE_TOKEN_PATTERN.sub("_", _enum_value(value).strip().lower().replace(" ", "_")).strip("_")
    token = _DUPLICATE_UNDERSCORE_PATTERN.sub("_", token).replace("_-", "_").replace("-_", "_")
    return token or "unknown"


def _stable_token(prefix: str, *parts: object, max_length: int = 120) -> str:
    raw = normalize_slug_like("__".join([_slug_token_part(prefix), *(_slug_token_part(part) for part in parts if part is not None)]))
    if len(raw) <= max_length:
        return raw
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"{raw[: max_length - 14].rstrip('_')}__{digest}"


def _style(
    tone: EvidenceGraphStyleTone,
    *,
    importance: int = 3,
    icon: str | None = None,
) -> EvidenceGraphStyleHint:
    return EvidenceGraphStyleHint(
        tone=tone,
        color_token=tone.value,
        icon=icon or tone.value,
        importance=importance,
    )


def _detail(
    key: str,
    label: str,
    value: str | float | int | bool | None,
    *,
    value_kind: str = "text",
) -> EvidenceGraphDetailField:
    return EvidenceGraphDetailField(key=key, label=label, value=value, value_kind=value_kind)


def _node(
    node_id: str,
    node_class: EvidenceGraphNodeClass,
    label: str,
    *,
    summary: str,
    details: list[EvidenceGraphDetailField] | None = None,
    evidence_refs: list[str] | None = None,
    artifact_refs: list[str] | None = None,
    tone: EvidenceGraphStyleTone = EvidenceGraphStyleTone.NEUTRAL,
    importance: int = 3,
    icon: str | None = None,
) -> EvidenceGraphNode:
    return EvidenceGraphNode(
        node_id=node_id,
        node_class=node_class,
        label=label,
        summary=summary,
        details=details or [],
        evidence_refs=evidence_refs or [],
        artifact_refs=artifact_refs or [],
        style=_style(tone, importance=importance, icon=icon),
    )


def _edge(
    edge_id: str,
    edge_class: EvidenceGraphEdgeClass,
    source: str,
    target: str,
    label: str,
    *,
    summary: str | None = None,
    evidence_refs: list[str] | None = None,
    tone: EvidenceGraphStyleTone = EvidenceGraphStyleTone.NEUTRAL,
    weight: float = 1.0,
) -> EvidenceGraphEdge:
    return EvidenceGraphEdge(
        edge_id=edge_id,
        edge_class=edge_class,
        source=source,
        target=target,
        label=label,
        summary=summary,
        evidence_refs=evidence_refs or [],
        weight=weight,
        style=_style(tone, importance=2),
    )


def _merge_duplicate_nodes(nodes: list[EvidenceGraphNode]) -> list[EvidenceGraphNode]:
    merged: list[EvidenceGraphNode] = []
    by_id: dict[str, EvidenceGraphNode] = {}
    for node in nodes:
        existing = by_id.get(node.node_id)
        if existing is None:
            by_id[node.node_id] = node
            merged.append(node)
            continue
        if existing.node_class != node.node_class:
            raise ValueError("Evidence graph builder emitted conflicting node classes for one node ID.")
        existing.evidence_refs = _unique([*existing.evidence_refs, *node.evidence_refs])
        existing.artifact_refs = _unique([*existing.artifact_refs, *node.artifact_refs])
        known_detail_keys = {detail.key for detail in existing.details}
        existing.details = [
            *existing.details,
            *(detail for detail in node.details if detail.key not in known_detail_keys),
        ]
    return merged


def _merge_duplicate_edges(edges: list[EvidenceGraphEdge]) -> list[EvidenceGraphEdge]:
    merged: list[EvidenceGraphEdge] = []
    by_id: dict[str, EvidenceGraphEdge] = {}
    for edge in edges:
        existing = by_id.get(edge.edge_id)
        if existing is None:
            by_id[edge.edge_id] = edge
            merged.append(edge)
            continue
        if (
            existing.edge_class != edge.edge_class
            or existing.source != edge.source
            or existing.target != edge.target
        ):
            raise ValueError("Evidence graph builder emitted conflicting edges for one edge ID.")
        existing.evidence_refs = _unique([*existing.evidence_refs, *edge.evidence_refs])
        existing.weight = max(existing.weight, edge.weight)
    return merged


def _unique(items: Iterable[str]) -> list[str]:
    return sorted({item for item in items if item})


def _mechanism_label(evidence_index: int, decision: DecisionObject) -> str:
    evidence = decision.mechanistic_evidence[evidence_index]
    return evidence.gene_symbol or evidence.mutation or f"mechanism {evidence_index + 1}"


def _mechanism_evidence_id(index: int) -> str:
    return f"mechanistic_evidence__{index}"


def _supported_mechanism_indices(decision: DecisionObject) -> list[int]:
    supported_levels = {MechanismSupportLevel.SUPPORTED.value, MechanismSupportLevel.PARTIAL.value}
    return [
        index
        for index, evidence in enumerate(decision.mechanistic_evidence)
        if _enum_value(evidence.support_level) in supported_levels
    ]


def _mechanism_targets_drug(target_drug: str, drug_association: Iterable[str]) -> bool:
    normalized_target = normalize_slug_like(target_drug)
    return any(normalize_slug_like(drug) == normalized_target for drug in drug_association)


def _mechanism_has_actionable_support(support_level: MechanismSupportLevel | str) -> bool:
    return _enum_value(support_level) in {
        MechanismSupportLevel.SUPPORTED.value,
        MechanismSupportLevel.PARTIAL.value,
    }


def _copilot_citation_ids(copilot: CopilotResponse | None) -> list[str]:
    if copilot is None:
        return []
    evidence_ids = list(copilot.cited_evidence_ids)
    for block in copilot.answer_blocks:
        evidence_ids.extend(block.cited_evidence_ids)
    if copilot.semantic_ui is not None:
        if copilot.semantic_ui.evidence_table is not None:
            evidence_ids.extend(
                row.evidence_id
                for row in copilot.semantic_ui.evidence_table.rows
                if row.evidence_id is not None
            )
        for chart in copilot.semantic_ui.risk_charts:
            evidence_ids.extend(
                point.evidence_id
                for point in chart.points
                if point.evidence_id is not None
            )
    return _unique(evidence_ids)


def _artifact_node_id(artifact_id: str) -> str:
    return _stable_token("artifact", artifact_id)


def _build_nodes(
    decision: DecisionObject,
    *,
    artifact_manifest: ArtifactManifest | None,
    copilot: CopilotResponse | None,
    execution_gate: ExecutionGateReport | None,
    reasoning_trace: ReasoningTrace | None,
) -> list[EvidenceGraphNode]:
    sample = decision.sample
    prediction = decision.phenotype_prediction
    novelty = decision.novelty_assessment
    qc = decision.assembly_qc
    features = decision.actionability_features
    triage = decision.triage_decision
    job_id = decision.job_id or triage.job_id

    sample_node_id = _stable_token("sample", sample.sample_id)
    organism_node_id = _stable_token("organism", sample.organism_hint or "unknown")
    drug_node_id = _stable_token("drug", sample.target_drug)
    phenotype_node_id = _stable_token("phenotype", prediction.predicted_phenotype)
    novelty_node_id = _stable_token("novelty", novelty.novelty_bucket)
    qc_node_id = _stable_token("qc", qc.qc_status)
    actionability_node_id = "actionability__score"
    decision_node_id = _stable_token("decision", triage.triage)
    policy_node_id = _stable_token("policy", features.threshold_version)

    nodes = [
        _node(
            sample_node_id,
            EvidenceGraphNodeClass.SAMPLE,
            sample.sample_id,
            summary="Persisted sample input and provenance context.",
            details=[
                _detail("job_id", "Job ID", job_id),
                _detail("source_context", "Source context", _enum_value(sample.metadata.source_context)),
                _detail("accession", "Accession", sample.metadata.accession or "Unavailable"),
            ],
            evidence_refs=["decision_object__summary"],
            tone=EvidenceGraphStyleTone.SAMPLE,
            importance=5,
            icon="sample",
        ),
        _node(
            organism_node_id,
            EvidenceGraphNodeClass.ORGANISM,
            _display_token(sample.organism_hint),
            summary="Organism context from the persisted sample metadata.",
            evidence_refs=["decision_object__summary"],
            tone=EvidenceGraphStyleTone.NEUTRAL,
        ),
        _node(
            drug_node_id,
            EvidenceGraphNodeClass.DRUG,
            _display_token(sample.target_drug),
            summary="Target drug for the AMR triage case.",
            evidence_refs=["decision_object__summary"],
            tone=EvidenceGraphStyleTone.POLICY,
            importance=4,
        ),
        _node(
            phenotype_node_id,
            EvidenceGraphNodeClass.PHENOTYPE_PREDICTION,
            f"{_display_token(prediction.predicted_phenotype)} phenotype",
            summary="Persisted baseline phenotype prediction.",
            details=[
                _detail("probability", "Probability", prediction.probability, value_kind="ratio"),
                _detail("calibration_status", "Calibration status", _enum_value(prediction.calibration_status)),
                _detail("model_version", "Model version", prediction.model_version),
            ],
            evidence_refs=["phenotype_prediction__summary"],
            tone=EvidenceGraphStyleTone.RISK,
            importance=4,
        ),
        _node(
            novelty_node_id,
            EvidenceGraphNodeClass.NOVELTY,
            f"{_display_token(novelty.novelty_bucket)} novelty",
            summary="Lineage novelty and reference-distance context.",
            details=[
                _detail("novelty_score", "Novelty score", novelty.novelty_score, value_kind="ratio"),
                _detail("nearest_neighbor", "Nearest neighbor", novelty.nearest_neighbor_id or "Unavailable"),
                _detail("reference_snapshot", "Reference snapshot", novelty.reference_snapshot_id),
            ],
            evidence_refs=["novelty_assessment__summary"],
            tone=(
                EvidenceGraphStyleTone.CAVEAT
                if _enum_value(novelty.novelty_bucket) in {NoveltyBucket.HIGH.value, NoveltyBucket.UNKNOWN.value}
                else EvidenceGraphStyleTone.RISK
            ),
            importance=4,
        ),
        _node(
            qc_node_id,
            EvidenceGraphNodeClass.QUALITY_CONTROL,
            f"QC {_display_token(qc.qc_status)}",
            summary="Assembly QC and metadata completeness constraints.",
            details=[
                _detail("qc_status", "QC status", _enum_value(qc.qc_status)),
                _detail("qc_risk", "QC risk", features.qc_risk, value_kind="ratio"),
                _detail("metadata_completeness", "Metadata completeness", features.metadata_completeness, value_kind="ratio"),
            ],
            evidence_refs=["decision_object__assembly_qc"],
            tone=EvidenceGraphStyleTone.CAVEAT if features.qc_risk > 0 else EvidenceGraphStyleTone.RISK,
            importance=4,
        ),
        _node(
            actionability_node_id,
            EvidenceGraphNodeClass.ACTIONABILITY,
            "Actionability score",
            summary="Persisted actionability policy score and mechanism concordance.",
            details=[
                _detail("actionability_score", "Actionability score", features.actionability_score, value_kind="ratio"),
                _detail("mechanism_concordance", "Mechanism concordance", bool(features.mechanism_concordance)),
                _detail("threshold_version", "Threshold version", features.threshold_version),
            ],
            evidence_refs=["actionability_features__summary"],
            tone=EvidenceGraphStyleTone.POLICY,
            importance=5,
        ),
        _node(
            decision_node_id,
            EvidenceGraphNodeClass.DECISION,
            _display_token(triage.triage).upper(),
            summary=triage.recommended_next_step,
            details=[
                _detail("triage", "Triage", _enum_value(triage.triage)),
                _detail("severity", "Severity", _enum_value(triage.severity)),
            ],
            evidence_refs=["decision_object__triage"],
            tone=EvidenceGraphStyleTone.DECISION,
            importance=5,
        ),
        _node(
            policy_node_id,
            EvidenceGraphNodeClass.POLICY,
            features.threshold_version,
            summary="Actionability policy version used by the persisted decision.",
            evidence_refs=["actionability_features__summary", "decision_object__triage"],
            tone=EvidenceGraphStyleTone.POLICY,
            importance=3,
        ),
    ]

    if decision.mechanistic_evidence:
        for index, evidence in enumerate(decision.mechanistic_evidence, start=1):
            signal = evidence.gene_symbol or evidence.mutation or f"mechanism_{index}"
            evidence_id = _mechanism_evidence_id(index)
            nodes.append(
                _node(
                    _stable_token("gene", signal),
                    EvidenceGraphNodeClass.GENE,
                    signal,
                    summary=evidence.interpretation,
                    details=[
                        _detail("support_level", "Support level", _enum_value(evidence.support_level)),
                        _detail("source_tool", "Source tool", evidence.source_tool),
                    ],
                    evidence_refs=[evidence_id],
                    artifact_refs=[evidence.raw_artifact_id] if evidence.raw_artifact_id else [],
                    tone=EvidenceGraphStyleTone.EVIDENCE,
                    importance=4,
                )
            )
            nodes.append(
                _node(
                    _stable_token("mechanism", evidence.mechanism_class),
                    EvidenceGraphNodeClass.MECHANISM,
                    _display_token(evidence.mechanism_class),
                    summary="Mechanism class reported by the mechanistic evidence layer.",
                    evidence_refs=[evidence_id],
                    tone=EvidenceGraphStyleTone.EVIDENCE,
                    importance=4,
                )
            )
        if not any(
            _mechanism_targets_drug(sample.target_drug, evidence.drug_association)
            for evidence in decision.mechanistic_evidence
        ):
            nodes.append(
                _node(
                    "warning__target_mechanism_not_linked",
                    EvidenceGraphNodeClass.WARNING,
                    "No target-drug mechanism link",
                    summary="Mechanistic rows exist, but none directly name the target drug association.",
                    evidence_refs=[_mechanism_evidence_id(index) for index in range(1, len(decision.mechanistic_evidence) + 1)],
                    tone=EvidenceGraphStyleTone.CAVEAT,
                    importance=3,
                )
            )
    else:
        nodes.append(
            _node(
                "warning__mechanistic_evidence_missing",
                EvidenceGraphNodeClass.WARNING,
                "No mechanistic evidence",
                summary="No persisted mechanism rows are available; the graph preserves this as an explicit gap.",
                evidence_refs=["mechanistic_evidence__none"],
                tone=EvidenceGraphStyleTone.CAVEAT,
                importance=4,
            )
        )

    if artifact_manifest is not None and artifact_manifest.artifacts:
        for artifact in artifact_manifest.artifacts:
            nodes.append(
                _node(
                    _artifact_node_id(artifact.artifact_id),
                    EvidenceGraphNodeClass.ARTIFACT,
                    artifact.artifact_id,
                    summary="Persisted evidence artifact available through the backend manifest.",
                    details=[
                        _detail("kind", "Kind", _enum_value(artifact.kind)),
                        _detail("media_type", "Media type", artifact.media_type),
                        _detail("generated_by", "Generated by", artifact.generated_by),
                        _detail("preview_eligible", "Preview eligible", artifact.preview_eligible, value_kind="boolean"),
                        _detail("size_bytes", "Size bytes", artifact.size_bytes, value_kind="integer"),
                    ],
                    artifact_refs=[artifact.artifact_id],
                    tone=EvidenceGraphStyleTone.EVIDENCE,
                    importance=2 if artifact.kind != ArtifactKind.MECHANISTIC_EVIDENCE else 3,
                )
            )
    else:
        nodes.append(
            _node(
                "warning__artifact_manifest_missing",
                EvidenceGraphNodeClass.WARNING,
                "Artifact manifest missing",
                summary="No artifact manifest was supplied to the graph builder.",
                evidence_refs=["decision_object__warnings"],
                tone=EvidenceGraphStyleTone.CAVEAT,
                importance=3,
            )
        )

    for rationale in triage.rationale_codes:
        nodes.append(
            _node(
                _stable_token("rationale", rationale),
                EvidenceGraphNodeClass.RATIONALE,
                _display_token(rationale),
                summary="Persisted rationale code attached to the final triage decision.",
                evidence_refs=["decision_object__triage"],
                tone=EvidenceGraphStyleTone.POLICY,
                importance=3,
            )
        )

    if copilot is not None:
        nodes.append(
            _node(
                "copilot__grounded_response",
                EvidenceGraphNodeClass.COPILOT,
                "Grounded copilot",
                summary="Cached or live grounded copilot sidecar linked by citations.",
                details=[
                    _detail("refusal_required", "Refusal required", copilot.refusal_required, value_kind="boolean"),
                    _detail("answer_blocks", "Answer blocks", len(copilot.answer_blocks), value_kind="integer"),
                ],
                evidence_refs=copilot.cited_evidence_ids,
                tone=EvidenceGraphStyleTone.AI,
                importance=3,
            )
        )
        citation_ids = _copilot_citation_ids(copilot)
        if citation_ids:
            for evidence_id in citation_ids:
                nodes.append(
                    _node(
                        _stable_token("citation", evidence_id),
                        EvidenceGraphNodeClass.CITATION,
                        evidence_id,
                        summary="Evidence ID cited by the grounded copilot sidecar.",
                        evidence_refs=[evidence_id],
                        tone=EvidenceGraphStyleTone.AI,
                        importance=2,
                    )
                )
        else:
            nodes.append(
                _node(
                    "warning__copilot_citations_missing",
                    EvidenceGraphNodeClass.WARNING,
                    "Copilot citations missing",
                    summary="Copilot output was supplied but did not expose citation IDs.",
                    tone=EvidenceGraphStyleTone.CAVEAT,
                    importance=3,
                )
            )
    else:
        nodes.append(
            _node(
                "warning__copilot_sidecar_missing",
                EvidenceGraphNodeClass.WARNING,
                "Copilot sidecar missing",
                summary="No copilot response was supplied; graph generation remains deterministic from persisted evidence.",
                evidence_refs=["decision_object__warnings"],
                tone=EvidenceGraphStyleTone.CAVEAT,
                importance=2,
            )
        )

    if execution_gate is not None:
        nodes.append(
            _node(
                "execution_gate__report",
                EvidenceGraphNodeClass.EXECUTION_GATE,
                f"Gate {execution_gate.gate_decision.value.upper()}",
                summary=execution_gate.summary,
                details=[
                    _detail("gate_decision", "Gate decision", execution_gate.gate_decision.value),
                    _detail("policy_hash", "Policy hash", execution_gate.policy_hash),
                    _detail("checks", "Checks", len(execution_gate.checks), value_kind="integer"),
                ],
                evidence_refs=["decision_object__summary"],
                tone=EvidenceGraphStyleTone.GATE,
                importance=4,
            )
        )

    if reasoning_trace is not None:
        nodes.append(
            _node(
                "reasoning_trace__summary",
                EvidenceGraphNodeClass.REASONING_TRACE,
                "Reasoning trace",
                summary=reasoning_trace.summary,
                details=[
                    _detail("coverage_ratio", "Coverage ratio", reasoning_trace.coverage.coverage_ratio, value_kind="ratio"),
                    _detail("steps", "Steps", len(reasoning_trace.steps), value_kind="integer"),
                ],
                evidence_refs=["decision_object__summary"],
                tone=EvidenceGraphStyleTone.AI,
                importance=3,
            )
        )

    return nodes


def _build_edges(
    decision: DecisionObject,
    nodes: list[EvidenceGraphNode],
    *,
    artifact_manifest: ArtifactManifest | None,
    copilot: CopilotResponse | None,
    execution_gate: ExecutionGateReport | None,
    reasoning_trace: ReasoningTrace | None,
) -> list[EvidenceGraphEdge]:
    sample = decision.sample
    prediction = decision.phenotype_prediction
    novelty = decision.novelty_assessment
    qc = decision.assembly_qc
    triage = decision.triage_decision
    node_ids = {node.node_id for node in nodes}

    sample_node_id = _stable_token("sample", sample.sample_id)
    organism_node_id = _stable_token("organism", sample.organism_hint or "unknown")
    drug_node_id = _stable_token("drug", sample.target_drug)
    phenotype_node_id = _stable_token("phenotype", prediction.predicted_phenotype)
    novelty_node_id = _stable_token("novelty", novelty.novelty_bucket)
    qc_node_id = _stable_token("qc", qc.qc_status)
    actionability_node_id = "actionability__score"
    decision_node_id = _stable_token("decision", triage.triage)
    policy_node_id = _stable_token("policy", decision.actionability_features.threshold_version)

    edges = [
        _edge(
            "edge__sample_to_organism",
            EvidenceGraphEdgeClass.HAS_CONTEXT,
            sample_node_id,
            organism_node_id,
            "has organism context",
            evidence_refs=["decision_object__summary"],
            tone=EvidenceGraphStyleTone.SAMPLE,
        ),
        _edge(
            "edge__sample_to_drug",
            EvidenceGraphEdgeClass.TARGETS,
            sample_node_id,
            drug_node_id,
            "targets drug",
            evidence_refs=["decision_object__summary"],
            tone=EvidenceGraphStyleTone.POLICY,
        ),
        _edge(
            "edge__phenotype_to_actionability",
            EvidenceGraphEdgeClass.INFORMS,
            phenotype_node_id,
            actionability_node_id,
            "informs actionability",
            evidence_refs=["phenotype_prediction__summary"],
            tone=EvidenceGraphStyleTone.RISK,
        ),
        _edge(
            "edge__novelty_to_actionability",
            EvidenceGraphEdgeClass.CONSTRAINS,
            novelty_node_id,
            actionability_node_id,
            "constrains actionability",
            evidence_refs=["novelty_assessment__summary"],
            tone=EvidenceGraphStyleTone.CAVEAT,
            weight=0.85,
        ),
        _edge(
            "edge__qc_to_actionability",
            EvidenceGraphEdgeClass.CONSTRAINS,
            qc_node_id,
            actionability_node_id,
            "constrains actionability",
            evidence_refs=["decision_object__assembly_qc"],
            tone=EvidenceGraphStyleTone.CAVEAT,
            weight=0.85,
        ),
        _edge(
            "edge__policy_to_actionability",
            EvidenceGraphEdgeClass.CONSTRAINS,
            policy_node_id,
            actionability_node_id,
            "applies policy",
            evidence_refs=["actionability_features__summary"],
            tone=EvidenceGraphStyleTone.POLICY,
        ),
        _edge(
            "edge__actionability_to_decision",
            EvidenceGraphEdgeClass.TRIAGES_AS,
            actionability_node_id,
            decision_node_id,
            "triages as",
            evidence_refs=["actionability_features__summary", "decision_object__triage"],
            tone=EvidenceGraphStyleTone.DECISION,
        ),
    ]

    if decision.mechanistic_evidence:
        raw_artifact_ids = {
            artifact.artifact_id
            for artifact in (artifact_manifest.artifacts if artifact_manifest is not None else [])
        }
        for index, evidence in enumerate(decision.mechanistic_evidence, start=1):
            signal = evidence.gene_symbol or evidence.mutation or f"mechanism_{index}"
            gene_node_id = _stable_token("gene", signal)
            mechanism_node_id = _stable_token("mechanism", evidence.mechanism_class)
            evidence_id = _mechanism_evidence_id(index)
            targets_drug = _mechanism_targets_drug(sample.target_drug, evidence.drug_association)
            has_actionable_support = _mechanism_has_actionable_support(evidence.support_level)
            edges.extend(
                [
                    _edge(
                        _stable_token("edge", "sample_detects", gene_node_id, max_length=140),
                        EvidenceGraphEdgeClass.DETECTS,
                        sample_node_id,
                        gene_node_id,
                        "detects",
                        evidence_refs=[evidence_id],
                        tone=EvidenceGraphStyleTone.EVIDENCE,
                    ),
                    _edge(
                        _stable_token("edge", gene_node_id, "mechanism", mechanism_node_id, max_length=140),
                        EvidenceGraphEdgeClass.LINKED_TO,
                        gene_node_id,
                        mechanism_node_id,
                        "maps to mechanism",
                        evidence_refs=[evidence_id],
                        tone=EvidenceGraphStyleTone.EVIDENCE,
                    ),
                ]
            )
            if targets_drug:
                edges.append(
                    _edge(
                        _stable_token("edge", mechanism_node_id, drug_node_id, max_length=140),
                        EvidenceGraphEdgeClass.ASSOCIATED_WITH,
                        mechanism_node_id,
                        drug_node_id,
                        "associated with target drug",
                        evidence_refs=[evidence_id],
                        tone=EvidenceGraphStyleTone.EVIDENCE,
                    )
                )
            if targets_drug and has_actionable_support:
                edges.append(
                    _edge(
                        _stable_token("edge", mechanism_node_id, "actionability", max_length=140),
                        EvidenceGraphEdgeClass.SUPPORTS,
                        mechanism_node_id,
                        actionability_node_id,
                        "supports actionability",
                        evidence_refs=[evidence_id, "actionability_features__summary"],
                        tone=EvidenceGraphStyleTone.EVIDENCE,
                    )
                )
            else:
                edges.append(
                    _edge(
                        _stable_token("edge", mechanism_node_id, "actionability_caveat", max_length=140),
                        EvidenceGraphEdgeClass.CAVEATS,
                        mechanism_node_id,
                        actionability_node_id,
                        "does not support target actionability",
                        evidence_refs=[evidence_id, "actionability_features__summary"],
                        tone=EvidenceGraphStyleTone.CAVEAT,
                        weight=0.55,
                    )
                )
                warning_node_id = "warning__target_mechanism_not_linked"
                if warning_node_id in node_ids:
                    edges.append(
                        _edge(
                            _stable_token("edge", warning_node_id, mechanism_node_id, max_length=140),
                            EvidenceGraphEdgeClass.CAVEATS,
                            warning_node_id,
                            mechanism_node_id,
                            "caveats mechanism",
                            evidence_refs=[evidence_id],
                            tone=EvidenceGraphStyleTone.CAVEAT,
                        )
                    )
            if evidence.raw_artifact_id and evidence.raw_artifact_id in raw_artifact_ids:
                edges.append(
                    _edge(
                        _stable_token("edge", evidence.raw_artifact_id, gene_node_id, max_length=140),
                        EvidenceGraphEdgeClass.GENERATED_ARTIFACT,
                        _artifact_node_id(evidence.raw_artifact_id),
                        gene_node_id,
                        "generated evidence",
                        evidence_refs=[evidence_id],
                        tone=EvidenceGraphStyleTone.EVIDENCE,
                    )
                )
    else:
        edges.append(
            _edge(
                "edge__missing_mechanism_to_actionability",
                EvidenceGraphEdgeClass.CAVEATS,
                "warning__mechanistic_evidence_missing",
                actionability_node_id,
                "caveats actionability",
                evidence_refs=["mechanistic_evidence__none"],
                tone=EvidenceGraphStyleTone.CAVEAT,
            )
        )

    if artifact_manifest is not None and artifact_manifest.artifacts:
        for artifact in artifact_manifest.artifacts:
            artifact_node_id = _artifact_node_id(artifact.artifact_id)
            if artifact_node_id not in node_ids:
                continue
            target = sample_node_id
            if artifact.kind == ArtifactKind.DECISION_OBJECT:
                target = decision_node_id
            elif artifact.kind == ArtifactKind.NOVELTY_SUMMARY:
                target = novelty_node_id
            elif artifact.kind == ArtifactKind.PREDICTION_SUMMARY:
                target = phenotype_node_id
            edges.append(
                _edge(
                    _stable_token("edge", artifact_node_id, target, max_length=140),
                    EvidenceGraphEdgeClass.GENERATED_ARTIFACT,
                    artifact_node_id,
                    target,
                    "supports persisted case",
                    evidence_refs=[artifact.artifact_id],
                    tone=EvidenceGraphStyleTone.EVIDENCE,
                    weight=0.7,
                )
            )
    else:
        edges.append(
            _edge(
                "edge__missing_artifact_to_sample",
                EvidenceGraphEdgeClass.CAVEATS,
                "warning__artifact_manifest_missing",
                sample_node_id,
                "caveats artifact coverage",
                evidence_refs=["decision_object__warnings"],
                tone=EvidenceGraphStyleTone.CAVEAT,
            )
        )

    for rationale in triage.rationale_codes:
        rationale_node_id = _stable_token("rationale", rationale)
        edges.append(
            _edge(
                _stable_token("edge", rationale_node_id, decision_node_id, max_length=140),
                EvidenceGraphEdgeClass.SUPPORTS,
                rationale_node_id,
                decision_node_id,
                "supports decision",
                evidence_refs=["decision_object__triage"],
                tone=EvidenceGraphStyleTone.POLICY,
            )
        )

    if copilot is not None:
        copilot_node_id = "copilot__grounded_response"
        edges.append(
            _edge(
                "edge__copilot_to_decision",
                EvidenceGraphEdgeClass.EXPLAINS,
                copilot_node_id,
                decision_node_id,
                "explains decision",
                evidence_refs=copilot.cited_evidence_ids,
                tone=EvidenceGraphStyleTone.AI,
                weight=0.65,
            )
        )
        for citation_id in _copilot_citation_ids(copilot):
            citation_node_id = _stable_token("citation", citation_id)
            edges.append(
                _edge(
                    _stable_token("edge", copilot_node_id, citation_node_id, max_length=140),
                    EvidenceGraphEdgeClass.CITES,
                    copilot_node_id,
                    citation_node_id,
                    "cites",
                    evidence_refs=[citation_id],
                    tone=EvidenceGraphStyleTone.AI,
                    weight=0.65,
                )
            )
            matching_nodes = [
                node.node_id
                for node in nodes
                if node.node_class != EvidenceGraphNodeClass.CITATION and citation_id in node.evidence_refs
            ]
            for target_node_id in matching_nodes[:4] or [decision_node_id]:
                edges.append(
                    _edge(
                        _stable_token("edge", citation_node_id, target_node_id, max_length=140),
                        EvidenceGraphEdgeClass.LINKED_TO,
                        citation_node_id,
                        target_node_id,
                        "links citation",
                        evidence_refs=[citation_id],
                        tone=EvidenceGraphStyleTone.AI,
                        weight=0.55,
                    )
                )
    else:
        edges.append(
            _edge(
                "edge__missing_copilot_to_decision",
                EvidenceGraphEdgeClass.CAVEATS,
                "warning__copilot_sidecar_missing",
                decision_node_id,
                "caveats generated-language coverage",
                evidence_refs=["decision_object__warnings"],
                tone=EvidenceGraphStyleTone.CAVEAT,
            )
        )

    if execution_gate is not None:
        edges.append(
            _edge(
                "edge__gate_to_decision",
                EvidenceGraphEdgeClass.VERIFIED_BY,
                "execution_gate__report",
                decision_node_id,
                "verifies presentation",
                evidence_refs=["decision_object__summary"],
                tone=EvidenceGraphStyleTone.GATE,
            )
        )

    if reasoning_trace is not None:
        edges.append(
            _edge(
                "edge__trace_to_decision",
                EvidenceGraphEdgeClass.EXPLAINS,
                "reasoning_trace__summary",
                decision_node_id,
                "explains deterministic reasoning",
                evidence_refs=["decision_object__summary"],
                tone=EvidenceGraphStyleTone.AI,
            )
        )

    return edges


def _build_clusters(nodes: list[EvidenceGraphNode]) -> list[EvidenceGraphCluster]:
    node_ids_by_class: dict[EvidenceGraphNodeClass, list[str]] = {}
    for node in nodes:
        node_ids_by_class.setdefault(node.node_class, []).append(node.node_id)

    def node_ids_for(*classes: EvidenceGraphNodeClass) -> list[str]:
        return sorted(node_id for node_class in classes for node_id in node_ids_by_class.get(node_class, []))

    cluster_specs = [
        (
            "cluster__case_context",
            EvidenceGraphClusterClass.CASE_CONTEXT,
            "Case context",
            "Sample, organism, and target-drug nodes.",
            node_ids_for(EvidenceGraphNodeClass.SAMPLE, EvidenceGraphNodeClass.ORGANISM, EvidenceGraphNodeClass.DRUG),
            EvidenceGraphStyleTone.SAMPLE,
        ),
        (
            "cluster__mechanistic_evidence",
            EvidenceGraphClusterClass.MECHANISTIC_EVIDENCE,
            "Mechanistic evidence",
            "Genes, mechanisms, artifacts, and mechanism warnings.",
            node_ids_for(EvidenceGraphNodeClass.GENE, EvidenceGraphNodeClass.MECHANISM, EvidenceGraphNodeClass.ARTIFACT, EvidenceGraphNodeClass.WARNING),
            EvidenceGraphStyleTone.EVIDENCE,
        ),
        (
            "cluster__risk_signals",
            EvidenceGraphClusterClass.RISK_SIGNALS,
            "Risk signals",
            "Phenotype prediction, novelty, QC, and actionability nodes.",
            node_ids_for(
                EvidenceGraphNodeClass.PHENOTYPE_PREDICTION,
                EvidenceGraphNodeClass.NOVELTY,
                EvidenceGraphNodeClass.QUALITY_CONTROL,
                EvidenceGraphNodeClass.ACTIONABILITY,
            ),
            EvidenceGraphStyleTone.RISK,
        ),
        (
            "cluster__policy_triage",
            EvidenceGraphClusterClass.POLICY_AND_TRIAGE,
            "Policy and triage",
            "Policy, rationale, and persisted final decision.",
            node_ids_for(EvidenceGraphNodeClass.POLICY, EvidenceGraphNodeClass.RATIONALE, EvidenceGraphNodeClass.DECISION),
            EvidenceGraphStyleTone.POLICY,
        ),
        (
            "cluster__ai_sidecars",
            EvidenceGraphClusterClass.AI_SIDECARS,
            "AI sidecars",
            "Copilot, citations, and deterministic reasoning trace nodes.",
            node_ids_for(EvidenceGraphNodeClass.COPILOT, EvidenceGraphNodeClass.CITATION, EvidenceGraphNodeClass.REASONING_TRACE),
            EvidenceGraphStyleTone.AI,
        ),
        (
            "cluster__audit",
            EvidenceGraphClusterClass.AUDIT,
            "Audit",
            "Execution gate and graph-level audit support.",
            node_ids_for(EvidenceGraphNodeClass.EXECUTION_GATE),
            EvidenceGraphStyleTone.GATE,
        ),
    ]

    return [
        EvidenceGraphCluster(
            cluster_id=cluster_id,
            cluster_class=cluster_class,
            label=label,
            summary=summary,
            node_ids=node_ids,
            style=_style(tone, importance=3),
        )
        for cluster_id, cluster_class, label, summary, node_ids, tone in cluster_specs
        if node_ids
    ]


def _build_networkx_graph(nodes: list[EvidenceGraphNode], edges: list[EvidenceGraphEdge]) -> nx.DiGraph:
    graph = nx.DiGraph()
    for node in nodes:
        graph.add_node(node.node_id, node_class=node.node_class.value)

    node_ids = set(graph.nodes)
    for edge in edges:
        if edge.source not in node_ids or edge.target not in node_ids:
            raise ValueError("Evidence graph builder emitted an edge with a dangling node reference.")
        graph.add_edge(edge.source, edge.target, edge_class=edge.edge_class.value)
    return graph


def _safe_linkage_ratio(total: int, linked: int) -> float:
    return 1.0 if total == 0 else linked / total


def _build_stats(
    nodes: list[EvidenceGraphNode],
    edges: list[EvidenceGraphEdge],
    clusters: list[EvidenceGraphCluster],
    networkx_graph: nx.DiGraph,
) -> EvidenceGraphStats:
    node_classes = {node.node_class for node in nodes}
    required_classes = list(EVIDENCE_GRAPH_REQUIRED_NODE_CLASSES)
    present_classes = [node_class for node_class in required_classes if node_class in node_classes]
    missing_classes = [node_class for node_class in required_classes if node_class not in node_classes]
    evidence_node_classes = {
        EvidenceGraphNodeClass.GENE,
        EvidenceGraphNodeClass.MECHANISM,
        EvidenceGraphNodeClass.ARTIFACT,
    }
    incident_node_ids = {
        node_id
        for edge in edges
        for node_id in (edge.source, edge.target)
    }
    artifact_node_ids = {node.node_id for node in nodes if node.node_class == EvidenceGraphNodeClass.ARTIFACT}
    citation_node_ids = {node.node_id for node in nodes if node.node_class == EvidenceGraphNodeClass.CITATION}
    linked_artifact_nodes = len(artifact_node_ids & incident_node_ids)
    linked_citation_nodes = len(citation_node_ids & incident_node_ids)
    component_count = nx.number_weakly_connected_components(networkx_graph) if networkx_graph.nodes else 0
    isolated_node_ids = sorted(nx.isolates(networkx_graph))

    return EvidenceGraphStats(
        node_count=len(nodes),
        edge_count=len(edges),
        cluster_count=len(clusters),
        evidence_nodes=sum(1 for node in nodes if node.node_class in evidence_node_classes),
        citation_nodes=len(citation_node_ids),
        artifact_nodes=len(artifact_node_ids),
        linked_artifact_nodes=linked_artifact_nodes,
        artifact_linkage_ratio=_safe_linkage_ratio(len(artifact_node_ids), linked_artifact_nodes),
        linked_citation_nodes=linked_citation_nodes,
        citation_linkage_ratio=_safe_linkage_ratio(len(citation_node_ids), linked_citation_nodes),
        warning_nodes=sum(1 for node in nodes if node.node_class == EvidenceGraphNodeClass.WARNING),
        connected_component_count=component_count,
        weakly_connected=component_count <= 1,
        isolated_node_count=len(isolated_node_ids),
        isolated_node_ids=isolated_node_ids,
        required_node_classes=required_classes,
        present_node_classes=present_classes,
        missing_node_classes=missing_classes,
        completeness_ratio=1.0 if not required_classes else len(present_classes) / len(required_classes),
    )


def build_evidence_graph(
    decision: DecisionObject,
    *,
    artifact_manifest: ArtifactManifest | None = None,
    copilot: CopilotResponse | None = None,
    execution_gate: ExecutionGateReport | None = None,
    reasoning_trace: ReasoningTrace | None = None,
) -> EvidenceGraph:
    nodes = _build_nodes(
        decision,
        artifact_manifest=artifact_manifest,
        copilot=copilot,
        execution_gate=execution_gate,
        reasoning_trace=reasoning_trace,
    )
    nodes = _merge_duplicate_nodes(nodes)
    edges = _merge_duplicate_edges(_build_edges(
        decision,
        nodes,
        artifact_manifest=artifact_manifest,
        copilot=copilot,
        execution_gate=execution_gate,
        reasoning_trace=reasoning_trace,
    ))
    clusters = _build_clusters(nodes)
    networkx_graph = _build_networkx_graph(nodes, edges)
    component_count = nx.number_weakly_connected_components(networkx_graph) if networkx_graph.nodes else 0

    return EvidenceGraph(
        job_id=decision.job_id or decision.triage_decision.job_id,
        sample_id=decision.sample.sample_id,
        target_drug=decision.sample.target_drug,
        nodes=nodes,
        edges=edges,
        clusters=clusters,
        stats=_build_stats(nodes, edges, clusters, networkx_graph),
        metadata={
            "builder": "deterministic_decision_object_evidence_graph",
            "provider_calls_triggered": False,
            "networkx_component_count": component_count,
            "networkx_weakly_connected": component_count == 1,
        },
    )
