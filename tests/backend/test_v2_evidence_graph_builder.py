from __future__ import annotations

from datetime import date

import pytest

from app.contracts import (
    ActionabilityFeatures,
    ArtifactKind,
    ArtifactManifest,
    ArtifactRecord,
    AssemblyQC,
    CalibrationStatus,
    CopilotAnswerBlock,
    CopilotResponse,
    DecisionObject,
    EvidenceGraphEdgeClass,
    EvidenceGraphNodeClass,
    EvidenceGraphStyleTone,
    MechanisticEvidence,
    MechanismSupportLevel,
    NoveltyAssessment,
    NoveltyBucket,
    OrganismConsistency,
    OrganismHint,
    PhenotypePrediction,
    PredictedPhenotype,
    QCStatus,
    SampleInput,
    SampleMetadata,
    SourceContext,
    TriageDecision,
)
from app.services.evidence_graph import build_evidence_graph
from app.services.reasoning_trace import build_reasoning_trace
from app.services.verification import build_execution_gate_report


def build_decision(
    *,
    include_mechanism: bool = True,
    mechanism_drug_association: bool = True,
    gene_symbol: str = "tetB",
    extra_gene_symbols: list[str] | None = None,
    novelty_bucket: NoveltyBucket = NoveltyBucket.KNOWN,
    novelty_score: float = 0.12,
    rationale_codes: list[str] | None = None,
) -> DecisionObject:
    sample = SampleInput(
        sample_id="sample_001",
        organism_hint=OrganismHint.E_COLI,
        target_drug="tetracycline",
        fasta_path="data/fixtures/sample.fa",
        metadata=SampleMetadata(
            accession="acc_001",
            collection_date=date(2026, 4, 20),
            source_context=SourceContext.SURVEILLANCE_PROXY,
        ),
    )
    triage = TriageDecision(
        job_id="job_001",
        sample_id="sample_001",
        target_drug="tetracycline",
        triage="review",
        severity="high",
        recommended_next_step="Route to analyst review with evidence and uncertainty context.",
        threshold_version="policy_v1",
        rationale_codes=rationale_codes
        or ["actionability_threshold_met", "concordant_signal_present", "supported_mechanism_present"],
    )
    mechanism_genes = [gene_symbol, *(extra_gene_symbols or [])]
    mechanistic_evidence = (
        [
            MechanisticEvidence(
                job_id="job_001",
                sample_id="sample_001",
                target_drug="tetracycline",
                gene_symbol=mechanism_gene,
                mechanism_class="efflux",
                drug_association=["tetracycline"] if mechanism_drug_association else [],
                support_level="supported",
                interpretation=f"Detected {mechanism_gene} in the persisted AMRFinderPlus evidence.",
                raw_artifact_id="job_001_amrfinder_raw",
            )
            for mechanism_gene in mechanism_genes
        ]
        if include_mechanism
        else []
    )
    return DecisionObject(
        job_id="job_001",
        sample=sample,
        assembly_qc=AssemblyQC(
            job_id="job_001",
            sample_id="sample_001",
            target_drug="tetracycline",
            file_valid=True,
            sequence_count=4,
            total_bases=5000,
            ambiguous_base_fraction=0.01,
            organism_consistency=OrganismConsistency.MATCH,
            qc_status=QCStatus.PASS,
        ),
        mechanistic_evidence=mechanistic_evidence,
        phenotype_prediction=PhenotypePrediction(
            job_id="job_001",
            sample_id="sample_001",
            target_drug="tetracycline",
            predicted_phenotype=PredictedPhenotype.RESISTANT,
            probability=0.953,
            calibration_status=CalibrationStatus.NOT_AVAILABLE,
            uncertainty_score=0.27,
            feature_set_version="features_v1",
            model_version="baseline_v1",
        ),
        novelty_assessment=NoveltyAssessment(
            job_id="job_001",
            sample_id="sample_001",
            target_drug="tetracycline",
            reference_snapshot_id="reference_snapshot_v1",
            nearest_neighbor_id="reference_ec_001",
            nearest_neighbor_distance=0.12,
            novelty_score=novelty_score,
            novelty_percentile=72.0,
            novelty_bucket=novelty_bucket,
            uncertainty_flag=novelty_bucket == NoveltyBucket.HIGH,
        ),
        actionability_features=ActionabilityFeatures(
            job_id="job_001",
            sample_id="sample_001",
            target_drug="tetracycline",
            actionability_score=0.86,
            mechanism_concordance=include_mechanism,
            prediction_entropy=0.27,
            qc_risk=0.0,
            novelty_risk=novelty_score,
            metadata_completeness=1.0,
            threshold_version="policy_v1",
        ),
        triage_decision=triage,
        rationale_codes=triage.rationale_codes,
    )


