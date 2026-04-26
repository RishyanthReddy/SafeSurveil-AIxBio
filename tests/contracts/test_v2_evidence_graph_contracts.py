from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.contracts import (
    EVIDENCE_GRAPH_SCHEMA_VERSION,
    EvidenceGraph,
    EvidenceGraphDetailField,
    EvidenceGraphNodeClass,
    EvidenceGraphStats,
    EVIDENCE_GRAPH_REQUIRED_NODE_CLASSES,
)

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "v2_evidence_graph_example.json"


def load_evidence_graph_payload() -> dict[str, object]:
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


def test_evidence_graph_fixture_validates_and_normalizes_ids() -> None:
    graph = EvidenceGraph(**load_evidence_graph_payload())

    assert graph.schema_version == EVIDENCE_GRAPH_SCHEMA_VERSION
    assert graph.job_id == "job_graph_001"
    assert graph.sample_id == "sample_ecoli_001"
    assert graph.target_drug == "tetracycline"
    assert graph.nodes[0].node_id == "sample__sample_ecoli_001"
    assert graph.edges[0].source == "sample__sample_ecoli_001"
    assert graph.stats.completeness_ratio == pytest.approx(1.0)
    assert graph.stats.artifact_linkage_ratio == pytest.approx(1.0)
    assert graph.stats.citation_linkage_ratio == pytest.approx(1.0)
    assert graph.stats.weakly_connected is True
    assert graph.stats.isolated_node_ids == []
    assert EvidenceGraphNodeClass.SAMPLE in graph.stats.present_node_classes


def test_evidence_graph_rejects_unknown_node_class() -> None:
    payload = load_evidence_graph_payload()
    nodes = payload["nodes"]
    assert isinstance(nodes, list)
    first_node = nodes[0]
    assert isinstance(first_node, dict)
    first_node["node_class"] = "unknown_node_class"

    with pytest.raises(ValidationError, match="node_class"):
        EvidenceGraph(**payload)


def test_evidence_graph_rejects_unknown_edge_class() -> None:
    payload = load_evidence_graph_payload()
    edges = payload["edges"]
    assert isinstance(edges, list)
    first_edge = edges[0]
    assert isinstance(first_edge, dict)
    first_edge["edge_class"] = "unknown_edge_class"

    with pytest.raises(ValidationError, match="edge_class"):
        EvidenceGraph(**payload)


def test_evidence_graph_rejects_dangling_edge_reference() -> None:
    payload = load_evidence_graph_payload()
    edges = payload["edges"]
    assert isinstance(edges, list)
    first_edge = edges[0]
    assert isinstance(first_edge, dict)
    first_edge["target"] = "missing_node"

    with pytest.raises(ValidationError, match="edges must reference existing node IDs"):
        EvidenceGraph(**payload)


def test_evidence_graph_rejects_cluster_with_unknown_node() -> None:
    payload = load_evidence_graph_payload()
    clusters = payload["clusters"]
    assert isinstance(clusters, list)
    first_cluster = clusters[0]
    assert isinstance(first_cluster, dict)
    first_cluster["node_ids"] = [*first_cluster["node_ids"], "missing_node"]

    with pytest.raises(ValidationError, match="clusters must reference existing node IDs"):
        EvidenceGraph(**payload)


def test_evidence_graph_rejects_duplicate_node_ids() -> None:
    payload = load_evidence_graph_payload()
    nodes = payload["nodes"]
    assert isinstance(nodes, list)
    duplicate_node = deepcopy(nodes[0])
    assert isinstance(duplicate_node, dict)
    nodes.append(duplicate_node)
    stats = payload["stats"]
    assert isinstance(stats, dict)
    stats["node_count"] = len(nodes)

    with pytest.raises(ValidationError, match="must not repeat node_id"):
        EvidenceGraph(**payload)


