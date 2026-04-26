from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.data import load_manifest_rows, run_live_manifest_retrieval
from app.data.retrieval import _resolve_planned_public_row
from app.integrations import BVBRCAMRRecord, BVBRCGenomeRecord, NCBIAssemblyReport
from app.settings import load_settings

REPO_ROOT = Path(__file__).resolve().parents[2]


class _CandidateOrderBVBRCClient:
    def __init__(self) -> None:
        self.offsets: list[int] = []

    def query_amr_by_taxon_and_antibiotic(
        self,
        *,
        taxon_id: int,
        antibiotic: str,
        limit: int = 1,
        offset: int = 0,
    ) -> tuple[BVBRCAMRRecord, ...]:
        self.offsets.append(offset)
        if offset == 0:
            return tuple(
                BVBRCAMRRecord(
                    genome_id=f"genome_unknown_{index}",
                    genome_name="unknown label candidate",
                    taxon_id=taxon_id,
                    antibiotic=antibiotic,
                    resistant_phenotype="Not reported",
                    measurement=None,
                    laboratory_typing_method=None,
                    testing_standard=None,
                    raw={"genome_id": f"genome_unknown_{index}"},
                )
                for index in range(limit)
            )
        if offset == limit:
            return (
                BVBRCAMRRecord(
                    genome_id="genome_resistant",
                    genome_name="usable label candidate",
                    taxon_id=taxon_id,
                    antibiotic=antibiotic,
                    resistant_phenotype="Resistant",
                    measurement=None,
                    laboratory_typing_method=None,
                    testing_standard=None,
                    raw={"genome_id": "genome_resistant"},
                ),
            )
        return ()

    def query_genome_by_id(self, genome_id: str) -> BVBRCGenomeRecord | None:
        return BVBRCGenomeRecord(
            genome_id=genome_id,
            genome_name=f"{genome_id} name",
            taxon_id=562,
            assembly_accession=(
                None if genome_id.startswith("genome_unknown_") else "GCF_000000002.1"
            ),
            biosample_accession=f"SAMN_{genome_id}",
            raw={"genome_id": genome_id},
        )


class _CandidateOrderNCBIClient:
    def fetch_assembly_report(self, accessions: list[str]) -> NCBIAssemblyReport:
        accession = accessions[0]
        return NCBIAssemblyReport(
            accessions=tuple(accessions),
            reports=(
                {
                    "accession": accession,
                    "assembly_info": {
                        "biosample": {
                            "accession": f"SAMN_{accession}",
                            "collection_date": "2026-04-20",
                            "geo_loc_name": "USA",
                        }
                    },
                },
            ),
            raw={"reports": []},
        )


@pytest.mark.live
def test_live_retrieval_resolves_smoke_planned_public_row(tmp_path: Path) -> None:
    output_manifest = tmp_path / "live_accession_manifest.csv"
    summary_json = tmp_path / "live_retrieval_summary.json"

    summary = run_live_manifest_retrieval(
        settings=load_settings(),
        seed_manifest_path=REPO_ROOT / "data/accessions/seed_accession_manifest.csv",
        output_manifest_path=output_manifest,
        summary_output_path=summary_json,
        record_ids={"plan_ec_tetracycline_001"},
    )

    rows = load_manifest_rows(output_manifest)
    resolved = next(row for row in rows if row["record_id"] == "plan_ec_tetracycline_001")
    written_summary = json.loads(summary_json.read_text(encoding="utf-8"))

    assert output_manifest.exists()
    assert summary_json.exists()
    assert summary["status"] == "completed"
    assert written_summary == summary
    assert resolved["record_kind"] == "public_downloaded"
    assert resolved["retrieval_status"] == "ready"
    assert resolved["inclusion_status"] == "included"
    assert resolved["label_source"] == "bv_brc_amr_metadata"
    assert resolved["assembly_accession"].startswith(("GCA_", "GCF_"))
    assert resolved["biosample_accession"].startswith("SAM")
    assert resolved["sample_id"].startswith("sample_gc")
    assert resolved["phenotype"] in {"resistant", "susceptible", "intermediate"}
    assert resolved["notes"]


def test_live_retrieval_continues_past_unusable_candidate_labels() -> None:
    normalization_rules = json.loads(
        (REPO_ROOT / "data/accessions/metadata_normalization_rules.json").read_text(
            encoding="utf-8"
        )
    )
    filtering_rules = json.loads(
        (REPO_ROOT / "data/accessions/ast_label_filtering_rules.json").read_text(
            encoding="utf-8"
        )
    )
    planned_row = next(
        row
        for row in load_manifest_rows(REPO_ROOT / "data/accessions/seed_accession_manifest.csv")
        if row["record_id"] == "plan_ec_tetracycline_001"
    )

    bv_client = _CandidateOrderBVBRCClient()
    resolved = _resolve_planned_public_row(
        row=planned_row,
        bv_client=bv_client,
        ncbi_client=_CandidateOrderNCBIClient(),
        normalization_rules=normalization_rules,
        filtering_rules=filtering_rules,
    )

    assert bv_client.offsets == [0, 50]
    assert resolved["retrieval_status"] == "ready"
    assert resolved["record_kind"] == "public_downloaded"
    assert resolved["phenotype"] == "resistant"
    assert resolved["assembly_accession"] == "GCF_000000002.1"