def build_artifact_manifest() -> ArtifactManifest:
    return ArtifactManifest(
        job_id="job_001",
        sample_id="sample_001",
        target_drug="tetracycline",
        artifacts=[
            ArtifactRecord(
                artifact_id="job_001_amrfinder_raw",
                job_id="job_001",
                sample_id="sample_001",
                target_drug="tetracycline",
                kind=ArtifactKind.MECHANISTIC_EVIDENCE,
                path="artifacts/runs/jobs/job_001/evidence/sample_001.amrfinder.tsv",
                media_type="text/tab-separated-values",
                generated_by="amrfinderplus",
            )
        ],
    )


def build_copilot() -> CopilotResponse:
    return CopilotResponse(
        job_id="job_001",
        sample_id="sample_001",
        target_drug="tetracycline",
        summary="Grounded summary cites the persisted decision and mechanism.",
        next_steps=["Route to analyst review with evidence and uncertainty context."],
        cited_evidence_ids=["decision_object__summary", "mechanistic_evidence__1"],
        answer_blocks=[
            CopilotAnswerBlock(
                block_id="evidence_summary",
                block_type="summary",
                title="Evidence summary",
                content="Mechanistic evidence and novelty are cited for review.",
                cited_evidence_ids=["mechanistic_evidence__1", "novelty_assessment__summary"],
            )
        ],
    )


def node_ids_by_class(graph, node_class: EvidenceGraphNodeClass) -> set[str]:
    return {node.node_id for node in graph.nodes if node.node_class == node_class}


def test_evidence_graph_builder_emits_connected_complete_graph() -> None:
    decision = build_decision()
    artifacts = build_artifact_manifest()
    copilot = build_copilot()
    trace = build_reasoning_trace(decision)
    gate = build_execution_gate_report(
        decision,
        artifact_manifest=artifacts,
        copilot=copilot,
        reasoning_trace=trace,
    )

    graph = build_evidence_graph(
        decision,
        artifact_manifest=artifacts,
        copilot=copilot,
        execution_gate=gate,
        reasoning_trace=trace,
    )

    assert graph.metadata["provider_calls_triggered"] is False
    assert graph.metadata["networkx_weakly_connected"] is True
    assert graph.stats.weakly_connected is True
    assert graph.stats.connected_component_count == 1
    assert graph.stats.isolated_node_count == 0
    assert graph.stats.isolated_node_ids == []
    assert graph.stats.completeness_ratio == pytest.approx(1.0)
    assert graph.stats.missing_node_classes == []
    assert graph.stats.artifact_nodes == 1
    assert graph.stats.linked_artifact_nodes == 1
    assert graph.stats.artifact_linkage_ratio == pytest.approx(1.0)
    assert graph.stats.linked_citation_nodes == graph.stats.citation_nodes
    assert graph.stats.citation_linkage_ratio == pytest.approx(1.0)
    assert node_ids_by_class(graph, EvidenceGraphNodeClass.SAMPLE) == {"sample__sample_001"}
    assert node_ids_by_class(graph, EvidenceGraphNodeClass.GENE) == {"gene__tetb"}
    assert "execution_gate__report" in node_ids_by_class(graph, EvidenceGraphNodeClass.EXECUTION_GATE)
    assert "reasoning_trace__summary" in node_ids_by_class(graph, EvidenceGraphNodeClass.REASONING_TRACE)
    assert graph.stats.citation_nodes >= 3


