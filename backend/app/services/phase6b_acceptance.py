from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import json
from pathlib import Path
import subprocess
from typing import Any

from app.data import load_manifest_rows, prepare_live_sample_input_from_row, run_live_manifest_retrieval
from app.evidence import (
    execute_amrfinderplus,
    execute_mash_query_workflow,
    execute_mash_reference_workflow,
    inspect_amrfinderplus_runtime,
    inspect_mash_runtime,
    normalize_amrfinderplus_output,
    parse_mash_distance_output,
    plan_amrfinderplus_execution,
    plan_mash_query_workflow,
    plan_mash_reference_workflow,
    write_amrfinderplus_runtime_metadata,
    write_mash_runtime_metadata,
)
from app.integrations import (
    BVBRCClient,
    NCBIDatasetsClient,
    PathogenDetectionClient,
    build_integration_health_report,
)
from app.settings import AppSettings
from app.storage import SQLitePersistence

from .orchestration import AnalysisService

PHASE6B_ACCEPTANCE_MATRIX_VERSION = "0.1.0"
PHASE6B_SMOKE_RECORD_ID = "plan_ec_tetracycline_001"
PHASE6B_SMOKE_ASSEMBLY_ACCESSION = "GCF_000005845.2"
PHASE6B_SMOKE_ORGANISM_GROUP = "Escherichia_coli_Shigella"
PHASE6B_PATHOGEN_DETECTION_CHUNK_BYTES = 4_000_000
PHASE6B_BV_BRC_PAGE_SIZE = 50
PHASE6B_AREA_NAMES = (
    "secrets",
    "ncbi_datasets",
    "bv_brc",
    "pathogen_detection",
    "retrieval",
    "live_sample_input",
    "amrfinderplus",
    "mash",
    "persistence",
)


