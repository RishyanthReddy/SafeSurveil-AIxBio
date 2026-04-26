from __future__ import annotations

import csv
from dataclasses import dataclass
from http.client import IncompleteRead
import io
import re
from typing import Any, Iterator
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.settings import AppSettings, IntegrationSettings

DEFAULT_FTP_BASE_URL = "https://ftp.ncbi.nlm.nih.gov/pathogen"
DEFAULT_METADATA_HEADER_BYTE_LIMIT = 65_536
DEFAULT_METADATA_LOOKUP_MAX_BYTES = 8_000_000
DEFAULT_METADATA_LOOKUP_CHUNK_BYTES = 1_000_000
DEFAULT_METADATA_LOOKUP_STREAM_MAX_BYTES = 96_000_000
DEFAULT_METADATA_LOOKUP_STREAM_CHUNK_BYTES = 4_000_000
DEFAULT_METADATA_LOOKUP_STREAM_EOF_BYTES = 9_223_372_036_854_775_807
LOOKUP_STRATEGY_RANGE = "range"
LOOKUP_STRATEGY_STREAM = "stream"
_RETRYABLE_HTTP_STATUS_CODES = {408, 429, 500, 502, 503, 504}
_RETRYABLE_READ_EXCEPTIONS = (IncompleteRead, TimeoutError, ConnectionError)


class PathogenDetectionError(RuntimeError):
    """Raised when Pathogen Detection enrichment cannot be retrieved."""


@dataclass(frozen=True)
class PathogenDetectionRecord:
    organism_group: str
    asm_acc: str | None
    biosample_acc: str | None
    scientific_name: str | None
    collection_date: str | None
    geo_loc_name: str | None
    host: str | None
    isolation_source: str | None
    ast_phenotypes: str | None
    amr_genotypes: str | None
    source_url: str
    raw: dict[str, Any]


@dataclass(frozen=True)
class PathogenDetectionLookupResult:
    organism_group: str
    assembly_accession: str
    record: PathogenDetectionRecord | None
    source_url: str
    bytes_scanned: int
    max_scan_bytes: int
    scan_complete: bool


