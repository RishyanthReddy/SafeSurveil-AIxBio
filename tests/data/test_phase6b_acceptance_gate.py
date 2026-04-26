from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.integrations import (
    BVBRCAMRRecord,
    BVBRCGenomeRecord,
    PathogenDetectionRecord,
)
from app.integrations.pathogen_detection import PathogenDetectionLookupResult
import app.services.phase6b_acceptance as phase6b_acceptance
from app.services import build_phase6b_acceptance_report
from app.settings import load_settings

REPO_ROOT = Path(__file__).resolve().parents[2]


class _ProbeCandidateOrderBVBRCClient:
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
                    genome_id=f"genome_missing_{index}",
                    genome_name="missing assembly candidate",
                    taxon_id=taxon_id,
                    antibiotic=antibiotic,
                    resistant_phenotype="Resistant",
                    measurement=None,
                    laboratory_typing_method=None,
                    testing_standard=None,
                    raw={"genome_id": f"genome_missing_{index}"},
                )
                for index in range(limit)
            )
        if offset == limit:
            return (
                BVBRCAMRRecord(
                    genome_id="genome_ready",
                    genome_name="usable candidate",
                    taxon_id=taxon_id,
                    antibiotic=antibiotic,
                    resistant_phenotype="Susceptible",
                    measurement=None,
                    laboratory_typing_method=None,
                    testing_standard=None,
                    raw={"genome_id": "genome_ready"},
                ),
            )
        return ()

    def query_genome_by_id(self, genome_id: str) -> BVBRCGenomeRecord | None:
        if genome_id.startswith("genome_missing_"):
            return None
        return BVBRCGenomeRecord(
            genome_id=genome_id,
            genome_name="usable candidate",
            taxon_id=562,
            assembly_accession="GCF_000000123.1",
            biosample_accession="SAMN000000123",
            raw={"genome_id": genome_id},
        )


@pytest.mark.live
def test_phase6b_acceptance_gate_uses_actual_environment(tmp_path: Path) -> None:
    matrix = json.loads(
        (REPO_ROOT / "docs/data/PHASE_6B_ACCEPTANCE_MATRIX.json").read_text(encoding="utf-8")
    )
    settings = load_settings()
    output_path = tmp_path / "phase6b_acceptance_report.json"

    report = build_phase6b_acceptance_report(
        settings=settings,
        work_root=tmp_path / "phase6b_acceptance",
        output_path=output_path,
    )
    written = json.loads(output_path.read_text(encoding="utf-8"))
    areas = {area["name"]: area for area in report["areas"]}
    serialized = json.dumps(report, sort_keys=True)

    assert matrix["matrix_version"] == "0.1.0"
    assert any("Phase 7" in item for item in matrix["pass_criteria"])
    assert "Phase 7" in matrix["next_phase_gate"]
    assert output_path.exists()
    assert written == report
    assert set(areas) == {
        "secrets",
        "ncbi_datasets",
        "bv_brc",
        "pathogen_detection",
        "retrieval",
        "live_sample_input",
        "amrfinderplus",
        "mash",
        "persistence",
    }
    assert report["phase7_gate"]["status"] in {"ready", "blocked"}
    assert report["phase7_gate"]["blocking_areas"] == sorted(
        name for name, area in areas.items() if area["status"] != "live"
    )
    assert report["integration_health"]["status"] in {"ready", "degraded", "fixture"}
    if settings.integrations.ncbi_api_key:
        assert settings.integrations.ncbi_api_key not in serialized
    if settings.integrations.bv_brc_password:
        assert settings.integrations.bv_brc_password not in serialized

    assert areas["secrets"]["status"] == "live"
    assert areas["ncbi_datasets"]["status"] == "live"
    assert areas["bv_brc"]["status"] == "live"
    assert areas["pathogen_detection"]["status"] in {"live", "degraded"}
    assert areas["retrieval"]["status"] == "live"
    assert areas["live_sample_input"]["status"] == "live"

    pathogen_evidence = "\n".join(areas["pathogen_detection"]["evidence"])
    assert "requested_assembly_accession=" in pathogen_evidence
    assert "organism_group=" in pathogen_evidence
    assert "lookup_strategy=stream" in pathogen_evidence
    if areas["pathogen_detection"]["status"] == "live":
        assert (
            "verified_assembly_accession=" in pathogen_evidence
            or "record_found=false" in pathogen_evidence
        )
    else:
        assert "max_scan_bytes=" in pathogen_evidence

    amrfinder_runtime = report["integration_health"]["tools"]["amrfinderplus"]["runtime_status"]
    mash_runtime = report["integration_health"]["tools"]["mash"]["runtime_status"]

    if amrfinder_runtime == "ready":
        assert areas["amrfinderplus"]["status"] == "live"
    else:
        assert areas["amrfinderplus"]["status"] == "unavailable"

    if mash_runtime == "ready":
        assert areas["mash"]["status"] == "live"
    else:
        assert areas["mash"]["status"] == "unavailable"

    if settings.use_fixtures:
        assert areas["persistence"]["status"] == "fixture"
        assert report["phase7_gate"]["status"] == "blocked"
    elif amrfinder_runtime == "ready" and mash_runtime == "ready":
        assert areas["persistence"]["status"] == "live"
        if all(area["status"] == "live" for area in areas.values()):
            assert report["phase7_gate"] == {
                "status": "ready",
                "can_begin": True,
                "blocking_areas": [],
                "summary": "Phase 7 may begin because the Phase 6B live data, tool, retrieval, and persistence gates are all satisfied.",
            }
        else:
            assert report["phase7_gate"]["status"] == "blocked"
    else:
        assert areas["persistence"]["status"] == "blocked"
        assert report["phase7_gate"]["status"] == "blocked"


