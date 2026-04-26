from __future__ import annotations

import csv
import json
import subprocess
from dataclasses import dataclass
import hashlib
from pathlib import Path
from shutil import which

from app.contracts import NoveltyAssessment, NoveltyBucket, SampleInput
from app.contracts.common import normalize_slug_like
from app.paths import display_path, resolve_local_path


_FASTA_SUFFIXES = {".fa", ".fasta", ".fna"}
_MASH_DISTANCE_COLUMNS = (
    "reference_id",
    "query_id",
    "mash_distance",
    "p_value",
    "matching_hashes",
)


@dataclass(frozen=True)
class MashReferencePlan:
    snapshot_id: str
    sketch_id: str
    mode: str
    cache_key: str
    reference_inputs: tuple[Path, ...]
    output_prefix: Path
    output_path: Path
    command: tuple[str, ...]
    notes: tuple[str, ...]
    timeout_seconds: int
    executable_path: Path | None = None
    executable_source: str = "PATH"
    version: str | None = None


@dataclass(frozen=True)
class MashQueryPlan:
    sample_id: str
    mode: str
    raw_output_path: Path
    query_input: Path
    reference_sketch_path: Path
    query_sketch_path: Path
    runtime_json_path: Path
    command: tuple[str, ...]
    fixture_fallback_used: bool
    message: str
    timeout_seconds: int
    executable_path: Path | None = None
    executable_source: str = "PATH"
    version: str | None = None


@dataclass(frozen=True)
class MashRuntimeInfo:
    status: str
    tool_available: bool
    executable_path: Path | None
    executable_source: str
    version: str | None
    version_command: tuple[str, ...]
    notes: tuple[str, ...]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _resolve_mash_executable_path(executable_override: str | None) -> tuple[Path | None, str]:
    if executable_override:
        override_path = Path(executable_override).expanduser()
        if override_path.exists():
            return override_path.resolve(strict=False), "override_path"
        discovered = which(executable_override)
        if discovered:
            return Path(discovered).resolve(strict=False), "override_command"
        return None, "override"

    discovered = which("mash")
    if discovered:
        return Path(discovered).resolve(strict=False), "PATH"
    return None, "PATH"


def _run_text_command(command: tuple[str, ...], *, timeout_seconds: int) -> tuple[int, str, str]:
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )
    return completed.returncode, completed.stdout.strip(), completed.stderr.strip()


def _first_text_line(*values: str | None) -> str | None:
    for value in values:
        if value is None:
            continue
        for line in value.splitlines():
            stripped = line.strip()
            if stripped:
                return stripped
    return None


def inspect_mash_runtime(
    *,
    executable_override: str | None = None,
    timeout_seconds: int = 30,
) -> MashRuntimeInfo:
    executable_path, executable_source = _resolve_mash_executable_path(executable_override)
    if executable_path is None:
        notes = ["Mash executable was not found."]
        if executable_override:
            notes = ["Configured Mash executable override was not found."]
        return MashRuntimeInfo(
            status="tool_missing",
            tool_available=False,
            executable_path=None,
            executable_source=executable_source,
            version=None,
            version_command=(),
            notes=tuple(notes),
        )

    version_command = (str(executable_path), "--version")
    version_returncode, version_stdout, version_stderr = _run_text_command(
        version_command,
        timeout_seconds=timeout_seconds,
    )
    if version_returncode == 0:
        return MashRuntimeInfo(
            status="ready",
            tool_available=True,
            executable_path=executable_path,
            executable_source=executable_source,
            version=_first_text_line(version_stdout, version_stderr),
            version_command=version_command,
            notes=(),
        )
    return MashRuntimeInfo(
        status="version_unavailable",
        tool_available=True,
        executable_path=executable_path,
        executable_source=executable_source,
        version=None,
        version_command=version_command,
        notes=(
            _first_text_line(version_stderr, version_stdout)
            or "Mash version inspection failed.",
        ),
    )


def build_mash_sketch_command(
    reference_inputs: tuple[Path, ...],
    output_prefix: Path,
    *,
    executable: str = "mash",
) -> tuple[str, ...]:
    return (
        executable,
        "sketch",
        "-o",
        str(output_prefix),
        *(str(path) for path in reference_inputs),
    )


