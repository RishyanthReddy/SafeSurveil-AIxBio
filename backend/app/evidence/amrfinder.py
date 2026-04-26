from __future__ import annotations

import csv
import subprocess
from dataclasses import dataclass
import json
from pathlib import Path
from shutil import which

from app.contracts import MechanismSupportLevel, MechanisticEvidence, SampleInput
from app.paths import display_path, resolve_local_path


_DRUG_HINTS_BY_SYMBOL_PREFIX = {
    "tet": ["tetracycline"],
    "bla": ["ampicillin"],
    "gyr": ["ciprofloxacin"],
    "erm": ["erythromycin"],
    "mec": ["oxacillin"],
}

_DRUG_HINTS_BY_TEXT = {
    "tetracycline": "tetracycline",
    "beta-lactam": "ampicillin",
    "penicillin": "ampicillin",
    "ciprofloxacin": "ciprofloxacin",
    "quinolone": "ciprofloxacin",
    "erythromycin": "erythromycin",
    "macrolide": "erythromycin",
    "oxacillin": "oxacillin",
}
_GENE_SYMBOL_COLUMNS = ("Element symbol", "Gene symbol")
_ELEMENT_NAME_COLUMNS = ("Element name", "Sequence name")
_CLASS_COLUMNS = ("Class", "Type", "Element type")
_SUBCLASS_COLUMNS = ("Subclass", "Subtype", "Element subtype")
_COVERAGE_COLUMNS = ("% Coverage of reference", "% Coverage of reference sequence")
_IDENTITY_COLUMNS = ("% Identity to reference", "% Identity to reference sequence")


@dataclass(frozen=True)
class AMRFinderExecutionPlan:
    sample_id: str
    mode: str
    status: str
    raw_output_path: Path
    runtime_json_path: Path
    output_dir: Path
    command: tuple[str, ...]
    timeout_seconds: int
    tool_available: bool
    fixture_fallback_used: bool
    message: str
    executable_path: Path | None = None
    executable_source: str = "PATH"
    version: str | None = None
    database_path: Path | None = None
    database_status: str = "unknown"
    database_version: str | None = None
    database_check_command: tuple[str, ...] = ()


@dataclass(frozen=True)
class AMRFinderRuntimeInfo:
    status: str
    tool_available: bool
    executable_path: Path | None
    executable_source: str
    version: str | None
    database_path: Path | None
    database_status: str
    database_version: str | None
    version_command: tuple[str, ...]
    database_check_command: tuple[str, ...]
    notes: tuple[str, ...]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _resolve_executable_path(executable_override: str | None) -> tuple[Path | None, str]:
    if executable_override:
        override_path = Path(executable_override).expanduser()
        if override_path.exists():
            return override_path.resolve(strict=False), "override_path"
        discovered = which(executable_override)
        if discovered:
            return Path(discovered).resolve(strict=False), "override_command"
        return None, "override"

    discovered = which("amrfinder")
    if discovered:
        return Path(discovered).resolve(strict=False), "PATH"
    return None, "PATH"


def _resolve_database_path(database_dir: Path | str | None) -> Path | None:
    if database_dir is None:
        return None
    return Path(database_dir).expanduser().resolve(strict=False)


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


def _database_version_from_output(*values: str | None) -> str | None:
    for value in values:
        if value is None:
            continue
        for line in value.splitlines():
            stripped = line.strip()
            if stripped.lower().startswith("database version:"):
                _, _, remainder = stripped.partition(":")
                version = remainder.strip()
                if version:
                    return version
    return _first_text_line(*values)