def build_phase6b_acceptance_report(
    *,
    settings: AppSettings,
    work_root: Path,
    output_path: Path | None = None,
) -> dict[str, Any]:
    work_root = Path(work_root).resolve()
    work_root.mkdir(parents=True, exist_ok=True)
    live_settings = _build_live_work_settings(settings, work_root=work_root)
    health_report = build_integration_health_report(settings)

    areas: list[dict[str, Any]] = []

    areas.append(_evaluate_secret_hygiene(settings=settings, health_report=health_report))

    ncbi_probe, ncbi_error = _probe_ncbi_datasets(live_settings)
    areas.append(ncbi_probe)

    bv_probe, bv_error = _probe_bv_brc(live_settings)
    areas.append(bv_probe)

    retrieval_area: dict[str, Any]
    resolved_row: dict[str, str] | None = None
    retrieval_manifest_path = work_root / "live_accession_manifest.csv"
    retrieval_summary_path = work_root / "live_retrieval_summary.json"
    if ncbi_error is None and bv_error is None:
        retrieval_area, resolved_row = _probe_live_retrieval(
            settings=live_settings,
            output_manifest_path=retrieval_manifest_path,
            summary_output_path=retrieval_summary_path,
        )
    else:
        retrieval_area = _build_area(
            "retrieval",
            "blocked",
            "Live retrieval was blocked because prerequisite NCBI or BV-BRC probes failed.",
            evidence=[
                f"ncbi_datasets_status={ncbi_probe['status']}",
                f"bv_brc_status={bv_probe['status']}",
            ],
        )
    areas.append(retrieval_area)

    pathogen_area = _probe_pathogen_detection(
        live_settings,
        assembly_accession=(
            resolved_row["assembly_accession"]
            if resolved_row is not None and resolved_row.get("assembly_accession")
            else PHASE6B_SMOKE_ASSEMBLY_ACCESSION
        ),
    )
    areas.append(pathogen_area)

    live_sample_area: dict[str, Any]
    preparation = None
    sample_json_path = work_root / "live_sample_input.json"
    if resolved_row is not None:
        live_sample_area, preparation = _probe_live_sample_input(
            settings=live_settings,
            manifest_row=resolved_row,
            output_json_path=sample_json_path,
        )
    else:
        live_sample_area = _build_area(
            "live_sample_input",
            "blocked",
            "Live sample preparation was blocked because retrieval did not produce a ready manifest row.",
            evidence=[f"retrieval_status={retrieval_area['status']}"],
        )
    areas.append(live_sample_area)

    amrfinder_area: dict[str, Any]
    if preparation is not None:
        amrfinder_area = _probe_amrfinderplus(
            settings=live_settings,
            sample=preparation.sample,
            output_dir=work_root / "evidence" / "amrfinder",
        )
    else:
        amrfinder_area = _build_area(
            "amrfinderplus",
            "blocked",
            "AMRFinderPlus live execution was blocked because no live FASTA sample was prepared.",
            evidence=[f"live_sample_input_status={live_sample_area['status']}"],
        )
    areas.append(amrfinder_area)

    mash_area: dict[str, Any]
    if preparation is not None:
        mash_area = _probe_mash(
            settings=live_settings,
            sample=preparation.sample,
            output_dir=work_root / "evidence" / "mash",
        )
    else:
        mash_area = _build_area(
            "mash",
            "blocked",
            "Mash live novelty was blocked because no live FASTA sample was prepared.",
            evidence=[f"live_sample_input_status={live_sample_area['status']}"],
        )
    areas.append(mash_area)

    persistence_area = _probe_persistence_round_trip(
        settings=live_settings,
        sample=preparation.sample if preparation is not None else None,
        prerequisites={
            "fixture_mode": "fixture" if settings.use_fixtures else "live",
            "live_sample_input": live_sample_area["status"],
            "amrfinderplus": amrfinder_area["status"],
            "mash": mash_area["status"],
        },
    )
    areas.append(persistence_area)

    report = {
        "matrix_version": PHASE6B_ACCEPTANCE_MATRIX_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "mode": "fixture" if settings.use_fixtures else "live",
        "integration_health": health_report,
        "areas": areas,
        "phase7_gate": _build_phase7_gate(settings=settings, areas=areas),
    }
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def _build_live_work_settings(settings: AppSettings, *, work_root: Path) -> AppSettings:
    live_data_root = work_root / "live_data"
    artifact_root = work_root / "artifacts"
    sqlite_db_path = work_root / "phase6b_acceptance.sqlite"
    integrations = replace(settings.integrations, dataset_root=live_data_root)
    return replace(
        settings,
        artifact_root=artifact_root,
        sqlite_db_path=sqlite_db_path,
        data_root=live_data_root,
        integrations=integrations,
    )


def _evaluate_secret_hygiene(
    *,
    settings: AppSettings,
    health_report: dict[str, Any],
) -> dict[str, Any]:
    tracked_secret_paths: list[str] = []
    repo_root = settings.repo_root
    env_path = repo_root / ".env"
    if env_path.exists() and _is_tracked_in_git(env_path, repo_root=repo_root):
        tracked_secret_paths.append(".env")

    token_path = settings.integrations.bv_brc_token_path
    if _is_within_repo(token_path, repo_root=repo_root) and _is_tracked_in_git(
        token_path, repo_root=repo_root
    ):
        tracked_secret_paths.append(_display_path(token_path, repo_root=repo_root))

    if tracked_secret_paths:
        return _build_area(
            "secrets",
            "degraded",
            "Secret-bearing local files are tracked, so Phase 6B cannot claim safe live integration handling.",
            evidence=[f"tracked_secret_paths={', '.join(tracked_secret_paths)}"],
        )

    if not health_report["secrets"]["redacted"] or health_report["secrets"]["values_exposed"]:
        return _build_area(
            "secrets",
            "degraded",
            "Integration diagnostics exposed raw secret state instead of staying redacted.",
            evidence=[f"secrets_report={json.dumps(health_report['secrets'], sort_keys=True)}"],
        )

    evidence = [
        f"dotenv_present={env_path.exists()}",
        f"token_path={_display_path(token_path, repo_root=repo_root)}",
        "health_report_redacted=true",
    ]
    return _build_area(
        "secrets",
        "live",
        "Secret hygiene is intact: local secret files remain untracked and live diagnostics stay redacted.",
        evidence=evidence,
    )