class PathogenDetectionClient:
    def __init__(
        self,
        *,
        timeout_seconds: int = 30,
        retry_count: int = 0,
        ftp_base_url: str = DEFAULT_FTP_BASE_URL,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.retry_count = retry_count
        self.ftp_base_url = ftp_base_url.rstrip("/")

    @classmethod
    def from_settings(cls, settings: AppSettings) -> "PathogenDetectionClient":
        return cls.from_integration_settings(settings.integrations)

    @classmethod
    def from_integration_settings(
        cls,
        integrations: IntegrationSettings,
    ) -> "PathogenDetectionClient":
        return cls(
            timeout_seconds=integrations.live_http_timeout_seconds,
            retry_count=integrations.live_http_retry_count,
            ftp_base_url=integrations.ncbi_pathogen_detection_base_url,
        )

    def fetch_first_metadata_record(
        self,
        *,
        organism_group: str,
    ) -> PathogenDetectionRecord:
        try:
            records = self._read_metadata_prefix_records(
                organism_group=organism_group,
                byte_limit=200_000,
                limit=1,
            )
        except PathogenDetectionError:
            record = self._read_first_stream_metadata_record(organism_group=organism_group)
            if record is not None:
                return record
            records = []
        if not records:
            raise PathogenDetectionError(
                f"No Pathogen Detection metadata rows found for {organism_group}"
            )
        return records[0]

    def find_record_by_assembly_accession(
        self,
        assembly_accession: str,
        *,
        organism_group: str,
        max_scan_bytes: int | None = None,
        chunk_size_bytes: int = DEFAULT_METADATA_LOOKUP_CHUNK_BYTES,
        strategy: str = LOOKUP_STRATEGY_RANGE,
    ) -> PathogenDetectionRecord | None:
        if max_scan_bytes is None:
            if strategy == LOOKUP_STRATEGY_STREAM:
                source_url = self.metadata_tsv_url(organism_group)
                effective_max_scan_bytes = self._default_stream_lookup_max_bytes(
                    source_url=source_url,
                    requested_max_scan_bytes=None,
                )
            else:
                effective_max_scan_bytes = DEFAULT_METADATA_LOOKUP_MAX_BYTES
        else:
            effective_max_scan_bytes = max_scan_bytes
        result: PathogenDetectionLookupResult | None = None
        range_lookup_error: PathogenDetectionError | None = None
        try:
            result = self.lookup_record_by_assembly_accession(
                assembly_accession,
                organism_group=organism_group,
                max_scan_bytes=effective_max_scan_bytes,
                chunk_size_bytes=chunk_size_bytes,
                strategy=strategy,
            )
        except PathogenDetectionError as exc:
            if strategy != LOOKUP_STRATEGY_RANGE:
                raise
            range_lookup_error = exc
        if result is not None and (result.record is not None or result.scan_complete):
            return result.record

        if strategy == LOOKUP_STRATEGY_RANGE:
            source_url = result.source_url if result is not None else self.metadata_tsv_url(organism_group)
            stream_max_scan_bytes = self._default_stream_lookup_max_bytes(
                source_url=source_url,
                requested_max_scan_bytes=max_scan_bytes,
            )
            try:
                stream_result = self.lookup_record_by_assembly_accession(
                    assembly_accession,
                    organism_group=organism_group,
                    max_scan_bytes=stream_max_scan_bytes,
                    chunk_size_bytes=max(
                        chunk_size_bytes,
                        DEFAULT_METADATA_LOOKUP_STREAM_CHUNK_BYTES,
                    ),
                    strategy=LOOKUP_STRATEGY_STREAM,
                )
            except PathogenDetectionError as exc:
                if range_lookup_error is not None:
                    raise exc from range_lookup_error
                raise
            if stream_result.record is not None or stream_result.scan_complete:
                return stream_result.record
            result = stream_result
        elif result is None and range_lookup_error is not None:
            raise range_lookup_error

        raise PathogenDetectionError(
            "Pathogen Detection metadata lookup reached the bounded scan limit "
            f"after {result.bytes_scanned} bytes for {assembly_accession} "
            f"in {organism_group}."
        )

    def _read_first_stream_metadata_record(
        self,
        *,
        organism_group: str,
    ) -> PathogenDetectionRecord | None:
        attempts = self.retry_count + 1
        for attempt_index in range(attempts):
            records = self._stream_metadata_records(organism_group=organism_group)
            try:
                return next(records, None)
            except _RETRYABLE_READ_EXCEPTIONS:
                if attempt_index + 1 < attempts:
                    continue
                raise
            finally:
                close = getattr(records, "close", None)
                if callable(close):
                    close()
        raise AssertionError("unreachable Pathogen Detection stream retry loop exit")

    def _default_stream_lookup_max_bytes(
        self,
        *,
        source_url: str,
        requested_max_scan_bytes: int | None,
    ) -> int:
        if requested_max_scan_bytes is not None:
            return requested_max_scan_bytes
        try:
            total_bytes = self._metadata_total_bytes(source_url)
        except PathogenDetectionError:
            return DEFAULT_METADATA_LOOKUP_STREAM_EOF_BYTES
        if total_bytes is None:
            return DEFAULT_METADATA_LOOKUP_STREAM_EOF_BYTES
        return max(total_bytes, DEFAULT_METADATA_LOOKUP_STREAM_MAX_BYTES)

    def lookup_record_by_assembly_accession(
        self,
        assembly_accession: str,
        *,
        organism_group: str,
        max_scan_bytes: int | None = None,
        chunk_size_bytes: int = DEFAULT_METADATA_LOOKUP_CHUNK_BYTES,
        strategy: str = LOOKUP_STRATEGY_RANGE,
    ) -> PathogenDetectionLookupResult:
        if chunk_size_bytes <= 0:
            raise ValueError("chunk_size_bytes must be a positive integer")
        if strategy not in {LOOKUP_STRATEGY_RANGE, LOOKUP_STRATEGY_STREAM}:
            raise ValueError(f"Unsupported Pathogen Detection lookup strategy: {strategy}")

        if strategy == LOOKUP_STRATEGY_STREAM:
            source_url = self.metadata_tsv_url(organism_group)
            effective_max_scan_bytes = self._default_stream_lookup_max_bytes(
                source_url=source_url,
                requested_max_scan_bytes=max_scan_bytes,
            )
            if effective_max_scan_bytes <= 0:
                raise ValueError("max_scan_bytes must be a positive integer")
            return self._stream_lookup_record_by_assembly_accession(
                assembly_accession=assembly_accession,
                organism_group=organism_group,
                max_scan_bytes=effective_max_scan_bytes,
                chunk_size_bytes=chunk_size_bytes,
            )

        effective_max_scan_bytes = (
            max_scan_bytes if max_scan_bytes is not None else DEFAULT_METADATA_LOOKUP_MAX_BYTES
        )
        if effective_max_scan_bytes <= 0:
            raise ValueError("max_scan_bytes must be a positive integer")
        source_url = self.metadata_tsv_url(organism_group)
        header_fields = self._read_metadata_header_fields(source_url)

        carryover = ""
        bytes_scanned = 0
        scan_complete = False
        range_start = 0
        while range_start < effective_max_scan_bytes:
            byte_limit = min(chunk_size_bytes, effective_max_scan_bytes - range_start)
            chunk_bytes, total_bytes = self._read_metadata_range_bytes(
                source_url=source_url,
                range_start=range_start,
                byte_limit=byte_limit,
            )
            bytes_scanned += len(chunk_bytes)
            scan_complete = _range_scan_reached_eof(
                total_bytes=total_bytes,
                range_start=range_start,
                bytes_read=len(chunk_bytes),
                requested_bytes=byte_limit,
            )
            if not chunk_bytes:
                break

            chunk_text = chunk_bytes.decode("utf-8", errors="replace")
            text = f"{carryover}{chunk_text}"
            lines = text.splitlines()
            if range_start == 0 and lines:
                lines = lines[1:]

            if scan_complete:
                carryover = ""
            elif text and not text.endswith("\n") and lines:
                carryover = lines.pop()
            elif text and not text.endswith("\n") and not lines:
                carryover = text
            else:
                carryover = ""

            for record in self._records_from_lines(
                organism_group=organism_group,
                source_url=source_url,
                header_fields=header_fields,
                lines=lines,
            ):
                if record.asm_acc == assembly_accession:
                    return PathogenDetectionLookupResult(
                        organism_group=organism_group,
                        assembly_accession=assembly_accession,
                        record=record,
                        source_url=source_url,
                        bytes_scanned=bytes_scanned,
                        max_scan_bytes=effective_max_scan_bytes,
                        scan_complete=scan_complete,
                    )

            if scan_complete:
                break
            range_start += len(chunk_bytes)

        if carryover and scan_complete:
            for record in self._records_from_lines(
                organism_group=organism_group,
                source_url=source_url,
                header_fields=header_fields,
                lines=[carryover],
            ):
                if record.asm_acc == assembly_accession:
                    return PathogenDetectionLookupResult(
                        organism_group=organism_group,
                            assembly_accession=assembly_accession,
                            record=record,
                            source_url=source_url,
                            bytes_scanned=bytes_scanned,
                            max_scan_bytes=effective_max_scan_bytes,
                            scan_complete=True,
                        )

        return PathogenDetectionLookupResult(
            organism_group=organism_group,
            assembly_accession=assembly_accession,
            record=None,
            source_url=source_url,
            bytes_scanned=bytes_scanned,
            max_scan_bytes=effective_max_scan_bytes,
            scan_complete=scan_complete,
        )

    def _stream_lookup_record_by_assembly_accession(
        self,
        *,
        assembly_accession: str,
        organism_group: str,
        max_scan_bytes: int,
        chunk_size_bytes: int,
    ) -> PathogenDetectionLookupResult:
        source_url = self.metadata_tsv_url(organism_group)
        request = Request(
            source_url,
            headers={
                "Accept": "text/tab-separated-values",
                "User-Agent": "SafeSurveil-AIxBio/0.1",
            },
        )
        try:
            attempts = self.retry_count + 1
            for attempt_index in range(attempts):
                try:
                    return self._stream_lookup_record_by_assembly_accession_once(
                        request=request,
                        source_url=source_url,
                        assembly_accession=assembly_accession,
                        organism_group=organism_group,
                        max_scan_bytes=max_scan_bytes,
                        chunk_size_bytes=chunk_size_bytes,
                    )
                except _RETRYABLE_READ_EXCEPTIONS:
                    if attempt_index + 1 < attempts:
                        continue
                    raise
        except HTTPError as exc:
            raise PathogenDetectionError(
                f"Pathogen Detection metadata fetch failed with HTTP {exc.code}"
            ) from exc
        except URLError as exc:
            raise PathogenDetectionError(
                f"Pathogen Detection metadata fetch failed: {exc.reason}"
            ) from exc

    def _stream_lookup_record_by_assembly_accession_once(
        self,
        *,
        request: Request,
        source_url: str,
        assembly_accession: str,
        organism_group: str,
        max_scan_bytes: int,
        chunk_size_bytes: int,
    ) -> PathogenDetectionLookupResult:
        with self._open_request(request) as response:
            carryover = ""
            bytes_scanned = 0
            scan_complete = False
            header_fields: list[str] | None = None

            while bytes_scanned < max_scan_bytes:
                byte_limit = min(chunk_size_bytes, max_scan_bytes - bytes_scanned)
                chunk_bytes = response.read(byte_limit)
                bytes_scanned += len(chunk_bytes)
                scan_complete = not chunk_bytes or len(chunk_bytes) < byte_limit
                if not chunk_bytes:
                    break

                chunk_text = chunk_bytes.decode("utf-8", errors="replace")
                text = f"{carryover}{chunk_text}"
                lines = text.splitlines()
                if header_fields is None:
                    if not lines:
                        carryover = text
                        continue
                    header_fields = lines.pop(0).split("\t")

                if scan_complete:
                    carryover = ""
                elif text and not text.endswith("\n") and lines:
                    carryover = lines.pop()
                elif text and not text.endswith("\n") and not lines:
                    carryover = text
                else:
                    carryover = ""

                for record in self._records_from_lines(
                    organism_group=organism_group,
                    source_url=source_url,
                    header_fields=header_fields,
                    lines=lines,
                ):
                    if record.asm_acc == assembly_accession:
                        return PathogenDetectionLookupResult(
                            organism_group=organism_group,
                            assembly_accession=assembly_accession,
                            record=record,
                            source_url=source_url,
                            bytes_scanned=bytes_scanned,
                            max_scan_bytes=max_scan_bytes,
                            scan_complete=scan_complete,
                        )

            if header_fields is None:
                raise PathogenDetectionError(
                    f"Pathogen Detection metadata header could not be read from {source_url}"
                )

            if carryover and scan_complete:
                for record in self._records_from_lines(
                    organism_group=organism_group,
                    source_url=source_url,
                    header_fields=header_fields,
                    lines=[carryover],
                ):
                    if record.asm_acc == assembly_accession:
                        return PathogenDetectionLookupResult(
                            organism_group=organism_group,
                            assembly_accession=assembly_accession,
                            record=record,
                            source_url=source_url,
                            bytes_scanned=bytes_scanned,
                            max_scan_bytes=max_scan_bytes,
                            scan_complete=True,
                        )

            return PathogenDetectionLookupResult(
                organism_group=organism_group,
                assembly_accession=assembly_accession,
                record=None,
                source_url=source_url,
                bytes_scanned=bytes_scanned,
                max_scan_bytes=max_scan_bytes,
                scan_complete=scan_complete,
            )

    def _read_metadata_prefix_records(
        self,
        *,
        organism_group: str,
        byte_limit: int,
        limit: int | None = None,
    ) -> list[PathogenDetectionRecord]:
        source_url = self.metadata_tsv_url(organism_group)
        request = Request(
            source_url,
            headers={
                "Accept": "text/tab-separated-values",
                "Range": f"bytes=0-{byte_limit - 1}",
                "User-Agent": "SafeSurveil-AIxBio/0.1",
            },
        )
        try:
            text, _ = self._read_response_bytes(request, byte_limit=byte_limit)
            decoded = text.decode("utf-8", errors="replace")
            lines = decoded.splitlines()
            if decoded and not decoded.endswith("\n") and lines:
                lines = lines[:-1]
            reader = csv.DictReader(lines, delimiter="\t")
            records: list[PathogenDetectionRecord] = []
            for row in reader:
                normalized = {key.removeprefix("#"): value for key, value in row.items()}
                records.append(
                    self._build_record(
                        organism_group=organism_group,
                        source_url=source_url,
                        normalized=normalized,
                    )
                )
                if limit is not None and len(records) >= limit:
                    break
            return records
        except HTTPError as exc:
            raise PathogenDetectionError(
                f"Pathogen Detection metadata fetch failed with HTTP {exc.code}"
            ) from exc
        except URLError as exc:
            raise PathogenDetectionError(
                f"Pathogen Detection metadata fetch failed: {exc.reason}"
            ) from exc

    def _read_metadata_header_fields(self, source_url: str) -> list[str]:
        prefix_bytes, _ = self._read_metadata_range_bytes(
            source_url=source_url,
            range_start=0,
            byte_limit=DEFAULT_METADATA_HEADER_BYTE_LIMIT,
        )
        prefix_text = prefix_bytes.decode("utf-8", errors="replace")
        header_fields = prefix_text.splitlines()[0].split("\t") if prefix_text else []
        if not header_fields:
            raise PathogenDetectionError(
                f"Pathogen Detection metadata header could not be read from {source_url}"
            )
        return header_fields

    def _metadata_total_bytes(self, source_url: str) -> int | None:
        _, total_bytes = self._read_metadata_range_bytes(
            source_url=source_url,
            range_start=0,
            byte_limit=1,
        )
        return total_bytes

    def _read_metadata_range_bytes(
        self,
        *,
        source_url: str,
        range_start: int,
        byte_limit: int,
    ) -> tuple[bytes, int | None]:
        request = Request(
            source_url,
            headers={
                "Accept": "text/tab-separated-values",
                "Range": f"bytes={range_start}-{range_start + byte_limit - 1}",
                "User-Agent": "SafeSurveil-AIxBio/0.1",
            },
        )
        try:
            data, content_range = self._read_response_bytes(request, byte_limit=byte_limit)
            return data, _content_range_total_bytes(content_range)
        except HTTPError as exc:
            raise PathogenDetectionError(
                f"Pathogen Detection metadata fetch failed with HTTP {exc.code}"
            ) from exc
        except URLError as exc:
            raise PathogenDetectionError(
                f"Pathogen Detection metadata fetch failed: {exc.reason}"
            ) from exc

    def _stream_metadata_records(
        self,
        *,
        organism_group: str,
    ) -> Iterator[PathogenDetectionRecord]:
        source_url = self.metadata_tsv_url(organism_group)
        request = Request(
            source_url,
            headers={
                "Accept": "text/tab-separated-values",
                "User-Agent": "SafeSurveil-AIxBio/0.1",
            },
        )
        try:
            with self._open_request(request) as response:
                text_stream = io.TextIOWrapper(response, encoding="utf-8", errors="replace", newline="")
                reader = csv.DictReader(text_stream, delimiter="\t")
                for row in reader:
                    yield self._build_record(
                        organism_group=organism_group,
                        source_url=source_url,
                        normalized={key.removeprefix("#"): value for key, value in row.items() if key is not None},
                    )
        except HTTPError as exc:
            raise PathogenDetectionError(
                f"Pathogen Detection metadata fetch failed with HTTP {exc.code}"
            ) from exc
        except URLError as exc:
            raise PathogenDetectionError(
                f"Pathogen Detection metadata fetch failed: {exc.reason}"
            ) from exc

    def _records_from_lines(
        self,
        *,
        organism_group: str,
        source_url: str,
        header_fields: list[str],
        lines: list[str],
    ) -> Iterator[PathogenDetectionRecord]:
        reader = csv.DictReader(lines, fieldnames=header_fields, delimiter="\t")
        for row in reader:
            yield self._build_record(
                organism_group=organism_group,
                source_url=source_url,
                normalized={
                    key.removeprefix("#"): value
                    for key, value in row.items()
                    if key is not None
                },
            )

    def _build_record(
        self,
        *,
        organism_group: str,
        source_url: str,
        normalized: dict[str, Any],
    ) -> PathogenDetectionRecord:
        return PathogenDetectionRecord(
            organism_group=organism_group,
            asm_acc=_optional_str(normalized.get("asm_acc")),
            biosample_acc=_optional_str(normalized.get("biosample_acc")),
            scientific_name=_optional_str(normalized.get("scientific_name")),
            collection_date=_optional_str(normalized.get("collection_date")),
            geo_loc_name=_optional_str(normalized.get("geo_loc_name")),
            host=_optional_str(normalized.get("host")),
            isolation_source=_optional_str(normalized.get("isolation_source")),
            ast_phenotypes=_optional_str(normalized.get("AST_phenotypes")),
            amr_genotypes=_optional_str(normalized.get("AMR_genotypes")),
            source_url=source_url,
            raw=normalized,
        )

    def metadata_tsv_url(self, organism_group: str) -> str:
        if not organism_group.replace("_", "").isalnum():
            raise ValueError(f"Unsafe Pathogen Detection organism group: {organism_group}")
        metadata_dir = f"{self.ftp_base_url}/Results/{organism_group}/latest_snps/Metadata/"
        request = Request(
            metadata_dir,
            headers={"User-Agent": "SafeSurveil-AIxBio/0.1"},
        )
        try:
            listing_bytes, _ = self._read_response_bytes(request)
            listing = listing_bytes.decode("utf-8", errors="replace")
        except HTTPError as exc:
            raise PathogenDetectionError(
                f"Pathogen Detection metadata listing failed with HTTP {exc.code}"
            ) from exc
        except URLError as exc:
            raise PathogenDetectionError(
                f"Pathogen Detection metadata listing failed: {exc.reason}"
            ) from exc

        match = re.search(r'href="([^"]+\.metadata\.tsv)"', listing)
        if not match:
            raise PathogenDetectionError(
                f"Pathogen Detection metadata listing did not include a metadata TSV for {organism_group}"
            )
        return f"{metadata_dir}{match.group(1)}"

    def _read_response_bytes(
        self,
        request: Request,
        *,
        byte_limit: int | None = None,
    ) -> tuple[bytes, str | None]:
        attempts = self.retry_count + 1
        for attempt_index in range(attempts):
            try:
                with self._open_request(request) as response:
                    data = response.read() if byte_limit is None else response.read(byte_limit)
                    return data, response.headers.get("Content-Range")
            except _RETRYABLE_READ_EXCEPTIONS:
                if attempt_index + 1 < attempts:
                    continue
                raise
        raise AssertionError("unreachable Pathogen Detection read retry loop exit")

    def _open_request(self, request: Request):
        attempts = self.retry_count + 1
        for attempt_index in range(attempts):
            try:
                return urlopen(request, timeout=self.timeout_seconds)
            except HTTPError as exc:
                if (
                    exc.code in _RETRYABLE_HTTP_STATUS_CODES
                    and attempt_index + 1 < attempts
                ):
                    continue
                raise
            except URLError:
                if attempt_index + 1 < attempts:
                    continue
                raise
        return urlopen(request, timeout=self.timeout_seconds)


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _content_range_total_bytes(content_range: str | None) -> int | None:
    if not content_range:
        return None
    match = re.fullmatch(r"bytes\s+\d+-\d+/(\d+|\*)", content_range.strip())
    if match is None:
        return None
    total_bytes = match.group(1)
    if total_bytes == "*":
        return None
    return int(total_bytes)


def _range_scan_reached_eof(
    *,
    total_bytes: int | None,
    range_start: int,
    bytes_read: int,
    requested_bytes: int,
) -> bool:
    if total_bytes is not None and range_start + bytes_read >= total_bytes:
        return True
    return bytes_read < requested_bytes
