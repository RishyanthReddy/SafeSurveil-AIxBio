from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.contracts import (
    EvidenceTableBlock,
    EvidenceTableRow,
    MechanismSupportLevel,
    MechanisticEvidence,
    PredictedPhenotype,
    QCStatus,
    SampleInput,
)
from app.evidence import (
    MechanismConcordanceClassification,
    assess_mechanism_concordance,
    build_amrfinderplus_command,
    normalize_amrfinderplus_output,
    plan_amrfinderplus_execution,
    plan_mash_reference_workflow,
    validate_sample_for_evidence,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def load_smoke_sample() -> SampleInput:
    payload = json.loads((REPO_ROOT / "data/fixtures/smoke/sample_001.metadata.json").read_text(encoding="utf-8"))
    return SampleInput(
        sample_id=payload["sample_id"],
        organism_hint=payload["organism_hint"],
        target_drug=payload["target_drug"],
        fasta_path=payload["fasta_path"],
        metadata=payload["metadata"],
    )


def test_validation_accepts_known_good_fixture() -> None:
    qc = validate_sample_for_evidence(load_smoke_sample(), repo_root=REPO_ROOT)

    assert qc.file_valid is True
    assert qc.sequence_count == 2
    assert qc.total_bases > 0
    assert qc.qc_status == QCStatus.PASS


def test_validation_warns_for_missing_metadata() -> None:
    sample = load_smoke_sample()
    sample.metadata.country = None
    sample.metadata.collection_date = None
    qc = validate_sample_for_evidence(sample, repo_root=REPO_ROOT)

    assert qc.qc_status == QCStatus.WARN
    assert qc.missing_metadata_fields == ["collection_date", "country"]


def test_validation_rejects_malformed_fasta() -> None:
    temp_fasta = REPO_ROOT / "data/fixtures/smoke/temp_malformed.fasta"
    try:
        temp_fasta.write_text("ACGTACGT", encoding="utf-8")
        sample = load_smoke_sample()
        sample.fasta_path = "data/fixtures/smoke/temp_malformed.fasta"
        qc = validate_sample_for_evidence(sample, repo_root=REPO_ROOT)
        assert qc.file_valid is False
        assert qc.qc_status == QCStatus.FAIL
        assert "header" in qc.warnings[0].lower()
    finally:
        temp_fasta.unlink(missing_ok=True)


def test_validation_rejects_unsupported_drug() -> None:
    sample = load_smoke_sample()
    sample.target_drug = "vancomycin"
    qc = validate_sample_for_evidence(sample, repo_root=REPO_ROOT)

    assert qc.file_valid is False
    assert qc.qc_status == QCStatus.FAIL
    assert "outside the locked mvp scope" in " ".join(qc.warnings).lower()


def test_validation_rejects_drug_outside_organism_panel() -> None:
    sample = load_smoke_sample()
    sample.organism_hint = "e_coli"
    sample.target_drug = "oxacillin"
    qc = validate_sample_for_evidence(sample, repo_root=REPO_ROOT)

    assert qc.file_valid is False
    assert qc.qc_status == QCStatus.FAIL
    assert "outside the locked mvp scope" in " ".join(qc.warnings).lower()


def test_validation_accepts_drug_inside_organism_panel() -> None:
    sample = load_smoke_sample()
    sample.organism_hint = "s_aureus"
    sample.target_drug = "oxacillin"
    qc = validate_sample_for_evidence(sample, repo_root=REPO_ROOT)

    assert qc.file_valid is True
    assert qc.qc_status == QCStatus.PASS


def test_amrfinder_command_shape_matches_expected_flags() -> None:
    sample = load_smoke_sample()
    command = build_amrfinderplus_command(sample, Path("artifacts/demo/sample_001.amrfinder.tsv"))

    assert command[:2] == ("amrfinder", "-n")
    assert command[-2] == "-o"
    assert Path(command[-1]) == Path("artifacts/demo/sample_001.amrfinder.tsv")


def test_amrfinder_plan_resolves_relative_fasta_against_repo_root() -> None:
    plan = plan_amrfinderplus_execution(
        load_smoke_sample(),
        output_dir=REPO_ROOT / "artifacts/runs/amrfinder",
        fixture_mode=False,
        allow_fixture_fallback=True,
        repo_root=REPO_ROOT,
    )

    fasta_arg = Path(plan.command[2])
    assert fasta_arg == (REPO_ROOT / "data/fixtures/smoke/sample_001.fasta")


def test_amrfinder_fixture_plan_returns_existing_output() -> None:
    plan = plan_amrfinderplus_execution(
        load_smoke_sample(),
        output_dir=REPO_ROOT / "artifacts/runs/amrfinder",
        fixture_mode=True,
        repo_root=REPO_ROOT,
    )

    assert plan.mode == "fixture"
    assert plan.status == "fixture_ready"
    assert plan.raw_output_path.exists()


def test_amrfinder_normalization_maps_fixture_rows() -> None:
    normalized = normalize_amrfinderplus_output(
        REPO_ROOT / "data/fixtures/smoke/sample_001.amrfinder.tsv",
        job_id="job_smoke_001",
        sample_id="sample_001",
        target_drug="tetracycline",
        raw_artifact_id="job_smoke_001_amrfinder_raw",
    )

    assert len(normalized) == 2
    assert normalized[0].gene_symbol == "tetA"
    assert normalized[0].job_id == "job_smoke_001"
    assert normalized[0].target_drug == "tetracycline"
    assert normalized[0].support_level == MechanismSupportLevel.SUPPORTED
    assert normalized[0].drug_association == ["tetracycline"]
    assert normalized[0].raw_artifact_id == "job_smoke_001_amrfinder_raw"
    assert normalized[1].mechanism_class == "beta_lactamase"


def test_normalized_evidence_can_feed_evidence_table_contract() -> None:
    evidence_rows = normalize_amrfinderplus_output(
        REPO_ROOT / "data/fixtures/smoke/sample_001.amrfinder.tsv",
        job_id="job_smoke_001",
        sample_id="sample_001",
        target_drug="tetracycline",
        raw_artifact_id="job_smoke_001_amrfinder_raw",
    )
    table = EvidenceTableBlock(
        title="Mechanistic Evidence",
        columns=["gene_symbol", "mechanism_class", "support_level"],
        rows=[
            EvidenceTableRow(
                row_id=f"row_{index}",
                label=row.gene_symbol or row.mechanism_class,
                cells={
                    "gene_symbol": row.gene_symbol or "",
                    "mechanism_class": row.mechanism_class,
                    "support_level": row.support_level.value,
                },
                evidence_id=f"ev_{index}",
            )
            for index, row in enumerate(evidence_rows, start=1)
        ],
    )

    assert len(table.rows) == 2
    assert table.rows[0].cells["gene_symbol"] == "tetA"


def test_concordance_detects_supported_mechanism() -> None:
    evidence_rows = normalize_amrfinderplus_output(
        REPO_ROOT / "data/fixtures/smoke/sample_001.amrfinder.tsv",
        job_id="job_smoke_001",
        sample_id="sample_001",
        target_drug="tetracycline",
        raw_artifact_id="job_smoke_001_amrfinder_raw",
    )
    result = assess_mechanism_concordance(
        target_drug="tetracycline",
        predicted_phenotype=PredictedPhenotype.RESISTANT,
        evidence_rows=evidence_rows,
    )

    assert result.classification == MechanismConcordanceClassification.SUPPORTED
    assert result.mechanism_concordance is True


def test_concordance_detects_missing_mechanism() -> None:
    evidence_rows = normalize_amrfinderplus_output(
        REPO_ROOT / "data/fixtures/smoke/sample_001.amrfinder.tsv",
        job_id="job_smoke_001",
        sample_id="sample_001",
        target_drug="tetracycline",
        raw_artifact_id="job_smoke_001_amrfinder_raw",
    )
    result = assess_mechanism_concordance(
        target_drug="ciprofloxacin",
        predicted_phenotype=PredictedPhenotype.RESISTANT,
        evidence_rows=evidence_rows,
    )

    assert result.classification == MechanismConcordanceClassification.MISSING
    assert result.mechanism_concordance is False


def test_concordance_detects_ambiguous_mechanism() -> None:
    weak_row = MechanisticEvidence(
        job_id="job_smoke_001",
        sample_id="sample_001",
        target_drug="tetracycline",
        source_tool="amrfinderplus",
        gene_symbol="tetX",
        mechanism_class="efflux",
        drug_association=["tetracycline"],
        support_level="weak",
        interpretation="Detected tetX via hmm with limited support.",
        raw_row_index=0,
        raw_artifact_id="fixture_weak",
    )
    result = assess_mechanism_concordance(
        target_drug="tetracycline",
        predicted_phenotype=PredictedPhenotype.RESISTANT,
        evidence_rows=[weak_row],
    )

    assert result.classification == MechanismConcordanceClassification.AMBIGUOUS
    assert result.mechanism_concordance is None


def test_mash_reference_plan_ties_to_snapshot_checksum_and_fixture() -> None:
    plan = plan_mash_reference_workflow(repo_root=REPO_ROOT, fixture_mode=True)

    assert plan.mode == "fixture"
    assert plan.snapshot_id == "phase3_foundation_2026_04_20"
    assert plan.sketch_id.startswith("phase3_foundation_2026_04_20_")
    assert plan.output_path.exists()
    assert plan.output_path.name == "reference_smoke_fixture.msh"
    assert plan.command[:3] == ("mash", "sketch", "-o")
    assert plan.timeout_seconds == 600


def test_mash_live_reference_plan_uses_non_query_reference_fixture() -> None:
    artifact_root = REPO_ROOT / "custom_artifacts"
    plan = plan_mash_reference_workflow(
        repo_root=REPO_ROOT,
        fixture_mode=False,
        artifact_root=artifact_root,
    )

    assert plan.mode == "live"
    assert plan.reference_inputs == (REPO_ROOT / "data/fixtures/smoke/reference_ec_001.fasta",)
    assert REPO_ROOT / "data/fixtures/smoke/sample_001.fasta" not in plan.reference_inputs
    assert plan.output_path.is_relative_to(artifact_root)
    assert plan.timeout_seconds == 600