def _probe_ncbi_datasets(settings: AppSettings) -> tuple[dict[str, Any], Exception | None]:
    try:
        client = NCBIDatasetsClient.from_settings(settings)
        report = client.fetch_assembly_report([PHASE6B_SMOKE_ASSEMBLY_ACCESSION])
        if not report.reports:
            raise ValueError("NCBI Datasets returned no assembly reports for the smoke accession.")
        row = report.reports[0]
        return (
            _build_area(
                "ncbi_datasets",
                "live",
                "NCBI Datasets returned a live assembly report for the smoke accession.",
                evidence=[
                    f"assembly_accession={row.get('accession')}",
                    f"organism_name={row.get('organism', {}).get('organism_name', 'unknown')}",
                    f"report_count={len(report.reports)}",
                ],
            ),
            None,
        )
    except Exception as exc:
        return (
            _build_area(
                "ncbi_datasets",
                "unavailable",
                "NCBI Datasets live metadata retrieval failed.",
                evidence=[f"error={type(exc).__name__}: {exc}"],
            ),
            exc,
        )


def _probe_bv_brc(settings: AppSettings) -> tuple[dict[str, Any], Exception | None]:
    try:
        client = BVBRCClient.from_settings(settings)
        selected_row = None
        genome = None
        offset = 0
        while True:
            amr_rows = client.query_amr_by_taxon_and_antibiotic(
                taxon_id=562,
                antibiotic="tetracycline",
                limit=PHASE6B_BV_BRC_PAGE_SIZE,
                offset=offset,
            )
            if not amr_rows:
                break
            for candidate in amr_rows:
                candidate_genome = client.query_genome_by_id(candidate.genome_id)
                if candidate_genome is None or not candidate_genome.assembly_accession:
                    continue
                selected_row = candidate
                genome = candidate_genome
                break
            if selected_row is not None:
                break
            if len(amr_rows) < PHASE6B_BV_BRC_PAGE_SIZE:
                break
            offset += len(amr_rows)
        if selected_row is None and genome is None and offset == 0:
            raise ValueError("BV-BRC returned no genome_amr rows for the smoke organism/drug pair.")
        if selected_row is None or genome is None:
            raise ValueError(
                "BV-BRC did not return any genome metadata with an assembly accession for the smoke query."
            )
        return (
            _build_area(
                "bv_brc",
                "live",
                "BV-BRC authentication and Data API queries returned live phenotype and genome metadata.",
                evidence=[
                    f"genome_id={selected_row.genome_id}",
                    f"phenotype={selected_row.resistant_phenotype or 'unknown'}",
                    f"assembly_accession={genome.assembly_accession or 'missing'}",
                ],
            ),
            None,
        )
    except Exception as exc:
        return (
            _build_area(
                "bv_brc",
                "unavailable",
                "BV-BRC live phenotype or genome retrieval failed.",
                evidence=[f"error={type(exc).__name__}: {exc}"],
            ),
            exc,
        )


