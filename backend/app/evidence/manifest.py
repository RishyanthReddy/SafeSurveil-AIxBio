from __future__ import annotations

import hashlib
from pathlib import Path

from app.contracts import ArtifactKind, ArtifactManifest, ArtifactRecord, SampleInput
from app.paths import display_path, resolve_local_path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _sha256_for_path(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_record(
    *,
    artifact_id: str,
    job_id: str,
    sample_id: str,
    target_drug: str,
    kind: ArtifactKind,
    path: Path,
    media_type: str,
    generated_by: str,
    repo_root: Path,
) -> ArtifactRecord:
    return ArtifactRecord(
        artifact_id=artifact_id,
        job_id=job_id,
        sample_id=sample_id,
        target_drug=target_drug,
        kind=kind,
        path=display_path(path, repo_root=repo_root),
        media_type=media_type,
        generated_by=generated_by,
        sha256=_sha256_for_path(path),
        size_bytes=path.stat().st_size if path.exists() and path.is_file() else None,
    )


def build_evidence_artifact_manifest(
    *,
    sample: SampleInput,
    job_id: str,
    artifact_root: Path,
    qc_json_path: Path,
    amrfinder_raw_path: Path,
    amrfinder_runtime_json_path: Path | None = None,
    mechanistic_json_path: Path,
    mash_raw_path: Path,
    mash_runtime_json_path: Path | None = None,
    novelty_json_path: Path,
    repo_root: Path | None = None,
) -> ArtifactManifest:
    root = repo_root or _repo_root()
    if sample.fasta_path is None:
        raise ValueError("Artifact manifesting requires a local fasta_path.")

    input_fasta_path = resolve_local_path(sample.fasta_path, repo_root=root)
    records = [
        _artifact_record(
            artifact_id=f"{job_id}_input_fasta",
            job_id=job_id,
            sample_id=sample.sample_id,
            target_drug=sample.target_drug,
            kind=ArtifactKind.INPUT_FASTA,
            path=input_fasta_path,
            media_type="text/plain",
            generated_by="evidence_smoke_runner",
            repo_root=root,
        ),
        _artifact_record(
            artifact_id=f"{job_id}_qc_json",
            job_id=job_id,
            sample_id=sample.sample_id,
            target_drug=sample.target_drug,
            kind=ArtifactKind.OTHER,
            path=qc_json_path,
            media_type="application/json",
            generated_by="validation",
            repo_root=root,
        ),
        _artifact_record(
            artifact_id=f"{job_id}_amrfinder_raw",
            job_id=job_id,
            sample_id=sample.sample_id,
            target_drug=sample.target_drug,
            kind=ArtifactKind.MECHANISTIC_EVIDENCE,
            path=amrfinder_raw_path,
            media_type="text/tab-separated-values",
            generated_by="amrfinderplus",
            repo_root=root,
        ),
        _artifact_record(
            artifact_id=f"{job_id}_mechanistic_json",
            job_id=job_id,
            sample_id=sample.sample_id,
            target_drug=sample.target_drug,
            kind=ArtifactKind.MECHANISTIC_EVIDENCE,
            path=mechanistic_json_path,
            media_type="application/json",
            generated_by="amrfinder_normalizer",
            repo_root=root,
        ),
        _artifact_record(
            artifact_id=f"{job_id}_mash_raw",
            job_id=job_id,
            sample_id=sample.sample_id,
            target_drug=sample.target_drug,
            kind=ArtifactKind.NOVELTY_SUMMARY,
            path=mash_raw_path,
            media_type="text/tab-separated-values",
            generated_by="mash",
            repo_root=root,
        ),
        _artifact_record(
            artifact_id=f"{job_id}_novelty_json",
            job_id=job_id,
            sample_id=sample.sample_id,
            target_drug=sample.target_drug,
            kind=ArtifactKind.NOVELTY_SUMMARY,
            path=novelty_json_path,
            media_type="application/json",
            generated_by="mash_parser",
            repo_root=root,
        ),
    ]
    if amrfinder_runtime_json_path is not None:
        records.append(
            _artifact_record(
                artifact_id=f"{job_id}_amrfinder_runtime_json",
                job_id=job_id,
                sample_id=sample.sample_id,
                target_drug=sample.target_drug,
                kind=ArtifactKind.OTHER,
                path=amrfinder_runtime_json_path,
                media_type="application/json",
                generated_by="amrfinderplus",
                repo_root=root,
            )
        )
    if mash_runtime_json_path is not None:
        records.append(
            _artifact_record(
                artifact_id=f"{job_id}_mash_runtime_json",
                job_id=job_id,
                sample_id=sample.sample_id,
                target_drug=sample.target_drug,
                kind=ArtifactKind.OTHER,
                path=mash_runtime_json_path,
                media_type="application/json",
                generated_by="mash",
                repo_root=root,
            )
        )

    return ArtifactManifest(
        job_id=job_id,
        sample_id=sample.sample_id,
        target_drug=sample.target_drug,
        artifact_root=display_path(artifact_root, repo_root=root),
        artifacts=records,
    )