def test_phase6b_acceptance_bv_brc_probe_skips_unusable_first_candidate(monkeypatch) -> None:
    probe_client = _ProbeCandidateOrderBVBRCClient()

    class _ProbeClientFactory:
        @staticmethod
        def from_settings(settings):
            return probe_client

    monkeypatch.setattr(phase6b_acceptance, "BVBRCClient", _ProbeClientFactory)

    area, error = phase6b_acceptance._probe_bv_brc(load_settings())

    assert error is None
    assert probe_client.offsets == [0, 50]
    assert area["status"] == "live"
    evidence = "\n".join(area["evidence"])
    assert "genome_id=genome_ready" in evidence
    assert "assembly_accession=GCF_000000123.1" in evidence


def test_phase6b_acceptance_pathogen_probe_uses_stream_default_scan_limit(monkeypatch) -> None:
    captured: dict[str, object] = {}
    expected_accession = "GCA_000000999.1"

    class _ProbePathogenClient:
        def lookup_record_by_assembly_accession(
            self,
            assembly_accession: str,
            *,
            organism_group: str,
            max_scan_bytes: int | None = None,
            chunk_size_bytes: int,
            strategy: str,
        ) -> PathogenDetectionLookupResult:
            captured["assembly_accession"] = assembly_accession
            captured["organism_group"] = organism_group
            captured["max_scan_bytes"] = max_scan_bytes
            captured["chunk_size_bytes"] = chunk_size_bytes
            captured["strategy"] = strategy
            return PathogenDetectionLookupResult(
                organism_group=organism_group,
                assembly_accession=assembly_accession,
                record=PathogenDetectionRecord(
                    organism_group=organism_group,
                    asm_acc=assembly_accession,
                    biosample_acc="SAMN000000999",
                    scientific_name="Escherichia coli",
                    collection_date=None,
                    geo_loc_name=None,
                    host=None,
                    isolation_source=None,
                    ast_phenotypes=None,
                    amr_genotypes=None,
                    source_url="https://example.test/pathogen/metadata.tsv",
                    raw={"asm_acc": assembly_accession},
                ),
                source_url="https://example.test/pathogen/metadata.tsv",
                bytes_scanned=4_000_000,
                max_scan_bytes=9_223_372_036_854_775_807,
                scan_complete=False,
            )

    class _ProbeClientFactory:
        @staticmethod
        def from_settings(settings):
            return _ProbePathogenClient()

    monkeypatch.setattr(phase6b_acceptance, "PathogenDetectionClient", _ProbeClientFactory)

    area = phase6b_acceptance._probe_pathogen_detection(
        load_settings(),
        assembly_accession=expected_accession,
    )

    assert area["status"] == "live"
    assert captured == {
        "assembly_accession": expected_accession,
        "organism_group": phase6b_acceptance.PHASE6B_SMOKE_ORGANISM_GROUP,
        "max_scan_bytes": None,
        "chunk_size_bytes": phase6b_acceptance.PHASE6B_PATHOGEN_DETECTION_CHUNK_BYTES,
        "strategy": "stream",
    }