def _probe_pathogen_detection(
    settings: AppSettings,
    *,
    assembly_accession: str,
) -> dict[str, Any]:
    try:
        client = PathogenDetectionClient.from_settings(settings)
        lookup = client.lookup_record_by_assembly_accession(
            assembly_accession,
            organism_group=PHASE6B_SMOKE_ORGANISM_GROUP,
            chunk_size_bytes=PHASE6B_PATHOGEN_DETECTION_CHUNK_BYTES,
            strategy="stream",
        )
        record = lookup.record
        if record is None:
            if lookup.scan_complete:
                return _build_area(
                    "pathogen_detection",
                    "live",
                    "Pathogen Detection endpoint was reachable and the smoke assembly accession lookup completed with an explicit no-record result.",
                    evidence=[
                        f"requested_assembly_accession={assembly_accession}",
                        f"organism_group={PHASE6B_SMOKE_ORGANISM_GROUP}",
                        "lookup_strategy=stream",
                        "record_found=false",
                        f"source_url={lookup.source_url}",
                    ],
                )
            return _build_area(
                "pathogen_detection",
                "degraded",
                "Pathogen Detection endpoint was reachable, but the bounded metadata scan limit was reached before the smoke assembly accession could be confirmed.",
                evidence=[
                    f"requested_assembly_accession={assembly_accession}",
                    f"organism_group={PHASE6B_SMOKE_ORGANISM_GROUP}",
                    "lookup_strategy=stream",
                    f"bytes_scanned={lookup.bytes_scanned}",
                    f"max_scan_bytes={lookup.max_scan_bytes}",
                    f"source_url={lookup.source_url}",
                ],
            )
        return _build_area(
            "pathogen_detection",
            "live",
            "Pathogen Detection smoke assembly accession lookup succeeded against the live configured endpoint.",
            evidence=[
                f"requested_assembly_accession={assembly_accession}",
                f"organism_group={PHASE6B_SMOKE_ORGANISM_GROUP}",
                "lookup_strategy=stream",
                f"verified_assembly_accession={record.asm_acc or assembly_accession}",
                f"biosample_accession={record.biosample_acc or 'missing'}",
                f"source_url={record.source_url}",
            ],
        )
    except Exception as exc:
        return _build_area(
            "pathogen_detection",
            "unavailable",
            "Pathogen Detection live enrichment lookup failed.",
            evidence=[f"error={type(exc).__name__}: {exc}"],
        )


def _probe_live_retrieval(
    *,
    settings: AppSettings,
    output_manifest_path: Path,
    summary_output_path: Path,
) -> tuple[dict[str, Any], dict[str, str] | None]:
    try:
        summary = run_live_manifest_retrieval(
            settings=settings,
            seed_manifest_path=settings.repo_root / "data/accessions/seed_accession_manifest.csv",
            output_manifest_path=output_manifest_path,
            summary_output_path=summary_output_path,
            record_ids={PHASE6B_SMOKE_RECORD_ID},
        )
        rows = load_manifest_rows(output_manifest_path)
        resolved_row = next(
            row for row in rows if row["record_id"] == PHASE6B_SMOKE_RECORD_ID
        )
        if resolved_row["retrieval_status"] != "ready":
            raise ValueError(
                f"Live retrieval ended with retrieval_status={resolved_row['retrieval_status']}."
            )
        return (
            _build_area(
                "retrieval",
                "live",
                "The planned public smoke row resolved into a live, ready public record.",
                evidence=[
                    f"record_kind={resolved_row['record_kind']}",
                    f"assembly_accession={resolved_row['assembly_accession']}",
                    f"biosample_accession={resolved_row['biosample_accession'] or 'missing'}",
                    f"resolved_rows={summary['row_counts']['resolved_rows']}",
                ],
            ),
            resolved_row,
        )
    except Exception as exc:
        return (
            _build_area(
                "retrieval",
                "unavailable",
                "Live retrieval could not resolve the planned public smoke row.",
                evidence=[f"error={type(exc).__name__}: {exc}"],
            ),
            None,
        )


def _probe_live_sample_input(
    *,
    settings: AppSettings,
    manifest_row: dict[str, str],
    output_json_path: Path,
) -> tuple[dict[str, Any], Any | None]:
    try:
        preparation = prepare_live_sample_input_from_row(
            settings=settings,
            manifest_row=manifest_row,
            output_json_path=output_json_path,
        )
        return (
            _build_area(
                "live_sample_input",
                "live",
                "Live FASTA download and SampleInput preparation succeeded for the resolved public record.",
                evidence=[
                    f"sample_id={preparation.sample.sample_id}",
                    f"fasta_path={preparation.sample.fasta_path}",
                    f"assembly_accession={preparation.assembly_accession}",
                    f"fasta_sha256={preparation.extracted_fasta_sha256}",
                ],
            ),
            preparation,
        )
    except Exception as exc:
        return (
            _build_area(
                "live_sample_input",
                "unavailable",
                "Live FASTA download or SampleInput preparation failed.",
                evidence=[f"error={type(exc).__name__}: {exc}"],
            ),
            None,
        )


