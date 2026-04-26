from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from app.data import prepare_live_sample_input_from_row
from app.evidence import (
    build_mash_runtime_metadata,
    execute_mash_query_workflow,
    execute_mash_reference_workflow,
    inspect_mash_runtime,
    parse_mash_distance_output,
    plan_mash_query_workflow,
    plan_mash_reference_workflow,
    write_mash_runtime_metadata,
)
from app.integrations import build_integration_health_report
from app.settings import AppSettings, load_settings

REPO_ROOT = Path(__file__).resolve().parents[2]
LIVE_ASSEMBLY_ACCESSION = "GCF_000005845.2"


def _live_settings(tmp_path: Path) -> AppSettings:
    settings = load_settings()
    live_dataset_root = tmp_path / "live_data"
    artifact_root = tmp_path / "artifacts"
    integrations = replace(
        settings.integrations,
        dataset_root=live_dataset_root,
    )
    return replace(
        settings,
        artifact_root=artifact_root,
        data_root=live_dataset_root,
        integrations=integrations,
    )


def test_live_mash_runtime_health_reflects_actual_machine_state() -> None:
    settings = load_settings()
    runtime = inspect_mash_runtime(executable_override=settings.integrations.mash_bin)
    report = build_integration_health_report(settings)
    tool = report["tools"]["mash"]

    assert runtime.status in {"ready", "tool_missing", "version_unavailable"}
    assert tool["runtime_status"] == runtime.status
    assert tool["status"] == ("available" if runtime.status == "ready" else "missing")
    assert tool["version"] == runtime.version
    if runtime.status == "ready":
        assert runtime.version
    else:
        assert tool["notes"]


@pytest.mark.live
def test_live_mash_planning_uses_actual_runtime_state(tmp_path: Path) -> None:
    settings = _live_settings(tmp_path)
    preparation = prepare_live_sample_input_from_row(
        settings=settings,
        manifest_row={
            "record_id": "plan_ec_tetracycline_001",
            "record_kind": "public_downloaded",
            "sample_id": "sample_gcf_000005845_2",
            "organism": "e_coli",
            "target_drug": "tetracycline",
            "assembly_accession": LIVE_ASSEMBLY_ACCESSION,
            "biosample_accession": "",
            "retrieval_status": "ready",
            "retrieval_date": "2026-04-21",
            "label_source": "bv_brc_amr_metadata",
            "phenotype": "resistant",
            "source_context": "agricultural_surveillance_proxy",
            "inclusion_status": "included",
            "inclusion_reason": "live_public_bv_brc_ncbi_verified",
            "filtering_reason": "",
            "source_database": "bv_brc_ncbi_datasets",
            "source_record_id": "bv_brc_genome:test",
            "notes": "phase6b mash runtime test using live NCBI accession",
        },
    )
    runtime = inspect_mash_runtime(executable_override=settings.integrations.mash_bin)
    reference_plan = plan_mash_reference_workflow(
        repo_root=REPO_ROOT,
        fixture_mode=False,
        artifact_root=settings.artifact_root,
        executable_override=settings.integrations.mash_bin,
        runtime_info=runtime,
    )
    if runtime.status != "ready":
        assert reference_plan.mode == "unavailable"
        assert reference_plan.notes
        return

    query_plan = plan_mash_query_workflow(
        preparation.sample,
        reference_plan=reference_plan,
        output_dir=tmp_path / "evidence" / "mash",
        fixture_mode=False,
        allow_fixture_fallback=False,
        repo_root=REPO_ROOT,
        executable_override=settings.integrations.mash_bin,
        runtime_info=runtime,
    )

    assert query_plan.fixture_fallback_used is False
    assert reference_plan.output_path.is_relative_to(settings.artifact_root)
    assert reference_plan.timeout_seconds == 600
    assert query_plan.timeout_seconds == 600
    assert build_mash_runtime_metadata(reference_plan, query_plan, repo_root=REPO_ROOT)["mode"] == query_plan.mode

    if runtime.status == "ready":
        reference_sketch_path = execute_mash_reference_workflow(reference_plan)
        raw_output_path = execute_mash_query_workflow(query_plan)
        runtime_json_path = write_mash_runtime_metadata(reference_plan, query_plan, repo_root=REPO_ROOT)
        novelty = parse_mash_distance_output(
            raw_output_path,
            job_id="job_phase6b_mash_live",
            sample_id=preparation.sample.sample_id,
            target_drug=preparation.sample.target_drug,
            reference_snapshot_id=reference_plan.snapshot_id,
            query_input=query_plan.query_input,
        )

        assert reference_plan.mode == "live"
        assert query_plan.mode == "live"
        assert reference_sketch_path.exists()
        assert raw_output_path.exists()
        assert runtime_json_path.exists()
        assert novelty.reference_snapshot_id == reference_plan.snapshot_id