def test_evidence_graph_rejects_bad_stats_counts() -> None:
    payload = load_evidence_graph_payload()
    stats = payload["stats"]
    assert isinstance(stats, dict)
    stats["edge_count"] = 999

    with pytest.raises(ValidationError, match="edge_count must match"):
        EvidenceGraph(**payload)


def test_evidence_graph_stats_reject_bad_completeness_ratio() -> None:
    with pytest.raises(ValidationError, match="completeness_ratio"):
        EvidenceGraphStats(
            node_count=1,
            edge_count=0,
            cluster_count=0,
            evidence_nodes=0,
            citation_nodes=0,
            required_node_classes=list(EVIDENCE_GRAPH_REQUIRED_NODE_CLASSES),
            present_node_classes=list(EVIDENCE_GRAPH_REQUIRED_NODE_CLASSES),
            missing_node_classes=[],
            completeness_ratio=0.5,
        )


def test_evidence_graph_stats_reject_bad_artifact_linkage_ratio() -> None:
    with pytest.raises(ValidationError, match="artifact_linkage_ratio"):
        EvidenceGraphStats(
            node_count=2,
            edge_count=1,
            cluster_count=0,
            evidence_nodes=1,
            citation_nodes=0,
            artifact_nodes=2,
            linked_artifact_nodes=1,
            artifact_linkage_ratio=1.0,
            connected_component_count=1,
            required_node_classes=list(EVIDENCE_GRAPH_REQUIRED_NODE_CLASSES),
            present_node_classes=list(EVIDENCE_GRAPH_REQUIRED_NODE_CLASSES),
            missing_node_classes=[],
            completeness_ratio=1.0,
        )


def test_evidence_graph_accepts_disconnected_graph_with_isolated_warning_stats() -> None:
    payload = load_evidence_graph_payload()
    nodes = payload["nodes"]
    assert isinstance(nodes, list)
    nodes.append(
        {
            "node_id": "warning__orphan_metric",
            "node_class": "warning",
            "label": "Isolated metric",
            "summary": "Synthetic fixture warning used to prove isolated-node stats are transparent.",
            "evidence_refs": ["decision_object__warnings"],
            "style": {
                "tone": "caveat",
                "importance": 2,
            },
        }
    )
    stats = payload["stats"]
    assert isinstance(stats, dict)
    stats.update(
        {
            "node_count": 14,
            "warning_nodes": 1,
            "connected_component_count": 2,
            "weakly_connected": False,
            "isolated_node_count": 1,
            "isolated_node_ids": ["warning__orphan_metric"],
        }
    )

    graph = EvidenceGraph(**payload)

    assert graph.stats.weakly_connected is False
    assert graph.stats.connected_component_count == 2
    assert graph.stats.isolated_node_ids == ["warning__orphan_metric"]


def test_evidence_graph_rejects_bad_isolated_node_stats() -> None:
    payload = load_evidence_graph_payload()
    stats = payload["stats"]
    assert isinstance(stats, dict)
    stats["isolated_node_count"] = 1
    stats["isolated_node_ids"] = ["missing_node"]

    with pytest.raises(ValidationError, match="isolated_node_ids must match graph nodes"):
        EvidenceGraph(**payload)


def test_evidence_graph_rejects_secret_like_detail_keys_and_values() -> None:
    with pytest.raises(ValidationError, match="secret-like"):
        EvidenceGraphDetailField(
            key="api_key",
            label="API key",
            value="configured",
        )

    with pytest.raises(ValidationError, match="secret-like"):
        EvidenceGraphDetailField(
            key="safe_context",
            label="Safe context",
            value="Authorization: Bearer should never be shown",
        )


def test_evidence_graph_forbids_hidden_private_payloads() -> None:
    payload = load_evidence_graph_payload()
    nodes = payload["nodes"]
    assert isinstance(nodes, list)
    first_node = deepcopy(nodes[0])
    assert isinstance(first_node, dict)
    first_node["private_reasoning"] = "Hidden chain-of-thought must not be exposed."
    nodes[0] = first_node

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        EvidenceGraph(**payload)