def _probe_amrfinderplus(
    *,
    settings: AppSettings,
    sample: Any,
    output_dir: Path,
) -> dict[str, Any]:
    runtime = inspect_amrfinderplus_runtime(
        executable_override=settings.integrations.amrfinderplus_bin,
        database_dir=settings.integrations.amrfinderplus_db,
    )
    if runtime.status != "ready":
        return _build_area(
            "amrfinderplus",
            "unavailable",
            "AMRFinderPlus is not live-ready on this machine, so the Phase 6B gate remains blocked.",
            evidence=[
                f"runtime_status={runtime.status}",
                *(f"note={note}" for note in runtime.notes),
            ],
        )

    try:
        plan = plan_amrfinderplus_execution(
            sample,
            output_dir=output_dir,
            fixture_mode=False,
            allow_fixture_fallback=False,
            repo_root=settings.repo_root,
            executable_override=settings.integrations.amrfinderplus_bin,
            database_dir=settings.integrations.amrfinderplus_db,
            runtime_info=runtime,
        )
        raw_output_path = execute_amrfinderplus(plan)
        runtime_json_path = write_amrfinderplus_runtime_metadata(plan, repo_root=settings.repo_root)
        rows = normalize_amrfinderplus_output(
            raw_output_path,
            job_id="job_phase6b_acceptance_amrfinder",
            sample_id=sample.sample_id,
            target_drug=sample.target_drug,
            raw_artifact_id="job_phase6b_acceptance_amrfinder_raw",
        )
        return _build_area(
            "amrfinderplus",
            "live",
            "AMRFinderPlus ran against the live FASTA and produced provenance-bearing mechanistic evidence.",
            evidence=[
                f"version={runtime.version}",
                f"database_version={runtime.database_version}",
                f"row_count={len(rows)}",
                f"runtime_json={runtime_json_path}",
            ],
        )
    except Exception as exc:
        return _build_area(
            "amrfinderplus",
            "degraded",
            "AMRFinderPlus is installed but the live acceptance execution failed.",
            evidence=[f"error={type(exc).__name__}: {exc}"],
        )


def _probe_mash(
    *,
    settings: AppSettings,
    sample: Any,
    output_dir: Path,
) -> dict[str, Any]:
    runtime = inspect_mash_runtime(executable_override=settings.integrations.mash_bin)
    if runtime.status != "ready":
        return _build_area(
            "mash",
            "unavailable",
            "Mash is not live-ready on this machine, so the Phase 6B gate remains blocked.",
            evidence=[
                f"runtime_status={runtime.status}",
                *(f"note={note}" for note in runtime.notes),
            ],
        )

    try:
        reference_plan = plan_mash_reference_workflow(
            repo_root=settings.repo_root,
            fixture_mode=False,
            artifact_root=settings.artifact_root,
            executable_override=settings.integrations.mash_bin,
            runtime_info=runtime,
        )
        query_plan = plan_mash_query_workflow(
            sample,
            reference_plan=reference_plan,
            output_dir=output_dir,
            fixture_mode=False,
            allow_fixture_fallback=False,
            repo_root=settings.repo_root,
            executable_override=settings.integrations.mash_bin,
            runtime_info=runtime,
        )
        execute_mash_reference_workflow(reference_plan)
        raw_output_path = execute_mash_query_workflow(query_plan)
        runtime_json_path = write_mash_runtime_metadata(
            reference_plan,
            query_plan,
            repo_root=settings.repo_root,
        )
        novelty = parse_mash_distance_output(
            raw_output_path,
            job_id="job_phase6b_acceptance_mash",
            sample_id=sample.sample_id,
            target_drug=sample.target_drug,
            reference_snapshot_id=reference_plan.snapshot_id,
            query_input=query_plan.query_input,
        )
        return _build_area(
            "mash",
            "live",
            "Mash sketched the live reference set and produced a real novelty assessment for the live FASTA.",
            evidence=[
                f"version={runtime.version}",
                f"reference_snapshot_id={reference_plan.snapshot_id}",
                f"novelty_bucket={novelty.novelty_bucket}",
                f"runtime_json={runtime_json_path}",
            ],
        )
    except Exception as exc:
        return _build_area(
            "mash",
            "degraded",
            "Mash is installed but the live novelty acceptance execution failed.",
            evidence=[f"error={type(exc).__name__}: {exc}"],
        )