def inspect_amrfinderplus_runtime(
    *,
    executable_override: str | None = None,
    database_dir: Path | str | None = None,
    timeout_seconds: int = 30,
) -> AMRFinderRuntimeInfo:
    executable_path, executable_source = _resolve_executable_path(executable_override)
    resolved_database_path = _resolve_database_path(database_dir)
    if executable_path is None:
        notes = ["AMRFinderPlus executable was not found."]
        if executable_override:
            notes = ["Configured AMRFinderPlus executable override was not found."]
        return AMRFinderRuntimeInfo(
            status="tool_missing",
            tool_available=False,
            executable_path=None,
            executable_source=executable_source,
            version=None,
            database_path=resolved_database_path,
            database_status="unknown",
            database_version=None,
            version_command=(),
            database_check_command=(),
            notes=tuple(notes),
        )

    version_command = (str(executable_path), "--version")
    version_returncode, version_stdout, version_stderr = _run_text_command(
        version_command,
        timeout_seconds=timeout_seconds,
    )
    version = None if version_returncode != 0 else _first_text_line(version_stdout, version_stderr)

    if resolved_database_path is not None and not resolved_database_path.exists():
        return AMRFinderRuntimeInfo(
            status="database_missing",
            tool_available=True,
            executable_path=executable_path,
            executable_source=executable_source,
            version=version,
            database_path=resolved_database_path,
            database_status="missing",
            database_version=None,
            version_command=version_command,
            database_check_command=(str(executable_path), "--database", str(resolved_database_path), "--database_version"),
            notes=("Configured AMRFinderPlus database directory does not exist.",),
        )

    database_check_command = [str(executable_path)]
    if resolved_database_path is not None:
        database_check_command.extend(["--database", str(resolved_database_path)])
    database_check_command.append("--database_version")
    database_returncode, database_stdout, database_stderr = _run_text_command(
        tuple(database_check_command),
        timeout_seconds=timeout_seconds,
    )
    database_version = (
        _database_version_from_output(database_stdout, database_stderr)
        if database_returncode == 0
        else None
    )
    if database_returncode == 0:
        return AMRFinderRuntimeInfo(
            status="ready",
            tool_available=True,
            executable_path=executable_path,
            executable_source=executable_source,
            version=version,
            database_path=resolved_database_path,
            database_status="ready",
            database_version=database_version,
            version_command=version_command,
            database_check_command=tuple(database_check_command),
            notes=(),
        )

    return AMRFinderRuntimeInfo(
        status="database_unavailable",
        tool_available=True,
        executable_path=executable_path,
        executable_source=executable_source,
        version=version,
        database_path=resolved_database_path,
        database_status="unavailable",
        database_version=None,
        version_command=version_command,
        database_check_command=tuple(database_check_command),
        notes=(
            _first_text_line(database_stderr, database_stdout)
            or "AMRFinderPlus database readiness check failed.",
        ),
    )


def build_amrfinderplus_command(
    sample: SampleInput,
    raw_output_path: Path,
    *,
    executable: str = "amrfinder",
    database_dir: Path | None = None,
) -> tuple[str, ...]:
    if sample.fasta_path is None:
        raise ValueError("AMRFinderPlus command construction requires a local fasta_path.")
    fasta_path = Path(sample.fasta_path)
    command = [executable]
    if database_dir is not None:
        command.extend(["--database", str(database_dir)])
    command.extend(["-n", str(fasta_path), "-o", str(raw_output_path)])
    return tuple(command)


def _resolve_fixture_raw_output(sample: SampleInput, repo_root: Path | None = None) -> Path:
    root = repo_root or _repo_root()
    return root / f"data/fixtures/smoke/{sample.sample_id}.amrfinder.tsv"


