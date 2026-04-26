from __future__ import annotations

from http.client import IncompleteRead
import zipfile
from pathlib import Path

import pytest

from app.integrations import NCBIDatasetsClient
from app.integrations.ncbi_datasets import NCBIDatasetsResponse
from app.settings import IntegrationSettings, load_settings

LIVE_ACCESSION = "GCF_000005845.2"


def _live_client(tmp_path: Path) -> NCBIDatasetsClient:
    settings = load_settings()
    assert settings.integrations.ncbi_api_key, (
        "NCBI_API_KEY must be configured in the local environment for live NCBI Datasets tests"
    )
    integrations = IntegrationSettings(
        dataset_root=tmp_path / "data",
        ncbi_api_key=settings.integrations.ncbi_api_key,
        ncbi_datasets_base_url=settings.integrations.ncbi_datasets_base_url,
        live_http_timeout_seconds=settings.integrations.live_http_timeout_seconds,
        live_http_retry_count=settings.integrations.live_http_retry_count,
    )
    return NCBIDatasetsClient.from_integration_settings(integrations)


def test_fetch_assembly_report_retries_transport_body_failures(tmp_path: Path) -> None:
    attempts = {"count": 0}

    def flaky_transport(request):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise TimeoutError("read timed out")
        if attempts["count"] == 2:
            raise IncompleteRead(b'{"reports":[', 32)
        return NCBIDatasetsResponse(
            status_code=200,
            body=(
                b'{"reports":[{"accession":"GCF_000005845.2","organism":{"organism_name":"Escherichia coli"}}]}'
            ),
            content_type="application/json",
        )

    client = NCBIDatasetsClient(
        base_url="https://example.test/datasets",
        dataset_root=tmp_path / "data",
        retry_count=2,
        transport=flaky_transport,
    )

    report = client.fetch_assembly_report([LIVE_ACCESSION])

    assert attempts["count"] == 3
    assert report.accessions == (LIVE_ACCESSION,)
    assert report.reports[0]["accession"] == LIVE_ACCESSION


@pytest.mark.live
def test_live_env_loads_ncbi_api_key_without_printing_value() -> None:
    settings = load_settings()

    assert settings.integrations.ncbi_api_key
    assert settings.integrations.ncbi_datasets_base_url == (
        "https://api.ncbi.nlm.nih.gov/datasets/v2"
    )


@pytest.mark.live
def test_live_fetch_assembly_report_from_ncbi_datasets(tmp_path: Path) -> None:
    settings = load_settings()
    client = _live_client(tmp_path)
    report = client.fetch_assembly_report([LIVE_ACCESSION])

    assert client.retry_count == settings.integrations.live_http_retry_count
    assert report.accessions == (LIVE_ACCESSION,)
    assert report.reports
    assert any(LIVE_ACCESSION in str(value) for value in report.raw.values())
    assert "reports" in report.raw


@pytest.mark.live
def test_live_download_data_report_only_genome_package(tmp_path: Path) -> None:
    package = _live_client(tmp_path).download_genome_package(
        [LIVE_ACCESSION],
        include_annotation_type=(),
        hydrated="DATA_REPORT_ONLY",
        filename=f"{LIVE_ACCESSION}_data_report_only.zip",
    )

    assert package.path.exists()
    assert package.path.is_file()
    assert package.path.is_relative_to(tmp_path)
    assert package.byte_count > 0
    assert package.sha256
    assert "zip" in (package.content_type or "").lower()
    with zipfile.ZipFile(package.path) as archive:
        names = archive.namelist()
    assert any(name.endswith("dataset_catalog.json") for name in names)
    assert any("data_report" in name for name in names)
