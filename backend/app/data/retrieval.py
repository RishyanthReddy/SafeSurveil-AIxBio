from __future__ import annotations

import csv
from datetime import date
import json
from pathlib import Path
from typing import Any, Iterable

from app.integrations import BVBRCClient, NCBIDatasetsClient
from app.settings import AppSettings

ORGANISM_TAXON_MAP = {
    "e_coli": 562,
    "s_aureus": 1280,
}

_BV_BRC_AMR_PAGE_SIZE = 50


def load_manifest_rows(path: Path) -> list[dict[str, str]]:
    return list(csv.DictReader(path.read_text(encoding="utf-8").splitlines()))


def save_manifest_rows(path: Path, rows: Iterable[dict[str, str]]) -> None:
    rows = list(rows)
    if not rows:
        raise ValueError("Cannot save an empty manifest")
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def run_live_manifest_retrieval(
    *,
    settings: AppSettings,
    seed_manifest_path: Path,
    output_manifest_path: Path,
    summary_output_path: Path | None = None,
    record_ids: set[str] | None = None,
) -> dict[str, Any]:
    normalization_rules = json.loads(
        (settings.repo_root / "data/accessions/metadata_normalization_rules.json").read_text(
            encoding="utf-8"
        )
    )
    filtering_rules = json.loads(
        (settings.repo_root / "data/accessions/ast_label_filtering_rules.json").read_text(
            encoding="utf-8"
        )
    )
    bv_client = BVBRCClient.from_settings(settings)
    ncbi_client = NCBIDatasetsClient.from_settings(settings)

    input_rows = load_manifest_rows(seed_manifest_path)
    output_rows: list[dict[str, str]] = []
    resolved_count = 0
    excluded_count = 0
    for row in input_rows:
        if row["record_kind"] != "planned_public":
            output_rows.append(row)
            continue
        if record_ids and row["record_id"] not in record_ids:
            output_rows.append(row)
            continue

        resolved = _resolve_planned_public_row(
            row=row,
            bv_client=bv_client,
            ncbi_client=ncbi_client,
            normalization_rules=normalization_rules,
            filtering_rules=filtering_rules,
        )
        output_rows.append(resolved)
        if resolved["retrieval_status"] == "ready":
            resolved_count += 1
        else:
            excluded_count += 1

    save_manifest_rows(output_manifest_path, output_rows)
    summary = build_live_manifest_summary(
        output_rows=output_rows,
        seed_manifest_path=seed_manifest_path,
        output_manifest_path=output_manifest_path,
        resolved_count=resolved_count,
        excluded_count=excluded_count,
    )
    if summary_output_path is not None:
        summary_output_path.parent.mkdir(parents=True, exist_ok=True)
        summary_output_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def build_live_manifest_summary(
    *,
    output_rows: list[dict[str, str]],
    seed_manifest_path: Path,
    output_manifest_path: Path,
    resolved_count: int,
    excluded_count: int,
) -> dict[str, Any]:
    return {
        "status": "completed",
        "seed_manifest_path": str(seed_manifest_path),
        "output_manifest_path": str(output_manifest_path),
        "row_counts": {
            "total_rows": len(output_rows),
            "resolved_rows": resolved_count,
            "excluded_rows": excluded_count,
        },
        "ready_record_ids": [
            row["record_id"] for row in output_rows if row["retrieval_status"] == "ready"
        ],
    }


def _resolve_planned_public_row(
    *,
    row: dict[str, str],
    bv_client: BVBRCClient,
    ncbi_client: NCBIDatasetsClient,
    normalization_rules: dict[str, Any],
    filtering_rules: dict[str, Any],
) -> dict[str, str]:
    organism = _normalize_organism(row["organism"], normalization_rules)
    target_drug = _normalize_drug(row["target_drug"], normalization_rules)
    taxon_id = ORGANISM_TAXON_MAP[organism]
    first_excluded_candidate: dict[str, str] | None = None
    offset = 0
    while True:
        amr_rows = bv_client.query_amr_by_taxon_and_antibiotic(
            taxon_id=taxon_id,
            antibiotic=target_drug,
            limit=_BV_BRC_AMR_PAGE_SIZE,
            offset=offset,
        )
        if not amr_rows:
            break
        for amr_row in amr_rows:
            genome = bv_client.query_genome_by_id(amr_row.genome_id)
            if genome is None or not genome.assembly_accession:
                continue
            ncbi_report = ncbi_client.fetch_assembly_report([genome.assembly_accession])
            if not ncbi_report.reports:
                continue
            normalized_phenotype = _normalize_phenotype(
                amr_row.resistant_phenotype,
                normalization_rules,
            )
            resolved_row = _build_resolved_row(
                planned_row=row,
                amr_row=amr_row.raw,
                genome_row=genome.raw,
                ncbi_row=ncbi_report.reports[0],
                phenotype=normalized_phenotype,
                filtering_rules=filtering_rules,
            )
            if resolved_row["retrieval_status"] == "ready":
                return resolved_row
            if first_excluded_candidate is None:
                first_excluded_candidate = resolved_row
        if len(amr_rows) < _BV_BRC_AMR_PAGE_SIZE:
            break
        offset += len(amr_rows)

    if first_excluded_candidate is not None:
        return first_excluded_candidate

    return _build_excluded_row(
        planned_row=row,
        filtering_reason="unclear_provenance",
        notes="live retrieval found no BV-BRC/NCBI candidate with accession support",
    )


