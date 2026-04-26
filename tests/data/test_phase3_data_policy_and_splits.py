from __future__ import annotations

import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def load_json(relative_path: str) -> dict:
    return json.loads((REPO_ROOT / relative_path).read_text(encoding="utf-8"))


def test_ast_filtering_rules_protect_against_weak_labels() -> None:
    rules = load_json("data/accessions/ast_label_filtering_rules.json")

    assert rules["allowed_primary_labels"] == ["resistant", "susceptible", "intermediate"]
    reason_codes = {entry["reason_code"] for entry in rules["exclude_rules"]}
    assert {
        "unknown_label",
        "inferred_only_label",
        "conflicting_duplicate_records",
        "target_drug_missing",
        "unclear_provenance",
    } <= reason_codes
    assert any("fixture" in entry["rule_id"] for entry in rules["include_rules"])


def test_snapshot_manifest_checksum_matches_seed_manifest() -> None:
    snapshot = load_json("data/snapshots/2026-04-20_phase3_foundation_snapshot.json")
    seed_manifest_path = REPO_ROOT / snapshot["seed_manifest_file"]
    actual_checksum = hashlib.sha256(seed_manifest_path.read_bytes()).hexdigest()

    assert snapshot["metadata_checksum_sha256"] == actual_checksum
    assert snapshot["raw_data_roots"] == ["data/downloads", "data/cache"]
    assert len(snapshot["committed_summary_files"]) >= 10


def test_split_strategy_and_example_manifest_keep_lineage_groups_together() -> None:
    strategy = load_json("data/snapshots/split_strategy.json")
    manifest_path = REPO_ROOT / "data/snapshots/example_split_manifest.csv"
    rows = list(csv.DictReader(manifest_path.read_text(encoding="utf-8").splitlines()))
    lineage_split_map: dict[str, set[str]] = defaultdict(set)

    for row in rows:
        lineage_split_map[row["lineage_group_id"]].add(row["lineage_aware_split"])

    assert strategy["random_baseline"]["seed"] == 20260420
    assert strategy["lineage_aware"]["max_allowed_group_leakage"] == 0
    assert set(strategy["output_manifest_fields"]) == {
        "record_id",
        "sample_id",
        "organism",
        "target_drug",
        "phenotype",
        "lineage_group_id",
        "random_split",
        "lineage_aware_split",
        "notes",
    }
    assert all(len(split_names) == 1 for split_names in lineage_split_map.values())


def test_qa_template_requires_fit_for_use_sections() -> None:
    template = load_json("data/snapshots/dataset_qa_report_template.json")

    assert "fit_for_use_decision" in template["required_sections"]
    assert "organism_drug_counts" in template["required_tables"]
    assert any("excluded" in question for question in template["required_questions"])


def test_acceptance_matrix_keeps_fallbacks_explicit() -> None:
    matrix = load_json("data/snapshots/dataset_acceptance_matrix.json")

    assert matrix["smoke_workflow"]["fail"]["next_action"] == "repair fixture path before proceeding"
    assert matrix["training_readiness"]["pass"]["lineage_aware_split_ready"] is True
    assert any("fixture-backed" in action for action in matrix["fallback_actions"])
    assert any("smoke pair" in criterion for criterion in matrix["expansion_criteria"])
