from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

from app.contracts import ProvenanceSource, SampleInput, SampleMetadata
from app.integrations import NCBIDatasetsClient, NCBIDatasetsError, NCBIGenomePackage
from app.paths import serialize_local_path
from app.settings import AppSettings

from .retrieval import load_manifest_rows


@dataclass(frozen=True)
class LiveSamplePreparation:
    record_id: str
    sample: SampleInput
    genome_package_path: Path
    genome_package_sha256: str
    genome_package_byte_count: int
    extracted_fasta_path: Path
    extracted_fasta_sha256: str
    extracted_fasta_byte_count: int
    assembly_accession: str
    biosample_accession: str | None


def prepare_live_sample_input(
    *,
    settings: AppSettings,
    manifest_path: Path,
    record_id: str,
    output_json_path: Path | None = None,
) -> LiveSamplePreparation:
    rows = load_manifest_rows(manifest_path)
    try:
        row = next(candidate for candidate in rows if candidate["record_id"] == record_id)
    except StopIteration as exc:
        raise ValueError(f"Manifest does not include record_id {record_id}.") from exc
    return prepare_live_sample_input_from_row(
        settings=settings,
        manifest_row=row,
        output_json_path=output_json_path,
    )


def prepare_live_sample_input_from_row(
    *,
    settings: AppSettings,
    manifest_row: dict[str, str],
    output_json_path: Path | None = None,
) -> LiveSamplePreparation:
    _validate_manifest_row(manifest_row)

    assembly_accession = manifest_row["assembly_accession"].strip()
    client = NCBIDatasetsClient.from_settings(settings)
    genome_package = _download_genome_package(
        client=client,
        dataset_root=settings.integrations.dataset_root,
        assembly_accession=assembly_accession,
    )
    extracted_fasta_path = _extract_genomic_fasta(
        package=genome_package,
        dataset_root=settings.integrations.dataset_root,
        assembly_accession=assembly_accession,
    )
    assembly_report = client.fetch_assembly_report([assembly_accession])
    if not assembly_report.reports:
        raise NCBIDatasetsError(
            f"NCBI Datasets did not return a report for assembly accession {assembly_accession}."
        )

    biosample = assembly_report.reports[0].get("assembly_info", {}).get("biosample", {})
    sample = SampleInput(
        sample_id=manifest_row["sample_id"],
        organism_hint=manifest_row["organism"],
        target_drug=manifest_row["target_drug"],
        fasta_path=serialize_local_path(extracted_fasta_path, repo_root=settings.repo_root),
        metadata=SampleMetadata(
            accession=assembly_accession,
            collection_date=_parse_collection_date(biosample.get("collection_date")),
            source_context=manifest_row["source_context"],
            country=_normalize_country(biosample.get("geo_loc_name")),
            provenance_source=ProvenanceSource.NCBI,
        ),
    )

    if output_json_path is not None:
        output_json_path.parent.mkdir(parents=True, exist_ok=True)
        output_json_path.write_text(
            json.dumps(sample.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )

    return LiveSamplePreparation(
        record_id=manifest_row["record_id"],
        sample=sample,
        genome_package_path=genome_package.path,
        genome_package_sha256=genome_package.sha256,
        genome_package_byte_count=genome_package.byte_count,
        extracted_fasta_path=extracted_fasta_path,
        extracted_fasta_sha256=_sha256_for_path(extracted_fasta_path),
        extracted_fasta_byte_count=extracted_fasta_path.stat().st_size,
        assembly_accession=assembly_accession,
        biosample_accession=(
            str(biosample.get("accession")).strip() if biosample.get("accession") else None
        ),
    )


def _validate_manifest_row(row: dict[str, str]) -> None:
    required_pairs = {
        "record_id": row.get("record_id", "").strip(),
        "sample_id": row.get("sample_id", "").strip(),
        "organism": row.get("organism", "").strip(),
        "target_drug": row.get("target_drug", "").strip(),
        "assembly_accession": row.get("assembly_accession", "").strip(),
        "source_context": row.get("source_context", "").strip(),
    }
    missing = [name for name, value in required_pairs.items() if not value]
    if missing:
        raise ValueError(
            f"Manifest row is missing required live sample fields: {', '.join(sorted(missing))}."
        )
    if row.get("retrieval_status") != "ready":
        raise ValueError("Live sample preparation requires a manifest row with retrieval_status=ready.")
    if row.get("record_kind") != "public_downloaded":
        raise ValueError("Live sample preparation requires record_kind=public_downloaded.")
    if row.get("inclusion_status") != "included":
        raise ValueError("Live sample preparation requires inclusion_status=included.")


def _download_genome_package(
    *,
    client: NCBIDatasetsClient,
    dataset_root: Path,
    assembly_accession: str,
) -> NCBIGenomePackage:
    package_dir = dataset_root / "downloads" / "ncbi_datasets" / "genomes" / assembly_accession
    package_path = package_dir / f"{assembly_accession}.zip"
    return client.download_genome_package(
        [assembly_accession],
        output_path=package_path,
        include_annotation_type=("GENOME_FASTA",),
        filename=package_path.name,
    )


def _extract_genomic_fasta(
    *,
    package: NCBIGenomePackage,
    dataset_root: Path,
    assembly_accession: str,
) -> Path:
    with ZipFile(package.path) as archive:
        catalog = json.loads(archive.read("ncbi_dataset/data/dataset_catalog.json").decode("utf-8"))
        member_paths = [
            f"ncbi_dataset/data/{file_info['filePath']}"
            for entry in catalog.get("assemblies", [])
            if entry.get("accession") == assembly_accession
            for file_info in entry.get("files", [])
            if file_info.get("fileType") == "GENOMIC_NUCLEOTIDE_FASTA"
        ]
        if not member_paths:
            raise NCBIDatasetsError(
                f"Genome package {package.path.name} does not contain a GENOMIC_NUCLEOTIDE_FASTA member."
            )
        if len(member_paths) > 1:
            raise NCBIDatasetsError(
                f"Genome package {package.path.name} contains multiple GENOMIC_NUCLEOTIDE_FASTA members."
            )
        member_path = member_paths[0]
        output_dir = dataset_root / "downloads" / "fasta" / assembly_accession
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / Path(member_path).name
        output_path.write_bytes(archive.read(member_path))
        return output_path


def _parse_collection_date(value: object) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        return None


def _normalize_country(value: object) -> str | None:
    if value in (None, ""):
        return None
    raw = str(value).strip()
    if not raw:
        return None
    country = raw.split(":", 1)[0].strip()
    return country[:64] or None


def _sha256_for_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            digest.update(chunk)
    return digest.hexdigest()
