from __future__ import annotations

import json
from pathlib import Path

from app.contracts import AssemblyQC, OrganismConsistency, QCStatus, SampleInput
from app.paths import resolve_local_path

DEFAULT_MAX_FASTA_BYTES = 5 * 1024 * 1024
ALLOWED_FASTA_SUFFIXES = {".fa", ".fasta", ".fna"}
_MISSING_METADATA_FIELDS = ("collection_date", "country", "accession")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _load_dataset_scope(repo_root: Path | None = None) -> dict:
    root = repo_root or _repo_root()
    scope_path = root / "data/accessions/dataset_scope.json"
    return json.loads(scope_path.read_text(encoding="utf-8"))


def _allowed_drugs(scope: dict, organism_hint: str | None) -> set[str]:
    allowed = set(scope.get("cross_organism_shared_drugs", []))
    if organism_hint is None:
        return allowed

    organism_config = scope.get("drug_panel", {}).get(organism_hint, {})
    for bucket in ("locked_primary", "candidate_expansion", "fallback_if_labels_sparse"):
        allowed.update(organism_config.get(bucket, []))
    return allowed


def _organism_hint_value(sample: SampleInput) -> str | None:
    if sample.organism_hint is None:
        return None
    return sample.organism_hint.value


def _parse_fasta(fasta_path: Path, max_fasta_bytes: int) -> tuple[int, int, float, list[str], bool]:
    warnings: list[str] = []
    if fasta_path.suffix.lower() not in ALLOWED_FASTA_SUFFIXES:
        warnings.append("Unsupported FASTA extension.")
        return 0, 0, 0.0, warnings, False
    if not fasta_path.exists() or not fasta_path.is_file():
        warnings.append("FASTA input is missing or unreadable.")
        return 0, 0, 0.0, warnings, False

    file_size = fasta_path.stat().st_size
    if file_size == 0:
        warnings.append("FASTA input is empty.")
        return 0, 0, 0.0, warnings, False
    if file_size > max_fasta_bytes:
        warnings.append("FASTA input exceeds the configured size limit.")
        return 0, 0, 0.0, warnings, False

    sequence_count = 0
    total_bases = 0
    ambiguous_bases = 0
    current_sequence_started = False
    allowed_bases = {"A", "C", "G", "T", "N"}

    for raw_line in fasta_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(">"):
            sequence_count += 1
            current_sequence_started = True
            continue
        if not current_sequence_started:
            warnings.append("FASTA sequence content must follow a header line.")
            return 0, 0, 0.0, warnings, False
        upper_line = line.upper()
        if any(base not in allowed_bases for base in upper_line):
            warnings.append("FASTA sequence contains unsupported characters.")
            return 0, 0, 0.0, warnings, False
        total_bases += len(upper_line)
        ambiguous_bases += upper_line.count("N")

    if sequence_count == 0 or total_bases == 0:
        warnings.append("FASTA input must contain at least one sequence with bases.")
        return 0, 0, 0.0, warnings, False

    ambiguous_fraction = ambiguous_bases / total_bases
    if ambiguous_fraction > 0.02:
        warnings.append("FASTA contains elevated ambiguous base content.")
    return sequence_count, total_bases, ambiguous_fraction, warnings, True


def validate_sample_for_evidence(
    sample: SampleInput,
    *,
    repo_root: Path | None = None,
    job_id: str = "job_evidence_validation",
    max_fasta_bytes: int = DEFAULT_MAX_FASTA_BYTES,
) -> AssemblyQC:
    scope = _load_dataset_scope(repo_root=repo_root)
    warnings: list[str] = []
    missing_metadata_fields = [
        field_name
        for field_name in _MISSING_METADATA_FIELDS
        if getattr(sample.metadata, field_name) in (None, "")
    ]

    allowed_drug_values = _allowed_drugs(scope, _organism_hint_value(sample))
    file_valid = True
    sequence_count = 0
    total_bases = 0
    ambiguous_fraction = 0.0

    if sample.target_drug not in allowed_drug_values:
        file_valid = False
        warnings.append("Target drug is outside the locked MVP scope.")

    if sample.fasta_path is None:
        file_valid = False
        warnings.append("Local evidence validation requires a readable FASTA path.")
    else:
        fasta_path = resolve_local_path(sample.fasta_path, repo_root=repo_root or _repo_root())
        sequence_count, total_bases, ambiguous_fraction, fasta_warnings, fasta_valid = _parse_fasta(
            fasta_path,
            max_fasta_bytes=max_fasta_bytes,
        )
        warnings.extend(fasta_warnings)
        file_valid = file_valid and fasta_valid

    qc_status = QCStatus.PASS
    if not file_valid:
        qc_status = QCStatus.FAIL
    elif missing_metadata_fields or warnings:
        qc_status = QCStatus.WARN

    return AssemblyQC(
        job_id=job_id,
        sample_id=sample.sample_id,
        target_drug=sample.target_drug,
        file_valid=file_valid,
        sequence_count=sequence_count,
        total_bases=total_bases,
        ambiguous_base_fraction=ambiguous_fraction,
        organism_consistency=OrganismConsistency.UNKNOWN,
        missing_metadata_fields=missing_metadata_fields,
        qc_status=qc_status,
        warnings=warnings,
    )