def plan_amrfinderplus_execution(
    sample: SampleInput,
    *,
    output_dir: Path,
    fixture_mode: bool = False,
    allow_fixture_fallback: bool = True,
    timeout_seconds: int = 600,
    repo_root: Path | None = None,
    executable_override: str | None = None,
    database_dir: Path | str | None = None,
    runtime_info: AMRFinderRuntimeInfo | None = None,
) -> AMRFinderExecutionPlan:
    root = repo_root or _repo_root()
    live_output_dir = Path(output_dir)
    live_output_path = live_output_dir / f"{sample.sample_id}.amrfinder.tsv"
    runtime_json_path = live_output_dir / f"{sample.sample_id}.amrfinder.runtime.json"
    if sample.fasta_path is None:
        raise ValueError("AMRFinderPlus planning requires a local fasta_path.")
    fasta_path = resolve_local_path(sample.fasta_path, repo_root=root)
    resolved_sample = sample.model_copy(update={"fasta_path": str(fasta_path)})
    runtime = runtime_info or inspect_amrfinderplus_runtime(
        executable_override=executable_override,
        database_dir=database_dir,
        timeout_seconds=min(timeout_seconds, 30),
    )
    command = build_amrfinderplus_command(
        resolved_sample,
        live_output_path,
        executable=str(runtime.executable_path) if runtime.executable_path is not None else "amrfinder",
        database_dir=runtime.database_path if runtime.database_status == "ready" and runtime.database_path is not None else None,
    )
    tool_available = runtime.tool_available

    if fixture_mode:
        fixture_path = _resolve_fixture_raw_output(sample, repo_root=root)
        return AMRFinderExecutionPlan(
            sample_id=sample.sample_id,
            mode="fixture",
            status="fixture_ready",
            raw_output_path=fixture_path,
            runtime_json_path=runtime_json_path,
            output_dir=fixture_path.parent,
            command=command,
            timeout_seconds=timeout_seconds,
            tool_available=tool_available,
            fixture_fallback_used=True,
            message="Using committed AMRFinderPlus fixture output.",
            executable_path=runtime.executable_path,
            executable_source=runtime.executable_source,
            version=runtime.version,
            database_path=runtime.database_path,
            database_status=runtime.database_status,
            database_version=runtime.database_version,
            database_check_command=runtime.database_check_command,
        )

    if runtime.status == "ready":
        return AMRFinderExecutionPlan(
            sample_id=sample.sample_id,
            mode="live",
            status="planned",
            raw_output_path=live_output_path,
            runtime_json_path=runtime_json_path,
            output_dir=live_output_dir,
            command=command,
            timeout_seconds=timeout_seconds,
            tool_available=True,
            fixture_fallback_used=False,
            message="AMRFinderPlus command planned for local execution.",
            executable_path=runtime.executable_path,
            executable_source=runtime.executable_source,
            version=runtime.version,
            database_path=runtime.database_path,
            database_status=runtime.database_status,
            database_version=runtime.database_version,
            database_check_command=runtime.database_check_command,
        )

    if allow_fixture_fallback:
        fixture_path = _resolve_fixture_raw_output(sample, repo_root=root)
        return AMRFinderExecutionPlan(
            sample_id=sample.sample_id,
            mode="fixture",
            status="fixture_fallback",
            raw_output_path=fixture_path,
            runtime_json_path=runtime_json_path,
            output_dir=fixture_path.parent,
            command=command,
            timeout_seconds=timeout_seconds,
            tool_available=runtime.tool_available,
            fixture_fallback_used=True,
            message="AMRFinderPlus live runtime is unavailable; fixture fallback is active.",
            executable_path=runtime.executable_path,
            executable_source=runtime.executable_source,
            version=runtime.version,
            database_path=runtime.database_path,
            database_status=runtime.database_status,
            database_version=runtime.database_version,
            database_check_command=runtime.database_check_command,
        )

    return AMRFinderExecutionPlan(
        sample_id=sample.sample_id,
        mode="unavailable",
        status=runtime.status,
        raw_output_path=live_output_path,
        runtime_json_path=runtime_json_path,
        output_dir=live_output_dir,
        command=command,
        timeout_seconds=timeout_seconds,
        tool_available=runtime.tool_available,
        fixture_fallback_used=False,
        message=" ".join(runtime.notes),
        executable_path=runtime.executable_path,
        executable_source=runtime.executable_source,
        version=runtime.version,
        database_path=runtime.database_path,
        database_status=runtime.database_status,
        database_version=runtime.database_version,
        database_check_command=runtime.database_check_command,
    )


def execute_amrfinderplus(plan: AMRFinderExecutionPlan) -> Path:
    if plan.mode != "live":
        return plan.raw_output_path

    plan.output_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        plan.command,
        check=True,
        capture_output=True,
        text=True,
        timeout=plan.timeout_seconds,
    )
    if not plan.raw_output_path.exists():
        raise FileNotFoundError(
            f"AMRFinderPlus completed without producing the expected output: {plan.raw_output_path}"
        )
    return plan.raw_output_path


def build_amrfinderplus_runtime_metadata(
    plan: AMRFinderExecutionPlan,
    *,
    repo_root: Path | None = None,
) -> dict[str, object]:
    root = repo_root or _repo_root()
    return {
        "sample_id": plan.sample_id,
        "mode": plan.mode,
        "status": plan.status,
        "message": plan.message,
        "command": list(plan.command),
        "executable_path": (
            display_path(plan.executable_path, repo_root=root) if plan.executable_path is not None else None
        ),
        "executable_source": plan.executable_source,
        "version": plan.version,
        "database_path": (
            display_path(plan.database_path, repo_root=root) if plan.database_path is not None else None
        ),
        "database_status": plan.database_status,
        "database_version": plan.database_version,
        "database_check_command": list(plan.database_check_command),
        "raw_output_path": display_path(plan.raw_output_path, repo_root=root),
        "fixture_fallback_used": plan.fixture_fallback_used,
    }


def write_amrfinderplus_runtime_metadata(
    plan: AMRFinderExecutionPlan,
    *,
    repo_root: Path | None = None,
) -> Path:
    payload = build_amrfinderplus_runtime_metadata(plan, repo_root=repo_root)
    plan.runtime_json_path.parent.mkdir(parents=True, exist_ok=True)
    plan.runtime_json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return plan.runtime_json_path


