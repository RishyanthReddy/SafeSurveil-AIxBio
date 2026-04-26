from __future__ import annotations

import csv
from http.client import IncompleteRead
import json
import sys
from urllib.request import Request

import pytest

from app.integrations import (
    PathogenDetectionClient,
    PathogenDetectionError,
    PathogenDetectionRecord,
)
from app.integrations.pathogen_detection import PathogenDetectionLookupResult
from app.settings import load_settings

ORGANISM_GROUP = "Escherichia_coli_Shigella"
_LATE_ACCESSION_THRESHOLD_BYTES = 10_000_000
_LATE_ACCESSION_CHUNK_BYTES = 1_000_000


def _live_client() -> PathogenDetectionClient:
    settings = load_settings()
    return PathogenDetectionClient.from_settings(settings)


def _test_record(*, assembly_accession: str = "GCA_000000001.1") -> PathogenDetectionRecord:
    return PathogenDetectionRecord(
        organism_group=ORGANISM_GROUP,
        asm_acc=assembly_accession,
        biosample_acc="SAMN00000001",
        scientific_name="Escherichia coli",
        collection_date="2026-04-21",
        geo_loc_name="USA",
        host="human",
        isolation_source="stool",
        ast_phenotypes="Resistant",
        amr_genotypes="tetA",
        source_url="https://example.test/pathogen/metadata.tsv",
        raw={"asm_acc": assembly_accession},
    )


def _discover_late_assembly_accession(client: PathogenDetectionClient) -> str:
    attempts = client.retry_count + 1
    for attempt_index in range(attempts):
        try:
            return _discover_late_assembly_accession_once(client)
        except (IncompleteRead, TimeoutError, ConnectionError):
            if attempt_index + 1 < attempts:
                continue
            raise
    raise AssertionError("unreachable Pathogen Detection late-accession retry loop exit")


def _discover_late_assembly_accession_once(client: PathogenDetectionClient) -> str:
    source_url = client.metadata_tsv_url(ORGANISM_GROUP)
    request = Request(
        source_url,
        headers={
            "Accept": "text/tab-separated-values",
            "User-Agent": "SafeSurveil-AIxBio/0.1",
        },
    )
    with client._open_request(request) as response:
        bytes_scanned = 0
        carryover = ""
        header_fields: list[str] | None = None

        while True:
            chunk_bytes = response.read(_LATE_ACCESSION_CHUNK_BYTES)
            if not chunk_bytes:
                break

            bytes_scanned += len(chunk_bytes)
            chunk_text = chunk_bytes.decode("utf-8", errors="replace")
            text = f"{carryover}{chunk_text}"
            lines = text.splitlines()

            if header_fields is None:
                if not lines:
                    carryover = text
                    continue
                header_fields = lines.pop(0).split("\t")

            if text and not text.endswith("\n"):
                if lines:
                    carryover = lines.pop()
                else:
                    carryover = text
                    continue
            else:
                carryover = ""

            if bytes_scanned < _LATE_ACCESSION_THRESHOLD_BYTES:
                continue

            reader = csv.DictReader(lines, fieldnames=header_fields, delimiter="\t")
            late_asm_acc = next(
                (
                    str(row.get("#asm_acc") or row.get("asm_acc") or "").strip()
                    for row in reader
                    if (row.get("#asm_acc") or row.get("asm_acc"))
                ),
                "",
            )
            if late_asm_acc:
                return late_asm_acc

    raise AssertionError(
        "expected a real accession beyond the default 8 MB lookup window from streamed metadata"
    )


def test_fetch_first_pathogen_detection_metadata_record_falls_back_to_stream(monkeypatch) -> None:
    client = PathogenDetectionClient(ftp_base_url="https://example.test/pathogen")
    expected = _test_record()

    def fail_prefix_lookup(*, organism_group: str, byte_limit: int, limit: int | None = None):
        raise PathogenDetectionError("HTTP 416")

    def stream_records(*, organism_group: str):
        assert organism_group == ORGANISM_GROUP
        yield expected

    monkeypatch.setattr(client, "_read_metadata_prefix_records", fail_prefix_lookup)
    monkeypatch.setattr(client, "_stream_metadata_records", stream_records)

    record = client.fetch_first_metadata_record(organism_group=ORGANISM_GROUP)

    assert record == expected


