from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from app.contracts import ArtifactKind, SampleInput
from app.data import load_manifest_rows, prepare_live_sample_input_from_row, run_live_manifest_retrieval
from app.evidence import (
    build_evidence_artifact_manifest,
    plan_amrfinderplus_execution,
    plan_mash_query_workflow,
    plan_mash_reference_workflow,
    validate_sample_for_evidence,
)
from app.settings import AppSettings, load_settings

REPO_ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.live


def _live_settings(tmp_path: Path) -> AppSettings:
    settings = load_settings()
    live_dataset_root = tmp_path / "live_data"
    integrations = replace(
        settings.integrations,
        dataset_root=live_dataset_root,
    )
    return replace(
        settings,
        data_root=live_dataset_root,
        integrations=integrations,
    )


def test_live_sample_preparation_supports_evidence_inputs(tmp_path: Path) -> None:
    settings = _live_settings(tmp_path)
    output_manifest = tmp_path / "live_accession_manifest.csv"
    sample_json = tmp_path / "live_sample_input.json"

    run_live_manifest_retrieval(
        settings=settings,
        seed_manifest_path=REPO_ROOT / "data/accessions/seed_accession_manifest.csv",
        output_manifest_path=output_manifest,
        record_ids={"plan_ec_tetracycline_001"},
    )
    resolved_row = next(
        row
        for row in load_manifest_rows(output_manifest)
        if row["record_id"] == "plan_ec_tetracycline_001"
    )

    preparation = prepare_live_sample_input_from_row(
        settings=settings,
        manifest_row=resolved_row,
        output_json_path=sample_json,
    )
    written_sample = SampleInput.model_validate_json(sample_json.read_text(encoding="utf-8"))
    extracted_fasta = Path(preparation.sample.fasta_path)

    assert sample_json.exists()
    assert preparation.sample == written_sample
    assert preparation.sample.metadata.accession == resolved_row["assembly_accession"]
    assert preparation.sample.metadata.provenance_source.value == "ncbi_datasets"
    assert extracted_fasta.is_absolute()
    assert extracted_fasta.exists()
    assert extracted_fasta.is_relative_to(settings.integrations.dataset_root)
    assert extracted_fasta.suffix.lower() == ".fna"
    assert preparation.extracted_fasta_byte_count > 0
    assert preparation.extracted_fasta_sha256

    qc = validate_sample_for_evidence(preparation.sample, repo_root=REPO_ROOT)
    assert qc.file_valid is True
    assert qc.sequence_count >= 1
    assert qc.total_bases > 0

    amrfinder_plan = plan_amrfinderplus_execution(
        preparation.sample,
        output_dir=tmp_path / "evidence" / "amrfinder",
        fixture_mode=False,
        allow_fixture_fallback=False,
        repo_root=REPO_ROOT,
    )
    assert Path(amrfinder_plan.command[2]) == preparation.extracted_fasta_path

    mash_reference_plan = plan_mash_reference_workflow(
        repo_root=REPO_ROOT,
        fixture_mode=True,
    )
    mash_query_plan = plan_mash_query_workflow(
        preparation.sample,
        reference_plan=mash_reference_plan,
        output_dir=tmp_path / "evidence" / "mash",
        fixture_mode=False,
        allow_fixture_fallback=False,
        repo_root=REPO_ROOT,
    )
    assert mash_query_plan.query_input == preparation.extracted_fasta_path

    artifact_root = tmp_path / "evidence" / "manifest"
    artifact_root.mkdir(parents=True, exist_ok=True)
    qc_json_path = artifact_root / "qc.json"
    mechanistic_json_path = artifact_root / "mechanistic_evidence.json"
    novelty_json_path = artifact_root / "novelty.json"
    amrfinder_raw_path = artifact_root / "sample.amrfinder.tsv"
    mash_raw_path = artifact_root / "sample.mash.dist.tsv"
    qc_json_path.write_text(json.dumps(qc.model_dump(mode="json"), indent=2), encoding="utf-8")
    mechanistic_json_path.write_text("[]", encoding="utf-8")
    novelty_json_path.write_text("{}", encoding="utf-8")
    amrfinder_raw_path.write_text("", encoding="utf-8")
    mash_raw_path.write_text("", encoding="utf-8")

    manifest = build_evidence_artifact_manifest(
        sample=preparation.sample,
        job_id="job_phase6b_live_fasta",
        artifact_root=artifact_root,
        qc_json_path=qc_json_path,
        amrfinder_raw_path=amrfinder_raw_path,
        mechanistic_json_path=mechanistic_json_path,
        mash_raw_path=mash_raw_path,
        novelty_json_path=novelty_json_path,
        repo_root=REPO_ROOT,
    )
    input_fasta_record = next(
        artifact for artifact in manifest.artifacts if artifact.kind == ArtifactKind.INPUT_FASTA
    )
    assert input_fasta_record.sha256 == preparation.extracted_fasta_sha256
    assert input_fasta_record.path.endswith(".fna")
