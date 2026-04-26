from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from app.data import prepare_live_sample_input_from_row
from app.evidence import (
    build_amrfinderplus_runtime_metadata,
    execute_amrfinderplus,
    inspect_amrfinderplus_runtime,
    normalize_amrfinderplus_output,
    plan_amrfinderplus_execution,
    write_amrfinderplus_runtime_metadata,
)
from app.integrations import build_integration_health_report
from app.settings import AppSettings, load_settings

REPO_ROOT = Path(__file__).resolve().parents[2]
LIVE_ASSEMBLY_ACCESSION = "GCF_000005845.2"


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


def test_live_amrfinder_runtime_health_reflects_actual_machine_state() -> None:
    settings = load_settings()
    runtime = inspect_amrfinderplus_runtime(
        executable_override=settings.integrations.amrfinderplus_bin,
        database_dir=settings.integrations.amrfinderplus_db,
    )
    report = build_integration_health_report(settings)
    tool = report["tools"]["amrfinderplus"]

    assert runtime.status in {"ready", "tool_missing", "database_missing", "database_unavailable"}
    assert tool["runtime_status"] == runtime.status
    assert tool["status"] == ("available" if runtime.status == "ready" else "missing")
    assert tool["version"] == runtime.version
    assert tool["database_version"] == runtime.database_version
    if runtime.status == "ready":
        assert runtime.version
        assert runtime.database_version
    else:
        assert tool["notes"]


@pytest.mark.live
def test_live_amrfinder_planning_uses_actual_runtime_state(tmp_path: Path) -> None:
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
            "notes": "phase6b amrfinder runtime test using live NCBI accession",
        },
    )
    runtime = inspect_amrfinderplus_runtime(
        executable_override=settings.integrations.amrfinderplus_bin,
        database_dir=settings.integrations.amrfinderplus_db,
    )
    output_dir = tmp_path / "evidence" / "amrfinder"
    plan = plan_amrfinderplus_execution(
        preparation.sample,
        output_dir=output_dir,
        fixture_mode=False,
        allow_fixture_fallback=False,
        repo_root=REPO_ROOT,
        executable_override=settings.integrations.amrfinderplus_bin,
        database_dir=settings.integrations.amrfinderplus_db,
        runtime_info=runtime,
    )

    assert plan.fixture_fallback_used is False
    assert plan.database_status == runtime.database_status
    assert build_amrfinderplus_runtime_metadata(plan, repo_root=REPO_ROOT)["status"] == plan.status

    if runtime.status == "ready":
        raw_output_path = execute_amrfinderplus(plan)
        runtime_json_path = write_amrfinderplus_runtime_metadata(plan, repo_root=REPO_ROOT)
        metadata = json.loads(runtime_json_path.read_text(encoding="utf-8"))
        rows = normalize_amrfinderplus_output(
            raw_output_path,
            job_id="job_phase6b_amrfinder_live",
            sample_id=preparation.sample.sample_id,
            target_drug=preparation.sample.target_drug,
            raw_artifact_id="job_phase6b_amrfinder_live_amrfinder_raw",
        )

        assert plan.mode == "live"
        assert raw_output_path.exists()
        assert runtime_json_path.exists()
        assert metadata["version"] == runtime.version
        assert metadata["database_version"] == runtime.database_version
        assert isinstance(rows, list)
    else:
        assert plan.mode == "unavailable"
        assert plan.status == runtime.status
        assert "fixture" not in plan.message.lower()
