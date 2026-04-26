from __future__ import annotations

from http.client import IncompleteRead
import json
from pathlib import Path

import pytest

from app.integrations import BVBRCClient
from app.integrations.bv_brc import _limit_clause
from app.settings import load_settings


def test_bv_brc_pagination_uses_limit_offset_clause() -> None:
    assert _limit_clause(50, 0) == "limit(50,0)"
    assert _limit_clause(50, 50) == "limit(50,50)"


def _live_client() -> BVBRCClient:
    settings = load_settings()
    assert settings.integrations.bv_brc_password, (
        "BV_BRC_PASSWORD must be configured in the local environment for live BV-BRC tests"
    )
    assert (
        settings.integrations.bv_brc_username
        or settings.integrations.bv_brc_username_alt
    ), "BV_BRC_USERNAME or BV_BRC_USERNAME_ALT must be configured in the local environment"
    return BVBRCClient.from_settings(settings)


def test_query_genomes_retries_response_body_failures(tmp_path: Path, monkeypatch) -> None:
    outcomes: list[object] = [
        TimeoutError("read timed out"),
        IncompleteRead(b'[{"genome_id":"g1"', 32),
        b'[{"genome_id":"g1","genome_name":"usable candidate","taxon_id":562}]',
    ]
    attempts = {"count": 0}

    class _Response:
        def __init__(self, outcome: object) -> None:
            self._outcome = outcome

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def read(self) -> bytes:
            if isinstance(self._outcome, BaseException):
                raise self._outcome
            return self._outcome

    client = BVBRCClient(
        auth_url="https://example.test/auth",
        api_base_url="https://example.test/api",
        token_path=tmp_path / ".patric_token",
        username="user",
        password="password",
        retry_count=2,
    )
    monkeypatch.setattr(client, "load_token", lambda: "cached-token")

    def fake_open_request(request):
        attempts["count"] += 1
        return _Response(outcomes.pop(0))

    monkeypatch.setattr(client, "_open_request", fake_open_request)

    records = client.query_genomes_by_taxon(taxon_id=562, limit=1)

    assert attempts["count"] == 3
    assert len(records) == 1
    assert records[0].genome_id == "g1"


@pytest.mark.live
def test_live_authenticate_to_bv_brc_and_write_token_file() -> None:
    settings = load_settings()
    client = _live_client()

    token = client.authenticate(write_token=True)

    assert client.retry_count == settings.integrations.live_http_retry_count
    assert token
    assert len(token) > 40
    assert client.token_path.exists()
    assert client.load_token() == token


@pytest.mark.live
def test_live_query_bv_brc_genomes_by_taxon() -> None:
    records = _live_client().query_genomes_by_taxon(taxon_id=562, limit=1)

    assert records
    record = records[0]
    assert record.genome_id
    assert record.genome_name
    assert record.taxon_id == 562


@pytest.mark.live
def test_live_query_bv_brc_amr_metadata_for_ecoli_tetracycline() -> None:
    records = _live_client().query_amr_by_taxon_and_antibiotic(
        taxon_id=562,
        antibiotic="tetracycline",
        limit=1,
    )

    assert records
    record = records[0]
    serialized = json.dumps(record.raw, sort_keys=True)
    settings = load_settings()
    assert record.genome_id
    assert record.genome_name
    assert record.taxon_id == 562
    assert record.antibiotic.lower() == "tetracycline"
    assert record.resistant_phenotype in {"Resistant", "Susceptible", "Intermediate"}
    assert settings.integrations.bv_brc_password not in serialized


@pytest.mark.live
def test_live_query_bv_brc_recovers_from_stale_cached_token(tmp_path: Path) -> None:
    settings = load_settings()
    token_path = tmp_path / ".patric_token"
    token_path.write_text("invalid-token", encoding="utf-8")
    client = BVBRCClient(
        auth_url=settings.integrations.bv_brc_auth_url,
        api_base_url=settings.integrations.bv_brc_api_base_url,
        token_path=token_path,
        username=settings.integrations.bv_brc_username,
        username_alt=settings.integrations.bv_brc_username_alt,
        password=settings.integrations.bv_brc_password,
        timeout_seconds=settings.integrations.live_http_timeout_seconds,
        retry_count=settings.integrations.live_http_retry_count,
    )

    records = client.query_genomes_by_taxon(taxon_id=562, limit=1)

    assert records
    refreshed_token = client.load_token()
    assert refreshed_token
    assert refreshed_token != "invalid-token"
