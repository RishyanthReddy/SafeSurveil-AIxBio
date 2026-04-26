from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.contracts import ArtifactManifest, MechanisticEvidence, NoveltyAssessment, SampleInput
from app.settings import AppSettings

from .amrfinder import (
    inspect_amrfinderplus_runtime,
    execute_amrfinderplus,
    write_amrfinderplus_runtime_metadata,
    normalize_amrfinderplus_output,
    plan_amrfinderplus_execution,
)
from .failures import (
    EvidenceFailure,
    build_fixture_fallback_failure,
    build_tool_missing_failure,
    classify_validation_failure,
)
from .manifest import build_evidence_artifact_manifest
from .mash import (
    execute_mash_query_workflow,
    execute_mash_reference_workflow,
    inspect_mash_runtime,
    parse_mash_distance_output,
    plan_mash_query_workflow,
    plan_mash_reference_workflow,
    write_mash_runtime_metadata,
)
from .validation import validate_sample_for_evidence


@dataclass(frozen=True)
class EvidenceSmokeResult:
    sample_id: str
    job_id: str
    mode: str
    output_dir: Path
    qc_path: Path
    mechanistic_json_path: Path
    novelty_json_path: Path
    manifest_path: Path
    mechanistic_evidence: tuple[MechanisticEvidence, ...]
    novelty_assessment: NoveltyAssessment
    artifact_manifest: ArtifactManifest
    failures: tuple[EvidenceFailure, ...]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def run_evidence_smoke(
    sample: SampleInput,
    *,
    output_dir: Path,
    fixture_mode: bool = True,
    repo_root: Path | None = None,
    job_id: str = "job_smoke_001",
    settings: AppSettings | None = None,
) -> EvidenceSmokeResult:
    root = repo_root or _repo_root()
    resolved_output_dir = Path(output_dir)
    resolved_output_dir.mkdir(parents=True, exist_ok=True)
    qc = validate_sample_for_evidence(sample, repo_root=root, job_id=job_id)
    validation_failure = classify_validation_failure(qc)
    if validation_failure is not None:
        raise ValueError(validation_failure.detail)

    qc_path = resolved_output_dir / "qc.json"
    _write_json(qc_path, qc.model_dump(mode="json"))

    failures: list[EvidenceFailure] = []

    amrfinder_runtime_json_path: Path | None = None
    amrfinder_runtime = inspect_amrfinderplus_runtime(
        executable_override=(settings.integrations.amrfinderplus_bin if settings else None),
        database_dir=(settings.integrations.amrfinderplus_db if settings else None),
    )
    amrfinder_plan = plan_amrfinderplus_execution(
        sample,
        output_dir=resolved_output_dir,
        fixture_mode=fixture_mode,
        allow_fixture_fallback=fixture_mode,
        repo_root=root,
        executable_override=(settings.integrations.amrfinderplus_bin if settings else None),
        database_dir=(settings.integrations.amrfinderplus_db if settings else None),
        runtime_info=amrfinder_runtime,
    )
    if amrfinder_plan.mode == "unavailable":
        failures.append(build_tool_missing_failure("amrfinder", "mechanistic_evidence"))
        raise ValueError(amrfinder_plan.message)
    if amrfinder_plan.fixture_fallback_used and not fixture_mode:
        failures.append(build_fixture_fallback_failure("mechanistic_evidence", amrfinder_plan.message))
    if amrfinder_plan.mode == "live":
        execute_amrfinderplus(amrfinder_plan)
        amrfinder_runtime_json_path = write_amrfinderplus_runtime_metadata(
            amrfinder_plan,
            repo_root=root,
        )

    mechanistic_rows = normalize_amrfinderplus_output(
        amrfinder_plan.raw_output_path,
        job_id=job_id,
        sample_id=sample.sample_id,
        target_drug=sample.target_drug,
        raw_artifact_id=f"{job_id}_amrfinder_raw",
    )
    mechanistic_json_path = resolved_output_dir / "mechanistic_evidence.json"
    _write_json(
        mechanistic_json_path,
        [row.model_dump(mode="json") for row in mechanistic_rows],
    )

    mash_runtime_json_path: Path | None = None
    mash_runtime = inspect_mash_runtime(
        executable_override=(settings.integrations.mash_bin if settings else None),
    )
    reference_plan = plan_mash_reference_workflow(
        repo_root=root,
        fixture_mode=fixture_mode,
        artifact_root=(settings.artifact_root if settings else None),
        executable_override=(settings.integrations.mash_bin if settings else None),
        runtime_info=mash_runtime,
    )
    if reference_plan.mode == "unavailable":
        failures.append(build_tool_missing_failure("mash", "novelty"))
        raise ValueError(" ".join(reference_plan.notes))
    query_plan = plan_mash_query_workflow(
        sample,
        reference_plan=reference_plan,
        output_dir=resolved_output_dir,
        fixture_mode=fixture_mode,
        allow_fixture_fallback=fixture_mode,
        repo_root=root,
        executable_override=(settings.integrations.mash_bin if settings else None),
        runtime_info=mash_runtime,
    )
    if query_plan.mode == "unavailable":
        failures.append(build_tool_missing_failure("mash", "novelty"))
        raise ValueError(query_plan.message)
    if query_plan.fixture_fallback_used and not fixture_mode:
        failures.append(build_fixture_fallback_failure("novelty", query_plan.message))
    if reference_plan.mode == "live":
        execute_mash_reference_workflow(reference_plan)
    if query_plan.mode == "live":
        execute_mash_query_workflow(query_plan)
        mash_runtime_json_path = write_mash_runtime_metadata(
            reference_plan,
            query_plan,
            repo_root=root,
        )

    novelty = parse_mash_distance_output(
        query_plan.raw_output_path,
        job_id=job_id,
        sample_id=sample.sample_id,
        target_drug=sample.target_drug,
        reference_snapshot_id=reference_plan.snapshot_id,
        query_input=query_plan.query_input,
    )
    novelty_json_path = resolved_output_dir / "novelty.json"
    _write_json(novelty_json_path, novelty.model_dump(mode="json"))

    artifact_manifest = build_evidence_artifact_manifest(
        sample=sample,
        job_id=job_id,
        artifact_root=resolved_output_dir,
        qc_json_path=qc_path,
        amrfinder_raw_path=amrfinder_plan.raw_output_path,
        amrfinder_runtime_json_path=amrfinder_runtime_json_path,
        mechanistic_json_path=mechanistic_json_path,
        mash_raw_path=query_plan.raw_output_path,
        mash_runtime_json_path=mash_runtime_json_path,
        novelty_json_path=novelty_json_path,
        repo_root=root,
    )
    manifest_path = resolved_output_dir / "artifact_manifest.json"
    _write_json(manifest_path, artifact_manifest.model_dump(mode="json"))

    return EvidenceSmokeResult(
        sample_id=sample.sample_id,
        job_id=job_id,
        mode="fixture" if fixture_mode else "live_or_fallback",
        output_dir=resolved_output_dir,
        qc_path=qc_path,
        mechanistic_json_path=mechanistic_json_path,
        novelty_json_path=novelty_json_path,
        manifest_path=manifest_path,
        mechanistic_evidence=tuple(mechanistic_rows),
        novelty_assessment=novelty,
        artifact_manifest=artifact_manifest,
        failures=tuple(failures),
    )