def test_find_record_by_assembly_accession_retries_with_stream_after_range_error(
    monkeypatch,
) -> None:
    client = PathogenDetectionClient(ftp_base_url="https://example.test/pathogen")
    expected = _test_record(assembly_accession="GCA_000000002.1")
    calls: list[tuple[str, int | None]] = []

    def fake_lookup(
        assembly_accession: str,
        *,
        organism_group: str,
        max_scan_bytes: int | None = None,
        chunk_size_bytes: int = 0,
        strategy: str = "range",
    ) -> PathogenDetectionLookupResult:
        assert organism_group == ORGANISM_GROUP
        calls.append((strategy, max_scan_bytes))
        if strategy == "range":
            raise PathogenDetectionError("Range not supported")
        return PathogenDetectionLookupResult(
            organism_group=organism_group,
            assembly_accession=assembly_accession,
            record=expected,
            source_url="https://example.test/pathogen/metadata.tsv",
            bytes_scanned=4_000_000,
            max_scan_bytes=max_scan_bytes or 0,
            scan_complete=False,
        )

    monkeypatch.setattr(client, "lookup_record_by_assembly_accession", fake_lookup)
    monkeypatch.setattr(
        client,
        "_metadata_total_bytes",
        lambda source_url: (_ for _ in ()).throw(PathogenDetectionError("Range not supported")),
    )
    monkeypatch.setattr(
        client,
        "metadata_tsv_url",
        lambda organism_group: "https://example.test/pathogen/metadata.tsv",
    )

    record = client.find_record_by_assembly_accession(
        expected.asm_acc or "",
        organism_group=ORGANISM_GROUP,
    )

    assert record == expected
    assert calls[0] == ("range", 8_000_000)
    assert calls[1][0] == "stream"
    assert calls[1][1] is not None and calls[1][1] > 96_000_000


def test_stream_lookup_defaults_to_effective_eof_when_total_bytes_are_unavailable(
    monkeypatch,
) -> None:
    client = PathogenDetectionClient(ftp_base_url="https://example.test/pathogen")
    expected = _test_record(assembly_accession="GCA_000000003.1")

    def fake_stream_lookup(
        *,
        assembly_accession: str,
        organism_group: str,
        max_scan_bytes: int,
        chunk_size_bytes: int,
    ) -> PathogenDetectionLookupResult:
        assert organism_group == ORGANISM_GROUP
        return PathogenDetectionLookupResult(
            organism_group=organism_group,
            assembly_accession=assembly_accession,
            record=expected,
            source_url="https://example.test/pathogen/metadata.tsv",
            bytes_scanned=4_000_000,
            max_scan_bytes=max_scan_bytes,
            scan_complete=False,
        )

    monkeypatch.setattr(
        client,
        "_metadata_total_bytes",
        lambda source_url: (_ for _ in ()).throw(PathogenDetectionError("Range not supported")),
    )
    monkeypatch.setattr(
        client,
        "metadata_tsv_url",
        lambda organism_group: "https://example.test/pathogen/metadata.tsv",
    )
    monkeypatch.setattr(
        client,
        "_stream_lookup_record_by_assembly_accession",
        fake_stream_lookup,
    )

    lookup = client.lookup_record_by_assembly_accession(
        expected.asm_acc or "",
        organism_group=ORGANISM_GROUP,
        strategy="stream",
    )

    assert lookup.record == expected
    assert lookup.max_scan_bytes > 96_000_000


def test_stream_lookup_retries_body_read_timeouts(monkeypatch) -> None:
    client = PathogenDetectionClient(ftp_base_url="https://example.test/pathogen", retry_count=2)
    expected_accession = "GCA_000000004.1"
    attempts = {"count": 0}
    payload = (
        "#asm_acc\tbiosample_acc\tscientific_name\n"
        f"{expected_accession}\tSAMN00000004\tEscherichia coli\n"
    ).encode("utf-8")

    class _Response:
        def __init__(self, outcome: object) -> None:
            self._outcome = outcome
            self.headers: dict[str, str] = {}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def read(self, byte_limit: int = -1) -> bytes:
            if isinstance(self._outcome, BaseException):
                raise self._outcome
            data = self._outcome
            if byte_limit < 0 or byte_limit >= len(data):
                self._outcome = b""
                return data
            chunk = data[:byte_limit]
            self._outcome = data[byte_limit:]
            return chunk

    outcomes: list[object] = [
        TimeoutError("read timed out"),
        payload,
    ]

    monkeypatch.setattr(
        client,
        "metadata_tsv_url",
        lambda organism_group: "https://example.test/pathogen/metadata.tsv",
    )

    def fake_open_request(request):
        attempts["count"] += 1
        return _Response(outcomes.pop(0))

    monkeypatch.setattr(client, "_open_request", fake_open_request)

    lookup = client.lookup_record_by_assembly_accession(
        expected_accession,
        organism_group=ORGANISM_GROUP,
        strategy="stream",
        max_scan_bytes=1_024,
    )

    assert attempts["count"] == 2
    assert lookup.record is not None
    assert lookup.record.asm_acc == expected_accession