def test_evidence_graph_builder_uses_stable_ids() -> None:
    decision = build_decision()
    artifacts = build_artifact_manifest()
    copilot = build_copilot()

    first = build_evidence_graph(decision, artifact_manifest=artifacts, copilot=copilot)
    second = build_evidence_graph(decision, artifact_manifest=artifacts, copilot=copilot)

    assert [node.node_id for node in first.nodes] == [node.node_id for node in second.nodes]
    assert [edge.edge_id for edge in first.edges] == [edge.edge_id for edge in second.edges]


def test_evidence_graph_builder_sanitizes_biological_symbol_node_ids() -> None:
    graph = build_evidence_graph(
        build_decision(gene_symbol="aph(3'')-Ib"),
        artifact_manifest=build_artifact_manifest(),
        copilot=build_copilot(),
    )

    assert "gene__aph_3_ib" in node_ids_by_class(graph, EvidenceGraphNodeClass.GENE)
    assert graph.stats.weakly_connected is True
    assert graph.stats.isolated_node_ids == []


def test_evidence_graph_builder_merges_repeated_mechanism_classes() -> None:
    graph = build_evidence_graph(
        build_decision(
            gene_symbol="aph(6)-Id",
            extra_gene_symbols=["aph(3'')-Ib"],
        ),
        artifact_manifest=build_artifact_manifest(),
        copilot=build_copilot(),
    )

    assert node_ids_by_class(graph, EvidenceGraphNodeClass.GENE) == {
        "gene__aph_3_ib",
        "gene__aph_6_id",
    }
    assert node_ids_by_class(graph, EvidenceGraphNodeClass.MECHANISM) == {"mechanism__efflux"}
    assert len({edge.edge_id for edge in graph.edges}) == len(graph.edges)
    assert graph.stats.weakly_connected is True


def test_evidence_graph_builder_caveats_off_target_mechanisms() -> None:
    decision = build_decision(
        gene_symbol="tetB",
        extra_gene_symbols=["blaTEM-1", "tetA"],
    )
    decision.mechanistic_evidence[1].mechanism_class = "beta_lactamase"
    decision.mechanistic_evidence[1].drug_association = ["ampicillin"]
    decision.mechanistic_evidence[2].mechanism_class = "ribosomal_protection"
    decision.mechanistic_evidence[2].support_level = MechanismSupportLevel.WEAK

    graph = build_evidence_graph(
        decision,
        artifact_manifest=build_artifact_manifest(),
        copilot=build_copilot(),
    )

    assert any(
        edge.edge_class == EvidenceGraphEdgeClass.SUPPORTS
        and edge.source == "mechanism__efflux"
        and edge.target == "actionability__score"
        for edge in graph.edges
    )
    assert not any(
        edge.edge_class == EvidenceGraphEdgeClass.SUPPORTS
        and edge.source == "mechanism__beta_lactamase"
        and edge.target == "actionability__score"
        for edge in graph.edges
    )
    assert any(
        edge.edge_class == EvidenceGraphEdgeClass.CAVEATS
        and edge.source == "mechanism__beta_lactamase"
        and edge.target == "actionability__score"
        for edge in graph.edges
    )
    assert not any(
        edge.edge_class == EvidenceGraphEdgeClass.SUPPORTS
        and edge.source == "mechanism__ribosomal_protection"
        and edge.target == "actionability__score"
        for edge in graph.edges
    )
    assert any(
        edge.edge_class == EvidenceGraphEdgeClass.ASSOCIATED_WITH
        and edge.source == "mechanism__ribosomal_protection"
        and edge.target == "drug__tetracycline"
        for edge in graph.edges
    )
    assert any(
        edge.edge_class == EvidenceGraphEdgeClass.CAVEATS
        and edge.source == "mechanism__ribosomal_protection"
        and edge.target == "actionability__score"
        for edge in graph.edges
    )