def build_mash_dist_command(
    reference_sketch_path: Path,
    query_input: Path,
    *,
    executable: str = "mash",
) -> tuple[str, ...]:
    return (
        executable,
        "dist",
        str(reference_sketch_path),
        str(query_input),
    )


def mash_available() -> bool:
    return which("mash") is not None


def _resolve_reference_inputs(snapshot: dict, *, root: Path, fixture_mode: bool) -> tuple[Path, ...]:
    reference_file_key = "fixture_files" if fixture_mode else "mash_reference_files"
    reference_inputs = tuple(
        root / relative_path
        for relative_path in snapshot.get(reference_file_key, [])
        if Path(relative_path).suffix.lower() in _FASTA_SUFFIXES
    )
    if not reference_inputs:
        raise ValueError(f"Snapshot manifest must include at least one FASTA in {reference_file_key}.")
    return reference_inputs


def plan_mash_reference_workflow(
    *,
    snapshot_manifest_path: Path | None = None,
    fixture_mode: bool = True,
    repo_root: Path | None = None,
    artifact_root: Path | None = None,
    timeout_seconds: int = 600,
    executable_override: str | None = None,
    runtime_info: MashRuntimeInfo | None = None,
) -> MashReferencePlan:
    root = repo_root or _repo_root()
    resolved_artifact_root = Path(artifact_root) if artifact_root is not None else root / "artifacts"
    manifest_path = snapshot_manifest_path or root / "data/snapshots/2026-04-20_phase3_foundation_snapshot.json"
    snapshot = json.loads(manifest_path.read_text(encoding="utf-8"))
    snapshot_id = snapshot["snapshot_id"]
    checksum = snapshot["metadata_checksum_sha256"]
    cache_key = f"{snapshot_id}:{checksum}"
    sketch_id = f"{snapshot_id}_{checksum[:12]}"

    reference_inputs = _resolve_reference_inputs(snapshot, root=root, fixture_mode=fixture_mode)
    runtime = runtime_info
    if runtime is None and executable_override is not None:
        runtime = inspect_mash_runtime(executable_override=executable_override)
    if fixture_mode:
        output_path = root / "data/fixtures/smoke/reference_smoke_fixture.msh"
        output_prefix = output_path.with_suffix("")
        notes = (
            "Fixture sketch placeholder is committed for smoke validation.",
            "Sketch identity changes when the snapshot checksum changes.",
        )
    else:
        output_prefix = resolved_artifact_root / "runs" / "mash" / snapshot_id / sketch_id
        output_path = output_prefix.with_suffix(".msh")
        notes = (
            "Live Mash sketch should be rebuilt when the snapshot checksum changes.",
        )
        if runtime is not None and runtime.status != "ready":
            return MashReferencePlan(
                snapshot_id=snapshot_id,
                sketch_id=sketch_id,
                mode="unavailable",
                cache_key=cache_key,
                reference_inputs=reference_inputs,
                output_prefix=output_prefix,
                output_path=output_path,
                command=(),
                notes=runtime.notes,
                timeout_seconds=timeout_seconds,
                executable_path=runtime.executable_path,
                executable_source=runtime.executable_source,
                version=runtime.version,
            )

    executable = str(runtime.executable_path) if runtime and runtime.executable_path is not None else "mash"

    return MashReferencePlan(
        snapshot_id=snapshot_id,
        sketch_id=sketch_id,
        mode="fixture" if fixture_mode else "live",
        cache_key=cache_key,
        reference_inputs=reference_inputs,
        output_prefix=output_prefix,
        output_path=output_path,
        command=build_mash_sketch_command(reference_inputs, output_prefix, executable=executable),
        notes=notes,
        timeout_seconds=timeout_seconds,
        executable_path=runtime.executable_path if runtime else None,
        executable_source=runtime.executable_source if runtime else "PATH",
        version=runtime.version if runtime else None,
    )