def test_discover_late_accession_retries_body_read_timeouts(monkeypatch) -> None:
    client = PathogenDetectionClient(ftp_base_url="https://example.test/pathogen", retry_count=1)
    expected_accession = "GCA_000000005.1"
    attempts = {"count": 0}
    payload = (
        "#asm_acc\tbiosample_acc\tscientific_name\n"
        f"{expected_accession}\tSAMN00000005\tEscherichia coli\n"
    ).encode("utf-8")

    class _Response:
        def __init__(self, outcome: object) -> None:
            self._outcome = outcome
            self.headers: dict[str, str] = {}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def read(self, byte_limit: int = -1) -> bytes:
            if isinstance(self._outcome, BaseException):
                raise self._outcome
            data = self._outcome
            if byte_limit < 0 or byte_limit >= len(data):
                self._outcome = b""
                return data
            chunk = data[:byte_limit]
            self._outcome = data[byte_limit:]
            return chunk

    outcomes: list[object] = [
        TimeoutError("read timed out"),
        payload,
    ]

    monkeypatch.setattr(
        client,
        "metadata_tsv_url",
        lambda organism_group: "https://example.test/pathogen/metadata.tsv",
    )
    monkeypatch.setattr(sys.modules[__name__], "_LATE_ACCESSION_THRESHOLD_BYTES", 0)
    monkeypatch.setattr(sys.modules[__name__], "_LATE_ACCESSION_CHUNK_BYTES", 1_024)

    def fake_open_request(request):
        attempts["count"] += 1
        return _Response(outcomes.pop(0))

    monkeypatch.setattr(client, "_open_request", fake_open_request)

    late_accession = _discover_late_assembly_accession(client)

    assert attempts["count"] == 2
    assert late_accession == expected_accession


@pytest.mark.live
def test_live_pathogen_detection_client_uses_actual_configured_base_url() -> None:
    settings = load_settings()
    client = PathogenDetectionClient.from_settings(settings)

    assert client.ftp_base_url == settings.integrations.ncbi_pathogen_detection_base_url
    assert client.retry_count == settings.integrations.live_http_retry_count


@pytest.mark.live
def test_live_fetch_first_pathogen_detection_metadata_record() -> None:
    client = _live_client()
    record = client.fetch_first_metadata_record(organism_group=ORGANISM_GROUP)

    assert record.organism_group == ORGANISM_GROUP
    assert record.asm_acc
    assert record.biosample_acc
    assert record.scientific_name
    assert record.source_url.endswith(".metadata.tsv")
    assert record.source_url.startswith(f"{client.ftp_base_url}/Results/")
    assert "Results/Escherichia_coli_Shigella/latest_snps/Metadata/" in record.source_url


@pytest.mark.live
def test_live_lookup_pathogen_detection_record_by_assembly_accession() -> None:
    client = _live_client()
    first_record = client.fetch_first_metadata_record(organism_group=ORGANISM_GROUP)
    enrichment = client.find_record_by_assembly_accession(
        first_record.asm_acc or "",
        organism_group=ORGANISM_GROUP,
    )

    assert enrichment is not None
    serialized = json.dumps(enrichment.raw, sort_keys=True)
    assert enrichment.asm_acc == first_record.asm_acc
    assert enrichment.biosample_acc == first_record.biosample_acc
    assert enrichment.scientific_name == first_record.scientific_name
    assert "BV_BRC_PASSWORD" not in serialized


@pytest.mark.live
def test_live_lookup_pathogen_detection_record_beyond_old_prefix_limit() -> None:
    client = _live_client()
    late_asm_acc = _discover_late_assembly_accession(client)
    enrichment = client.find_record_by_assembly_accession(
        late_asm_acc,
        organism_group=ORGANISM_GROUP,
    )

    assert enrichment is not None
    assert enrichment.asm_acc == late_asm_acc


@pytest.mark.live
def test_live_stream_lookup_pathogen_detection_record_uses_deep_default_scan() -> None:
    client = _live_client()
    late_asm_acc = _discover_late_assembly_accession(client)
    lookup = client.lookup_record_by_assembly_accession(
        late_asm_acc,
        organism_group=ORGANISM_GROUP,
        strategy="stream",
    )

    assert lookup.record is not None
    assert lookup.record.asm_acc == late_asm_acc
    assert lookup.max_scan_bytes > 8_000_000


@pytest.mark.live
def test_live_stream_lookup_honors_explicit_scan_limit() -> None:
    client = _live_client()
    late_asm_acc = _discover_late_assembly_accession(client)
    explicit_limit = 1_000_000

    lookup = client.lookup_record_by_assembly_accession(
        late_asm_acc,
        organism_group=ORGANISM_GROUP,
        max_scan_bytes=explicit_limit,
        strategy="stream",
    )

    assert lookup.record is None
    assert lookup.max_scan_bytes == explicit_limit
    assert lookup.bytes_scanned == explicit_limit
    assert lookup.scan_complete is False