def _build_resolved_row(
    *,
    planned_row: dict[str, str],
    amr_row: dict[str, Any],
    genome_row: dict[str, Any],
    ncbi_row: dict[str, Any],
    phenotype: str,
    filtering_rules: dict[str, Any],
) -> dict[str, str]:
    assembly_accession = str(ncbi_row.get("accession") or genome_row.get("assembly_accession") or "")
    biosample = (
        ncbi_row.get("assembly_info", {})
        .get("biosample", {})
        .get("accession")
        or genome_row.get("biosample_accession")
        or ""
    )
    host = (
        ncbi_row.get("assembly_info", {})
        .get("biosample", {})
        .get("host")
        or genome_row.get("host_name")
        or ""
    )
    isolation_source = (
        ncbi_row.get("assembly_info", {})
        .get("biosample", {})
        .get("isolation_source")
        or genome_row.get("isolation_source")
        or ""
    )
    collection_date = (
        ncbi_row.get("assembly_info", {})
        .get("biosample", {})
        .get("collection_date")
        or genome_row.get("collection_date")
        or ""
    )
    country = (
        ncbi_row.get("assembly_info", {})
        .get("biosample", {})
        .get("geo_loc_name")
        or genome_row.get("geographic_location")
        or ""
    )
    inclusion_ready = phenotype in filtering_rules["allowed_primary_labels"]
    filtering_reason = "" if inclusion_ready else "unknown_label"
    return {
        "record_id": planned_row["record_id"],
        "record_kind": "public_downloaded" if inclusion_ready else "public_excluded",
        "sample_id": _sample_id_from_accession(assembly_accession),
        "organism": planned_row["organism"],
        "target_drug": planned_row["target_drug"],
        "source_database": "bv_brc_ncbi_datasets",
        "source_record_id": f"bv_brc_genome:{amr_row.get('genome_id', '')}",
        "assembly_accession": assembly_accession,
        "biosample_accession": str(biosample),
        "retrieval_status": "ready" if inclusion_ready else "excluded",
        "retrieval_date": date.today().isoformat(),
        "label_source": "bv_brc_amr_metadata",
        "phenotype": phenotype if inclusion_ready else "unknown",
        "source_context": "agricultural_surveillance_proxy",
        "inclusion_status": "included" if inclusion_ready else "excluded",
        "inclusion_reason": (
            "live_public_bv_brc_ncbi_verified" if inclusion_ready else planned_row["inclusion_reason"]
        ),
        "filtering_reason": filtering_reason,
        "notes": (
            f"live retrieval resolved via BV-BRC genome {amr_row.get('genome_id', '')}; "
            f"host={host or 'unknown'}; isolation_source={isolation_source or 'unknown'}; "
            f"collection_date={collection_date or 'unknown'}; geo_loc_name={country or 'unknown'}"
        ),
    }


def _build_excluded_row(
    *,
    planned_row: dict[str, str],
    filtering_reason: str,
    notes: str,
) -> dict[str, str]:
    return {
        **planned_row,
        "record_kind": "public_excluded",
        "retrieval_status": "excluded",
        "retrieval_date": date.today().isoformat(),
        "inclusion_status": "excluded",
        "filtering_reason": filtering_reason,
        "notes": notes,
    }


def _normalize_organism(value: str, normalization_rules: dict[str, Any]) -> str:
    normalized = value.strip().lower().replace(" ", "_")
    return normalization_rules["organism_aliases"].get(normalized, normalized)


def _normalize_drug(value: str, normalization_rules: dict[str, Any]) -> str:
    normalized = value.strip().lower().replace(" ", "_")
    return normalization_rules["drug_aliases"].get(normalized, normalized)


def _normalize_phenotype(value: str | None, normalization_rules: dict[str, Any]) -> str:
    if value is None:
        return "unknown"
    normalized = value.strip().lower()
    if normalized in normalization_rules["missing_value_markers"]:
        return "unknown"
    if normalized in normalization_rules["ambiguous_phenotype_terms"]:
        return "unknown"
    return normalization_rules["phenotype_aliases"].get(normalized, "unknown")


def _sample_id_from_accession(accession: str) -> str:
    return f"sample_{accession.lower().replace('.', '_')}"