def plan_mash_query_workflow(
    sample: SampleInput,
    *,
    reference_plan: MashReferencePlan,
    output_dir: Path,
    fixture_mode: bool = False,
    allow_fixture_fallback: bool = True,
    repo_root: Path | None = None,
    timeout_seconds: int = 600,
    executable_override: str | None = None,
    runtime_info: MashRuntimeInfo | None = None,
) -> MashQueryPlan:
    root = repo_root or _repo_root()
    if sample.fasta_path is None:
        raise ValueError("Mash query workflow requires a local fasta_path.")

    query_input = resolve_local_path(sample.fasta_path, repo_root=root)
    live_output_dir = Path(output_dir)
    live_output_path = live_output_dir / f"{sample.sample_id}.mash.dist.tsv"
    query_sketch_path = live_output_dir / f"{sample.sample_id}.msh"
    runtime_json_path = live_output_dir / f"{sample.sample_id}.mash.runtime.json"
    fixture_output_path = root / f"data/fixtures/smoke/{sample.sample_id}.mash.dist.tsv"
    if (
        not fixture_mode
        and any(
            reference_input.resolve(strict=False) == query_input.resolve(strict=False)
            for reference_input in reference_plan.reference_inputs
        )
    ):
        raise ValueError("Mash query workflow requires a non-self reference set.")
    runtime = runtime_info
    if runtime is None and executable_override is not None:
        runtime = inspect_mash_runtime(executable_override=executable_override)
    executable = str(runtime.executable_path) if runtime and runtime.executable_path is not None else "mash"
    command = build_mash_dist_command(reference_plan.output_path, query_input, executable=executable)
    tool_available = runtime.tool_available if runtime is not None else mash_available()

    if fixture_mode:
        return MashQueryPlan(
            sample_id=sample.sample_id,
            mode="fixture",
            raw_output_path=fixture_output_path,
            query_input=query_input,
            reference_sketch_path=reference_plan.output_path,
            query_sketch_path=fixture_output_path.with_suffix(".query.msh"),
            runtime_json_path=runtime_json_path,
            command=command,
            fixture_fallback_used=True,
            message="Using committed Mash distance fixture output.",
            timeout_seconds=timeout_seconds,
            executable_path=runtime.executable_path if runtime else None,
            executable_source=runtime.executable_source if runtime else "PATH",
            version=runtime.version if runtime else None,
        )

    if runtime is not None and runtime.status != "ready" and not allow_fixture_fallback:
        return MashQueryPlan(
            sample_id=sample.sample_id,
            mode="unavailable",
            raw_output_path=live_output_path,
            query_input=query_input,
            reference_sketch_path=reference_plan.output_path,
            query_sketch_path=query_sketch_path,
            runtime_json_path=runtime_json_path,
            command=command,
            fixture_fallback_used=False,
            message=" ".join(runtime.notes),
            timeout_seconds=timeout_seconds,
            executable_path=runtime.executable_path,
            executable_source=runtime.executable_source,
            version=runtime.version,
        )

    if not tool_available and allow_fixture_fallback:
        return MashQueryPlan(
            sample_id=sample.sample_id,
            mode="fixture",
            raw_output_path=fixture_output_path,
            query_input=query_input,
            reference_sketch_path=reference_plan.output_path,
            query_sketch_path=fixture_output_path.with_suffix(".query.msh"),
            runtime_json_path=runtime_json_path,
            command=command,
            fixture_fallback_used=True,
            message="Mash executable was not found; fixture fallback is active.",
            timeout_seconds=timeout_seconds,
            executable_path=runtime.executable_path if runtime else None,
            executable_source=runtime.executable_source if runtime else "PATH",
            version=runtime.version if runtime else None,
        )

    if reference_plan.mode == "live":
        return MashQueryPlan(
            sample_id=sample.sample_id,
            mode="live",
            raw_output_path=live_output_path,
            query_input=query_input,
            reference_sketch_path=reference_plan.output_path,
            query_sketch_path=query_sketch_path,
            runtime_json_path=runtime_json_path,
            command=command,
            fixture_fallback_used=False,
            message="Mash distance command planned for local execution.",
            timeout_seconds=timeout_seconds,
            executable_path=runtime.executable_path if runtime else None,
            executable_source=runtime.executable_source if runtime else "PATH",
            version=runtime.version if runtime else None,
        )

    if allow_fixture_fallback:
        return MashQueryPlan(
            sample_id=sample.sample_id,
            mode="fixture",
            raw_output_path=fixture_output_path,
            query_input=query_input,
            reference_sketch_path=reference_plan.output_path,
            query_sketch_path=fixture_output_path.with_suffix(".query.msh"),
            runtime_json_path=runtime_json_path,
            command=command,
            fixture_fallback_used=True,
            message="Mash query workflow is using committed fixture output.",
            timeout_seconds=timeout_seconds,
            executable_path=runtime.executable_path if runtime else None,
            executable_source=runtime.executable_source if runtime else "PATH",
            version=runtime.version if runtime else None,
        )

    return MashQueryPlan(
        sample_id=sample.sample_id,
        mode="unavailable",
        raw_output_path=live_output_path,
        query_input=query_input,
        reference_sketch_path=reference_plan.output_path,
        query_sketch_path=query_sketch_path,
        runtime_json_path=runtime_json_path,
        command=command,
        fixture_fallback_used=False,
        message="Mash query workflow is unavailable without live sketching or fixture fallback.",
        timeout_seconds=timeout_seconds,
        executable_path=runtime.executable_path if runtime else None,
        executable_source=runtime.executable_source if runtime else "PATH",
        version=runtime.version if runtime else None,
    )


