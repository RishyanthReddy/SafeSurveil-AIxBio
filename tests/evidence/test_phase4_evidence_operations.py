from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.contracts import ArtifactKind, NoveltyBucket, PredictedPhenotype, SampleInput
from app.evidence import (
    EvidenceFailureCode,
    build_fixture_fallback_failure,
    build_tool_missing_failure,
    build_evidence_artifact_manifest,
    parse_mash_distance_output,
    plan_mash_query_workflow,
    plan_mash_reference_workflow,
    run_evidence_smoke,
)
from app.evidence import amrfinder as amrfinder_module
from app.evidence import mash as mash_module

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


def test_mash_query_fixture_plan_uses_committed_distance_output() -> None:
    reference_plan = plan_mash_reference_workflow(repo_root=REPO_ROOT, fixture_mode=True)
    query_plan = plan_mash_query_workflow(
        load_smoke_sample(),
        reference_plan=reference_plan,
        output_dir=REPO_ROOT / "artifacts/runs/mash",
        fixture_mode=True,
        repo_root=REPO_ROOT,
    )

    assert query_plan.mode == "fixture"
    assert query_plan.raw_output_path.exists()
    assert query_plan.command[:2] == ("mash", "dist")


def test_parse_mash_distance_output_returns_novelty_contract() -> None:
    novelty = parse_mash_distance_output(
        REPO_ROOT / "data/fixtures/smoke/sample_001.mash.dist.tsv",
        job_id="job_smoke_001",
        sample_id="sample_001",
        target_drug="tetracycline",
        reference_snapshot_id="phase3_foundation_2026_04_20",
    )

    assert novelty.nearest_neighbor_id == "ref_ec_001"
    assert novelty.target_drug == "tetracycline"
    assert novelty.nearest_neighbor_distance == 0.034
    assert novelty.novelty_bucket == NoveltyBucket.ELEVATED
    assert novelty.reference_snapshot_id == "phase3_foundation_2026_04_20"


def test_parse_mash_distance_output_accepts_headerless_live_rows(tmp_path: Path) -> None:
    live_output = tmp_path / "sample_001.live.mash.dist.tsv"
    live_output.write_text(
        "\n".join(
            [
                "ref_ec_001\tsample_001\t0.034\t0\t842/1000",
                "ref_ec_002\tsample_001\t0.061\t0\t701/1000",
                "ref_ec_003\tsample_001\t0.087\t0\t522/1000",
            ]
        ),
        encoding="utf-8",
    )

    novelty = parse_mash_distance_output(
        live_output,
        job_id="job_smoke_001",
        sample_id="sample_001",
        target_drug="tetracycline",
        reference_snapshot_id="phase3_foundation_2026_04_20",
    )

    assert novelty.nearest_neighbor_id == "ref_ec_001"
    assert novelty.nearest_neighbor_distance == 0.034
    assert novelty.novelty_bucket == NoveltyBucket.ELEVATED


def test_parse_mash_distance_output_sanitizes_path_like_live_ids(tmp_path: Path) -> None:
    live_output = tmp_path / "sample_001.path_ids.mash.dist.tsv"
    live_output.write_text(
        "\n".join(
            [
                "data/fixtures/smoke/reference_ec_001.fasta\tdata/fixtures/smoke/sample_001.fasta\t0.034\t0\t842/1000",
                "C:\\refs\\reference_ec_002.fasta\tdata/fixtures/smoke/sample_001.fasta\t0.061\t0\t701/1000",
                "reference_ec_003.fasta\tdata/fixtures/smoke/sample_001.fasta\t0.087\t0\t522/1000",
            ]
        ),
        encoding="utf-8",
    )

    novelty = parse_mash_distance_output(
        live_output,
        job_id="job_smoke_001",
        sample_id="sample_001",
        target_drug="tetracycline",
        reference_snapshot_id="phase3_foundation_2026_04_20",
    )

    assert novelty.nearest_neighbor_id == "reference_ec_001"
    assert novelty.nearest_neighbor_distance == 0.034
    assert novelty.novelty_bucket == NoveltyBucket.ELEVATED