def _read_amrfinder_rows(raw_output_path: Path) -> list[dict[str, str]]:
    with raw_output_path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _parse_float(value: str | None) -> float | None:
    if value in (None, "", "NA"):
        return None
    return float(value)


def _first_present(row: dict[str, str], columns: tuple[str, ...]) -> str | None:
    for column in columns:
        value = row.get(column)
        if value not in (None, ""):
            return value
    return None


def _derive_drug_association(
    gene_symbol: str | None,
    element_name: str | None,
    raw_class: str | None,
    raw_subclass: str | None,
) -> list[str]:
    hits: set[str] = set()
    normalized_symbol = (gene_symbol or "").strip().lower()
    for prefix, drugs in _DRUG_HINTS_BY_SYMBOL_PREFIX.items():
        if normalized_symbol.startswith(prefix):
            hits.update(drugs)

    for text_value in (element_name, raw_class, raw_subclass):
        lowered = (text_value or "").strip().lower()
        for needle, drug in _DRUG_HINTS_BY_TEXT.items():
            if needle in lowered:
                hits.add(drug)

    return sorted(hits)


def _derive_mechanism_class(
    gene_symbol: str | None,
    element_name: str | None,
    raw_class: str | None,
    raw_subclass: str | None,
) -> str:
    combined = " ".join(
        value.strip().lower()
        for value in (gene_symbol, element_name, raw_class, raw_subclass)
        if value
    )
    if "efflux" in combined:
        return "efflux"
    if "protection" in combined:
        return "ribosomal_protection"
    if "beta-lactamase" in combined or (gene_symbol or "").lower().startswith("bla"):
        return "beta_lactamase"
    if "mutation" in combined:
        return "target_modification"
    return "sequence_feature"


def _derive_support_level(method: str | None, coverage: float | None, identity: float | None) -> MechanismSupportLevel:
    method_upper = (method or "").upper()
    if coverage is None or identity is None:
        return MechanismSupportLevel.WEAK
    if "PARTIAL" in method_upper or coverage < 60.0 or identity < 80.0:
        return MechanismSupportLevel.SCREEN_ONLY
    if coverage >= 90.0 and identity >= 90.0:
        return MechanismSupportLevel.SUPPORTED
    if coverage >= 75.0 and identity >= 80.0:
        return MechanismSupportLevel.PARTIAL
    return MechanismSupportLevel.WEAK


def normalize_amrfinderplus_output(
    raw_output_path: Path,
    *,
    job_id: str,
    sample_id: str,
    target_drug: str,
    raw_artifact_id: str,
) -> list[MechanisticEvidence]:
    rows = _read_amrfinder_rows(raw_output_path)
    normalized_rows: list[MechanisticEvidence] = []

    for row_index, row in enumerate(rows):
        gene_symbol = _first_present(row, _GENE_SYMBOL_COLUMNS)
        element_name = _first_present(row, _ELEMENT_NAME_COLUMNS)
        raw_class = _first_present(row, _CLASS_COLUMNS)
        raw_subclass = _first_present(row, _SUBCLASS_COLUMNS)
        method = row.get("Method") or None
        coverage = _parse_float(_first_present(row, _COVERAGE_COLUMNS))
        identity = _parse_float(_first_present(row, _IDENTITY_COLUMNS))
        support_level = _derive_support_level(method, coverage, identity)
        drug_association = _derive_drug_association(gene_symbol, element_name, raw_class, raw_subclass)
        mechanism_class = _derive_mechanism_class(gene_symbol, element_name, raw_class, raw_subclass)
        display_name = gene_symbol or element_name or "unlabeled_element"
        coverage_text = "NA" if coverage is None else f"{coverage:.1f}%"
        identity_text = "NA" if identity is None else f"{identity:.1f}%"
        interpretation = (
            f"Detected {display_name} via {(method or 'unspecified method').lower()} "
            f"with {coverage_text} coverage and {identity_text} identity."
        )

        normalized_rows.append(
            MechanisticEvidence(
                job_id=job_id,
                sample_id=sample_id,
                target_drug=target_drug,
                source_tool="amrfinderplus",
                gene_symbol=gene_symbol,
                mutation=None,
                mechanism_class=mechanism_class,
                drug_association=drug_association,
                support_level=support_level,
                interpretation=interpretation,
                raw_row_index=row_index,
                raw_artifact_id=raw_artifact_id,
            )
        )

    return normalized_rows