def execute_mash_reference_workflow(plan: MashReferencePlan) -> Path:
    if plan.mode != "live":
        return plan.output_path

    plan.output_prefix.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        plan.command,
        check=True,
        capture_output=True,
        text=True,
        timeout=plan.timeout_seconds,
    )
    if not plan.output_path.exists():
        raise FileNotFoundError(
            f"Mash sketch completed without producing the expected output: {plan.output_path}"
        )
    return plan.output_path


def execute_mash_query_workflow(plan: MashQueryPlan) -> Path:
    if plan.mode != "live":
        return plan.raw_output_path

    if not plan.reference_sketch_path.exists():
        raise FileNotFoundError(
            f"Mash query workflow requires an existing reference sketch: {plan.reference_sketch_path}"
        )
    plan.raw_output_path.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        plan.command,
        check=True,
        capture_output=True,
        text=True,
        timeout=plan.timeout_seconds,
    )
    plan.raw_output_path.write_text(completed.stdout, encoding="utf-8")
    if not plan.raw_output_path.exists():
        raise FileNotFoundError(
            f"Mash dist completed without producing the expected output: {plan.raw_output_path}"
        )
    return plan.raw_output_path


def _sha256_for_path(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_mash_runtime_metadata(
    reference_plan: MashReferencePlan,
    query_plan: MashQueryPlan,
    *,
    repo_root: Path | None = None,
) -> dict[str, object]:
    root = repo_root or _repo_root()
    return {
        "sample_id": query_plan.sample_id,
        "mode": query_plan.mode,
        "message": query_plan.message,
        "version": query_plan.version or reference_plan.version,
        "executable_path": (
            display_path(query_plan.executable_path, repo_root=root)
            if query_plan.executable_path is not None
            else (
                display_path(reference_plan.executable_path, repo_root=root)
                if reference_plan.executable_path is not None
                else None
            )
        ),
        "executable_source": query_plan.executable_source or reference_plan.executable_source,
        "reference_snapshot_id": reference_plan.snapshot_id,
        "reference_sketch_path": display_path(reference_plan.output_path, repo_root=root),
        "reference_sketch_sha256": _sha256_for_path(reference_plan.output_path),
        "reference_inputs": [
            display_path(path, repo_root=root)
            for path in reference_plan.reference_inputs
        ],
        "query_input": display_path(query_plan.query_input, repo_root=root),
        "raw_output_path": display_path(query_plan.raw_output_path, repo_root=root),
        "raw_output_sha256": _sha256_for_path(query_plan.raw_output_path),
        "command": list(query_plan.command),
        "reference_command": list(reference_plan.command),
    }


def write_mash_runtime_metadata(
    reference_plan: MashReferencePlan,
    query_plan: MashQueryPlan,
    *,
    repo_root: Path | None = None,
) -> Path:
    payload = build_mash_runtime_metadata(reference_plan, query_plan, repo_root=repo_root)
    query_plan.runtime_json_path.parent.mkdir(parents=True, exist_ok=True)
    query_plan.runtime_json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return query_plan.runtime_json_path


def _read_mash_distance_rows(raw_output_path: Path) -> list[dict[str, str]]:
    with raw_output_path.open("r", encoding="utf-8", newline="") as handle:
        raw_rows = [
            row
            for row in csv.reader(handle, delimiter="\t")
            if row and any(cell.strip() for cell in row)
        ]

    if not raw_rows:
        return []

    has_header = tuple(cell.strip() for cell in raw_rows[0]) == _MASH_DISTANCE_COLUMNS
    data_rows = raw_rows[1:] if has_header else raw_rows
    parsed_rows: list[dict[str, str]] = []
    for row_number, row in enumerate(data_rows, start=2 if has_header else 1):
        if len(row) < len(_MASH_DISTANCE_COLUMNS):
            raise ValueError(f"Mash distance row {row_number} has fewer than 5 columns.")
        parsed_rows.append(dict(zip(_MASH_DISTANCE_COLUMNS, row[: len(_MASH_DISTANCE_COLUMNS)], strict=True)))
    return parsed_rows


def _sanitize_mash_identifier(raw_identifier: str) -> str:
    cleaned = raw_identifier.strip().replace("\\", "/")
    parsed_path = Path(cleaned)
    candidate = (
        parsed_path.stem
        if "/" in cleaned or parsed_path.suffix.lower() in _FASTA_SUFFIXES
        else cleaned
    )
    return normalize_slug_like(candidate)


def parse_mash_distance_output(
    raw_output_path: Path,
    *,
    job_id: str,
    sample_id: str,
    target_drug: str,
    reference_snapshot_id: str,
    query_input: str | Path | None = None,
) -> NoveltyAssessment:
    normalized_sample_id = normalize_slug_like(sample_id)
    expected_query_ids = {normalized_sample_id}
    if query_input is not None:
        expected_query_ids.add(_sanitize_mash_identifier(str(query_input)))
    rows = _read_mash_distance_rows(raw_output_path)

    if not rows:
        return NoveltyAssessment(
            job_id=job_id,
            sample_id=normalized_sample_id,
            target_drug=target_drug,
            reference_snapshot_id=reference_snapshot_id,
            novelty_bucket=NoveltyBucket.UNKNOWN,
            missing_reference=True,
            warnings=["No Mash distance rows were available for novelty estimation."],
        )

    parsed_rows = []
    for row in rows:
        query_id = _sanitize_mash_identifier(row["query_id"])
        if query_id not in expected_query_ids:
            expected_values = ", ".join(sorted(expected_query_ids))
            raise ValueError(
                f"Mash distance row query_id {query_id} does not match sample_id or query_input identifiers: "
                f"{expected_values}."
            )
        parsed_rows.append(
            {
                "reference_id": _sanitize_mash_identifier(row["reference_id"]),
                "query_id": query_id,
                "mash_distance": float(row["mash_distance"]),
                "p_value": float(row["p_value"]),
                "matching_hashes": row["matching_hashes"],
            }
        )

    nearest_row = min(parsed_rows, key=lambda item: item["mash_distance"])
    nearest_distance = nearest_row["mash_distance"]
    novelty_score = round(min(1.0, nearest_distance / 0.1), 4)
    novelty_percentile = round(novelty_score * 100.0, 2)

    if nearest_distance < 0.02:
        novelty_bucket = NoveltyBucket.KNOWN
    elif nearest_distance < 0.05:
        novelty_bucket = NoveltyBucket.ELEVATED
    else:
        novelty_bucket = NoveltyBucket.HIGH

    return NoveltyAssessment(
        job_id=job_id,
        sample_id=normalized_sample_id,
        target_drug=target_drug,
        reference_snapshot_id=reference_snapshot_id,
        nearest_neighbor_id=nearest_row["reference_id"],
        nearest_neighbor_distance=nearest_distance,
        novelty_score=novelty_score,
        novelty_percentile=novelty_percentile,
        novelty_bucket=novelty_bucket,
        missing_reference=False,
        uncertainty_flag=len(parsed_rows) < 3,
        warnings=[],
    )