def test_evidence_graph_builder_marks_missing_mechanism_without_invention() -> None:
    graph = build_evidence_graph(
        build_decision(
            include_mechanism=False,
            rationale_codes=["no_supported_mechanism", "manual_confirmation_required"],
        ),
        artifact_manifest=build_artifact_manifest(),
        copilot=build_copilot(),
    )

    assert "warning__mechanistic_evidence_missing" in node_ids_by_class(graph, EvidenceGraphNodeClass.WARNING)
    assert not node_ids_by_class(graph, EvidenceGraphNodeClass.GENE)
    assert not node_ids_by_class(graph, EvidenceGraphNodeClass.MECHANISM)
    assert any(
        edge.edge_class == EvidenceGraphEdgeClass.CAVEATS
        and edge.source == "warning__mechanistic_evidence_missing"
        for edge in graph.edges
    )
    assert graph.stats.completeness_ratio == pytest.approx(1.0)


def test_evidence_graph_builder_marks_missing_artifact_manifest() -> None:
    graph = build_evidence_graph(build_decision(), artifact_manifest=None, copilot=build_copilot())

    assert "warning__artifact_manifest_missing" in node_ids_by_class(graph, EvidenceGraphNodeClass.WARNING)
    assert not node_ids_by_class(graph, EvidenceGraphNodeClass.ARTIFACT)
    assert any(edge.edge_id == "edge__missing_artifact_to_sample" for edge in graph.edges)
    assert graph.metadata["networkx_weakly_connected"] is True
    assert graph.stats.artifact_nodes == 0
    assert graph.stats.linked_artifact_nodes == 0
    assert graph.stats.artifact_linkage_ratio == pytest.approx(1.0)
    assert graph.stats.warning_nodes >= 1
    assert graph.stats.isolated_node_ids == []


def test_evidence_graph_builder_marks_missing_copilot_sidecar() -> None:
    graph = build_evidence_graph(build_decision(), artifact_manifest=build_artifact_manifest(), copilot=None)

    assert "warning__copilot_sidecar_missing" in node_ids_by_class(graph, EvidenceGraphNodeClass.WARNING)
    assert not node_ids_by_class(graph, EvidenceGraphNodeClass.COPILOT)
    assert not node_ids_by_class(graph, EvidenceGraphNodeClass.CITATION)
    assert any(edge.edge_id == "edge__missing_copilot_to_decision" for edge in graph.edges)
    assert graph.stats.citation_nodes == 0
    assert graph.stats.linked_citation_nodes == 0
    assert graph.stats.citation_linkage_ratio == pytest.approx(1.0)
    assert graph.stats.warning_nodes >= 1


def test_evidence_graph_builder_marks_high_novelty_as_caveated_risk_signal() -> None:
    graph = build_evidence_graph(
        build_decision(
            novelty_bucket=NoveltyBucket.HIGH,
            novelty_score=1.0,
            rationale_codes=["high_lineage_novelty", "manual_confirmation_required"],
        ),
        artifact_manifest=build_artifact_manifest(),
        copilot=build_copilot(),
    )
    novelty_nodes = [
        node for node in graph.nodes if node.node_class == EvidenceGraphNodeClass.NOVELTY
    ]

    assert len(novelty_nodes) == 1
    assert novelty_nodes[0].style.tone == EvidenceGraphStyleTone.CAVEAT
    assert novelty_nodes[0].details[0].value == pytest.approx(1.0)
    assert any(
        edge.edge_class == EvidenceGraphEdgeClass.CONSTRAINS
        and edge.source == novelty_nodes[0].node_id
        and edge.target == "actionability__score"
        for edge in graph.edges
    )