def _probe_persistence_round_trip(
    *,
    settings: AppSettings,
    sample: Any | None,
    prerequisites: dict[str, str],
) -> dict[str, Any]:
    if settings.use_fixtures:
        return _build_area(
            "persistence",
            "fixture",
            "Phase 6B persistence smoke is blocked because the current settings are still in fixture mode.",
            evidence=["USE_FIXTURES=true"],
        )

    missing = [name for name, status in prerequisites.items() if status != "live"]
    if sample is None or missing:
        return _build_area(
            "persistence",
            "blocked",
            "Live Phase 6 persistence round-trip did not run because upstream live prerequisites were not all satisfied.",
            evidence=[*(f"{name}={status}" for name, status in prerequisites.items())],
        )

    try:
        persistence = SQLitePersistence(settings.sqlite_db_path, repo_root=settings.repo_root)
        service = AnalysisService(settings=settings, persistence=persistence)
        result = service.analyze(sample)
        if result.decision is None:
            raise ValueError("AnalysisService did not return a DecisionObject.")
        stored_decision = persistence.get_decision(result.response.job_id)
        stored_manifest = persistence.get_job_artifact_manifest(result.response.job_id)
        stored_status = persistence.get_job_status(result.response.job_id)
        if stored_decision is None or stored_manifest is None or stored_status is None:
            raise ValueError("Persisted live analysis output could not be reconstructed.")
        return _build_area(
            "persistence",
            "live",
            "A live Phase 6 analysis persisted and round-tripped decision, status, and artifact records.",
            evidence=[
                f"job_id={result.response.job_id}",
                f"job_status={stored_status.status}",
                f"artifact_count={len(stored_manifest.artifacts)}",
                f"prediction={stored_decision.phenotype_prediction.predicted_phenotype}",
            ],
        )
    except Exception as exc:
        return _build_area(
            "persistence",
            "degraded",
            "A live Phase 6 analysis ran far enough to probe persistence, but the round-trip verification failed.",
            evidence=[f"error={type(exc).__name__}: {exc}"],
        )


def _build_phase7_gate(
    *,
    settings: AppSettings,
    areas: list[dict[str, Any]],
) -> dict[str, Any]:
    area_by_name = {area["name"]: area for area in areas}
    blocking_areas = sorted(
        name for name in PHASE6B_AREA_NAMES if area_by_name[name]["status"] != "live"
    )

    if settings.use_fixtures:
        return {
            "status": "blocked",
            "can_begin": False,
            "blocking_areas": blocking_areas,
            "summary": "Phase 7 remains blocked because the backend is still configured for fixture mode rather than fully live evidence.",
        }

    if blocking_areas:
        return {
            "status": "blocked",
            "can_begin": False,
            "blocking_areas": blocking_areas,
            "summary": "Phase 7 remains blocked until every required Phase 6B area is live instead of unavailable, blocked, fixture-backed, or degraded.",
        }

    return {
        "status": "ready",
        "can_begin": True,
        "blocking_areas": [],
        "summary": "Phase 7 may begin because the Phase 6B live data, tool, retrieval, and persistence gates are all satisfied.",
    }


def _build_area(
    name: str,
    status: str,
    summary: str,
    *,
    evidence: list[str],
) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "summary": summary,
        "evidence": evidence,
    }


def _is_tracked_in_git(path: Path, *, repo_root: Path) -> bool:
    if not _is_within_repo(path, repo_root=repo_root):
        return False
    relative_path = _display_path(path, repo_root=repo_root)
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", relative_path],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def _is_within_repo(path: Path, *, repo_root: Path) -> bool:
    candidate = path.expanduser()
    try:
        candidate.resolve().relative_to(repo_root.resolve())
        return True
    except ValueError:
        return False


def _display_path(path: Path, *, repo_root: Path) -> str:
    expanded = path.expanduser()
    try:
        return expanded.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return expanded.name
