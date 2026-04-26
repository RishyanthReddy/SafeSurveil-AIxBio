from __future__ import annotations

import csv
import json
from pathlib import Path

from app.contracts import AnalyzeJobRequest, OrganismHint

REPO_ROOT = Path(__file__).resolve().parents[2]


def load_json(relative_path: str) -> dict:
    return json.loads((REPO_ROOT / relative_path).read_text(encoding="utf-8"))


def test_dataset_scope_locks_smoke_workflow() -> None:
    scope = load_json("data/accessions/dataset_scope.json")

    assert scope["planning_status"] == "locked_for_mvp"
    assert scope["smoke_workflow"]["organism"] == "e_coli"
    assert scope["smoke_workflow"]["target_drug"] == "tetracycline"
    assert set(scope["locked_organisms"]) == {"e_coli", "s_aureus"}
    assert scope["guardrails"]["max_unique_drugs_for_mvp"] == 5


def test_source_priority_policy_covers_required_fields() -> None:
    policy = load_json("data/accessions/source_priority_policy.json")
    required_fields = {
        "assembly_fasta",
        "assembly_accession",
        "biosample_accession",
        "organism",
        "target_drug",
        "phenotype_label",
        "collection_date",
        "country",
        "source_context",
    }

    assert {entry["source_id"] for entry in policy["source_order"]} >= {
        "bv_brc_amr_metadata",
        "ncbi_datasets_genome_package",
    }
    assert set(policy["field_priority"]) == required_fields
    assert all(
        entry["official_reference_url"].startswith("https://")
        for entry in policy["source_order"]
    )


def test_seed_manifest_contains_fixture_and_planned_public_pairs() -> None:
    manifest_path = REPO_ROOT / "data/accessions/seed_accession_manifest.csv"
    rows = list(csv.DictReader(manifest_path.read_text(encoding="utf-8").splitlines()))

    assert len(rows) == 7
    assert rows[0]["record_kind"] == "fixture"
    assert rows[0]["sample_id"] == "sample_001"
    planned_pairs = {(row["organism"], row["target_drug"]) for row in rows[1:]}
    assert planned_pairs == {
        ("e_coli", "tetracycline"),
        ("e_coli", "ampicillin"),
        ("e_coli", "ciprofloxacin"),
        ("s_aureus", "tetracycline"),
        ("s_aureus", "oxacillin"),
        ("s_aureus", "erythromycin"),
    }


def test_fixture_metadata_validates_against_api_request_contract() -> None:
    payload = load_json("data/fixtures/smoke/sample_001.metadata.json")
    fixture_request = AnalyzeJobRequest(
        sample_id=payload["sample_id"],
        organism_hint=payload["organism_hint"],
        target_drug=payload["target_drug"],
        fasta_path=payload["fasta_path"],
        metadata=payload["metadata"],
    )
    fasta_text = (REPO_ROOT / payload["fasta_path"]).read_text(encoding="utf-8")

    assert fixture_request.organism_hint == OrganismHint.E_COLI
    assert fixture_request.sample_id == "sample_001"
    assert fixture_request.target_drug == "tetracycline"
    assert fasta_text.startswith(">sample_001_contig_1")
    assert set("".join(line.strip() for line in fasta_text.splitlines() if not line.startswith(">"))) <= {
        "A",
        "C",
        "G",
        "T",
    }


def test_foundation_snapshot_references_existing_files() -> None:
    snapshot = load_json("data/snapshots/2026-04-20_phase3_foundation_snapshot.json")
    referenced_files = [
        snapshot["scope_file"],
        snapshot["source_policy_file"],
        snapshot["manifest_schema_file"],
        snapshot["seed_manifest_file"],
        snapshot["normalization_rules_file"],
        snapshot["label_filtering_rules_file"],
        snapshot["freeze_procedure_file"],
        snapshot["split_strategy_file"],
        snapshot["example_split_manifest_file"],
        snapshot["dataset_qa_template_file"],
        snapshot["dataset_acceptance_matrix_file"],
        snapshot["fixture_policy_file"],
        *snapshot["fixture_files"],
        *snapshot["mash_reference_files"],
    ]

    assert snapshot["status"] == "phase3_policy_ready"
    assert snapshot["record_counts"] == {"fixture_rows": 1, "planned_public_rows": 6}
    assert all((REPO_ROOT / relative_path).exists() for relative_path in referenced_files)


def test_metadata_normalization_rules_cover_mvp_entities() -> None:
    rules = load_json("data/accessions/metadata_normalization_rules.json")

    assert rules["organism_aliases"]["escherichia coli"] == "e_coli"
    assert rules["organism_aliases"]["staphylococcus aureus"] == "s_aureus"
    assert rules["drug_aliases"]["tetracycline_hcl"] == "tetracycline"
    assert "methicillin" in rules["manual_review_required_drug_terms"]
    assert rules["phenotype_aliases"]["r"] == "resistant"
    assert "unknown" in rules["missing_value_markers"]