def test_parse_mash_distance_output_accepts_planned_query_path(tmp_path: Path) -> None:
    live_output = tmp_path / "sample_001.upload_name.mash.dist.tsv"
    live_output.write_text(
        "\n".join(
            [
                "ref_ec_001\tdata/uploads/input.fasta\t0.034\t0\t842/1000",
                "ref_ec_002\tdata/uploads/input.fasta\t0.061\t0\t701/1000",
                "ref_ec_003\tdata/uploads/input.fasta\t0.087\t0\t522/1000",
            ]
        ),
        encoding="utf-8",
    )

    novelty = parse_mash_distance_output(
        live_output,
        job_id="job_smoke_001",
        sample_id="sample_001",
        target_drug="tetracycline",
        reference_snapshot_id="phase3_foundation_2026_04_20",
        query_input=Path("data/uploads/input.fasta"),
    )

    assert novelty.sample_id == "sample_001"
    assert novelty.nearest_neighbor_id == "ref_ec_001"
    assert novelty.nearest_neighbor_distance == 0.034


def test_parse_mash_distance_output_rejects_wrong_query_sample(tmp_path: Path) -> None:
    live_output = tmp_path / "sample_001.stale.mash.dist.tsv"
    live_output.write_text(
        "ref_ec_001\tother_sample_001\t0.034\t0\t842/1000\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="does not match sample_id"):
        parse_mash_distance_output(
            live_output,
            job_id="job_smoke_001",
            sample_id="sample_001",
            target_drug="tetracycline",
            reference_snapshot_id="phase3_foundation_2026_04_20",
        )


def test_normalize_amrfinderplus_output_accepts_live_headers(tmp_path: Path) -> None:
    live_output = tmp_path / "sample_001.live.amrfinder.tsv"
    live_output.write_text(
        "\t".join(
            [
                "Protein identifier",
                "Contig id",
                "Start",
                "Stop",
                "Strand",
                "Gene symbol",
                "Sequence name",
                "Scope",
                "Element type",
                "Element subtype",
                "Class",
                "Subclass",
                "Method",
                "Target length",
                "Reference sequence length",
                "% Coverage of reference sequence",
                "% Identity to reference sequence",
                "Alignment length",
                "Accession of closest sequence",
                "Name of closest sequence",
                "HMM id",
                "HMM description",
            ]
        )
        + "\n"
        + "\t".join(
            [
                "prot_001",
                "sample_001_contig_1",
                "12",
                "98",
                "+",
                "tetA",
                "Tetracycline efflux MFS transporter",
                "plus",
                "AMR",
                "AMR",
                "Efflux pump",
                "Tetracycline",
                "BLAST",
                "399",
                "399",
                "100.0",
                "99.2",
                "399",
                "WP_000001",
                "TetA reference",
                "NA",
                "NA",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    rows = amrfinder_module.normalize_amrfinderplus_output(
        live_output,
        job_id="job_smoke_001",
        sample_id="sample_001",
        target_drug="tetracycline",
        raw_artifact_id="job_smoke_001_amrfinder_raw",
    )

    assert len(rows) == 1
    assert rows[0].gene_symbol == "tetA"
    assert rows[0].target_drug == "tetracycline"
    assert rows[0].support_level.value == "supported"
    assert rows[0].drug_association == ["tetracycline"]


def test_build_evidence_artifact_manifest_links_expected_files(tmp_path: Path) -> None:
    sample = load_smoke_sample()
    qc_path = tmp_path / "qc.json"
    mechanistic_path = tmp_path / "mechanistic_evidence.json"
    novelty_path = tmp_path / "novelty.json"
    qc_path.write_text("{}", encoding="utf-8")
    mechanistic_path.write_text("[]", encoding="utf-8")
    novelty_path.write_text("{}", encoding="utf-8")

    manifest = build_evidence_artifact_manifest(
        sample=sample,
        job_id="job_smoke_001",
        artifact_root=tmp_path,
        qc_json_path=qc_path,
        amrfinder_raw_path=REPO_ROOT / "data/fixtures/smoke/sample_001.amrfinder.tsv",
        mechanistic_json_path=mechanistic_path,
        mash_raw_path=REPO_ROOT / "data/fixtures/smoke/sample_001.mash.dist.tsv",
        novelty_json_path=novelty_path,
        repo_root=REPO_ROOT,
    )

    assert manifest.artifact_root == tmp_path.resolve().as_posix()
    assert manifest.target_drug == sample.target_drug
    assert len(manifest.artifacts) == 6
    assert manifest.artifacts[0].kind == ArtifactKind.INPUT_FASTA
    assert all(record.target_drug == sample.target_drug for record in manifest.artifacts)
    assert any(
        Path(record.path).is_absolute()
        for record in manifest.artifacts
        if record.path.endswith(".json")
    )
    assert any(record.path.startswith("data/fixtures/") for record in manifest.artifacts)
    assert any(record.kind == ArtifactKind.NOVELTY_SUMMARY for record in manifest.artifacts)


def test_failure_helpers_emit_structured_codes() -> None:
    tool_missing = build_tool_missing_failure("mash", "novelty")
    fixture_fallback = build_fixture_fallback_failure("mechanistic_evidence", "fixture mode used")

    assert tool_missing.code == EvidenceFailureCode.TOOL_MISSING
    assert tool_missing.retryable is True
    assert fixture_fallback.code == EvidenceFailureCode.FIXTURE_FALLBACK


def test_run_evidence_smoke_writes_schema_valid_outputs(tmp_path: Path) -> None:
    result = run_evidence_smoke(
        load_smoke_sample(),
        output_dir=tmp_path,
        fixture_mode=True,
        repo_root=REPO_ROOT,
        job_id="job_smoke_001",
    )

    assert result.qc_path.exists()
    assert result.mechanistic_json_path.exists()
    assert result.novelty_json_path.exists()
    assert result.manifest_path.exists()
    assert len(result.mechanistic_evidence) == 2
    assert result.novelty_assessment.novelty_bucket == NoveltyBucket.ELEVATED
    assert result.failures == ()


def test_run_evidence_smoke_live_rejects_missing_amrfinder_without_fixture_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(amrfinder_module, "which", lambda _: None)
    monkeypatch.setattr(mash_module, "which", lambda _: None)

    with pytest.raises(ValueError, match="AMRFinderPlus executable was not found"):
        run_evidence_smoke(
            load_smoke_sample(),
            output_dir=tmp_path,
            fixture_mode=False,
            repo_root=REPO_ROOT,
            job_id="job_smoke_live_fallback",
        )


def test_run_evidence_smoke_live_executes_tool_outputs_when_available(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture_amrfinder = (REPO_ROOT / "data/fixtures/smoke/sample_001.amrfinder.tsv").read_text(encoding="utf-8")
    live_mash_output = "\n".join(
        [
            "ref_ec_001\tsample_001\t0.034\t0\t842/1000",
            "ref_ec_002\tsample_001\t0.061\t0\t701/1000",
            "ref_ec_003\tsample_001\t0.087\t0\t522/1000",
        ]
    )

    class CompletedProcess:
        def __init__(self, stdout: str = "", *, stderr: str = "", returncode: int = 0) -> None:
            self.stdout = stdout
            self.stderr = stderr
            self.returncode = returncode

    def fake_run(
        command: tuple[str, ...],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
        timeout: int | None = None,
    ) -> CompletedProcess:
        assert capture_output is True
        assert text is True
        tool_name = command[0]
        tool_basename = Path(tool_name).name
        if tool_basename == "amrfinder" and command[1] == "--version":
            return CompletedProcess(stdout="AMRFinderPlus 4.2.7")
        if tool_basename == "amrfinder" and command[1] == "--database_version":
            return CompletedProcess(stdout="2026-01-22.1")
        if tool_basename == "amrfinder":
            output_index = command.index("-o") + 1
            Path(command[output_index]).write_text(fixture_amrfinder, encoding="utf-8")
            return CompletedProcess()
        if tool_basename == "mash" and command[1] == "--version":
            return CompletedProcess(stdout="Mash version 2.3")
        if tool_basename == "mash" and command[1] == "sketch":
            assert timeout == 600
            output_prefix = Path(command[3])
            output_prefix.with_suffix(".msh").write_text("fixture_mash_sketch_placeholder\n", encoding="utf-8")
            return CompletedProcess()
        if tool_basename == "mash" and command[1] == "dist":
            assert timeout == 600
            return CompletedProcess(stdout=live_mash_output)
        raise AssertionError(f"Unexpected command: {command}")

    monkeypatch.setattr(amrfinder_module, "which", lambda _: "C:/fake/amrfinder")
    monkeypatch.setattr(mash_module, "which", lambda _: "C:/fake/mash")
    monkeypatch.setattr(amrfinder_module.subprocess, "run", fake_run)
    monkeypatch.setattr(mash_module.subprocess, "run", fake_run)

    result = run_evidence_smoke(
        load_smoke_sample(),
        output_dir=tmp_path,
        fixture_mode=False,
        repo_root=REPO_ROOT,
        job_id="job_smoke_live_exec",
    )

    assert result.failures == ()
    assert result.output_dir.joinpath("sample_001.amrfinder.tsv").exists()
    assert result.output_dir.joinpath("sample_001.mash.dist.tsv").exists()
    assert result.novelty_assessment.novelty_bucket == NoveltyBucket.ELEVATED


