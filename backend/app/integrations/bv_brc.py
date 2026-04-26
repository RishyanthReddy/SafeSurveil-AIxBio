from __future__ import annotations

from dataclasses import dataclass
from http.client import IncompleteRead
import json
from pathlib import Path
from typing import Any, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from app.settings import AppSettings, IntegrationSettings

_RETRYABLE_HTTP_STATUS_CODES = {408, 429, 500, 502, 503, 504}
_RETRYABLE_READ_EXCEPTIONS = (IncompleteRead, TimeoutError, ConnectionError)


class BVBRCError(RuntimeError):
    """Raised when BV-BRC authentication or Data API access fails."""


@dataclass(frozen=True)
class BVBRCGenomeRecord:
    genome_id: str
    genome_name: str
    taxon_id: int | None
    raw: dict[str, Any]
    assembly_accession: str | None = None
    biosample_accession: str | None = None
    collection_date: str | None = None
    geographic_location: str | None = None
    host_name: str | None = None
    isolation_source: str | None = None


@dataclass(frozen=True)
class BVBRCAMRRecord:
    genome_id: str
    genome_name: str
    taxon_id: int | None
    antibiotic: str
    resistant_phenotype: str | None
    measurement: str | None
    laboratory_typing_method: str | None
    testing_standard: str | None
    raw: dict[str, Any]


def _safe_rql_value(value: object) -> str:
    return quote(str(value), safe="._-")


def _select_clause(fields: Sequence[str]) -> str:
    safe_fields = []
    for field in fields:
        if not field.replace("_", "").isalnum():
            raise ValueError(f"Unsafe BV-BRC field name: {field}")
        safe_fields.append(field)
    return f"select({','.join(safe_fields)})"


def _limit_clause(limit: int, offset: int) -> str:
    return f"limit({limit},{offset})"


class BVBRCClient:
    def __init__(
        self,
        *,
        auth_url: str,
        api_base_url: str,
        token_path: Path,
        username: str | None,
        password: str | None,
        username_alt: str | None = None,
        timeout_seconds: int = 30,
        retry_count: int = 0,
    ) -> None:
        self.auth_url = auth_url
        self.api_base_url = api_base_url.rstrip("/")
        self.token_path = token_path.expanduser()
        self.username = username or username_alt
        self.password = password
        self.timeout_seconds = timeout_seconds
        self.retry_count = retry_count

    @classmethod
    def from_settings(cls, settings: AppSettings) -> "BVBRCClient":
        return cls.from_integration_settings(settings.integrations)

    @classmethod
    def from_integration_settings(cls, integrations: IntegrationSettings) -> "BVBRCClient":
        return cls(
            auth_url=integrations.bv_brc_auth_url,
            api_base_url=integrations.bv_brc_api_base_url,
            token_path=integrations.bv_brc_token_path,
            username=integrations.bv_brc_username,
            username_alt=integrations.bv_brc_username_alt,
            password=integrations.bv_brc_password,
            timeout_seconds=integrations.live_http_timeout_seconds,
            retry_count=integrations.live_http_retry_count,
        )

    def authenticate(self, *, write_token: bool = True) -> str:
        if not self.username or not self.password:
            raise BVBRCError("BV-BRC username/password are not configured")
        body = urlencode({"username": self.username, "password": self.password}).encode("utf-8")
        request = Request(
            self.auth_url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "text/plain",
                "User-Agent": "SafeSurveil-AIxBio/0.1",
            },
        )
        try:
            token = self._read_response_bytes(request).decode("utf-8").strip()
        except HTTPError as exc:
            raise BVBRCError(f"BV-BRC authentication failed with HTTP {exc.code}") from exc
        except URLError as exc:
            raise BVBRCError(f"BV-BRC authentication request failed: {exc.reason}") from exc
        except _RETRYABLE_READ_EXCEPTIONS as exc:
            raise BVBRCError(f"BV-BRC authentication request failed: {exc}") from exc
        if not token:
            raise BVBRCError("BV-BRC authentication returned an empty token")
        if write_token:
            self.token_path.parent.mkdir(parents=True, exist_ok=True)
            self.token_path.write_text(token, encoding="utf-8")
        return token

    def load_token(self) -> str | None:
        if not self.token_path.exists():
            return None
        token = self.token_path.read_text(encoding="utf-8").strip()
        return token or None

    def get_token(self, *, refresh: bool = False) -> str:
        if not refresh:
            cached_token = self.load_token()
            if cached_token:
                return cached_token
        return self.authenticate(write_token=True)

    def query_genomes_by_taxon(
        self,
        *,
        taxon_id: int,
        limit: int = 1,
        offset: int = 0,
    ) -> tuple[BVBRCGenomeRecord, ...]:
        if limit <= 0:
            raise ValueError("BV-BRC genome queries require a positive limit")
        if offset < 0:
            raise ValueError("BV-BRC genome queries require a non-negative offset")
        rows = self._query_collection(
            "genome",
            [
                f"eq(taxon_id,{taxon_id})",
                _limit_clause(limit, offset),
                _select_clause(("genome_id", "genome_name", "taxon_id")),
            ],
        )
        return tuple(
            BVBRCGenomeRecord(
                genome_id=str(row.get("genome_id", "")),
                genome_name=str(row.get("genome_name", "")),
                taxon_id=_optional_int(row.get("taxon_id")),
                assembly_accession=_optional_str(row.get("assembly_accession")),
                biosample_accession=_optional_str(row.get("biosample_accession")),
                collection_date=_optional_str(row.get("collection_date")),
                geographic_location=_optional_str(row.get("geographic_location")),
                host_name=_optional_str(row.get("host_name")),
                isolation_source=_optional_str(row.get("isolation_source")),
                raw=row,
            )
            for row in rows
        )

    def query_genome_by_id(self, genome_id: str) -> BVBRCGenomeRecord | None:
        rows = self._query_collection(
            "genome",
            [
                f"eq(genome_id,{_safe_rql_value(genome_id)})",
                "limit(1)",
                _select_clause(
                    (
                        "genome_id",
                        "genome_name",
                        "taxon_id",
                        "assembly_accession",
                        "biosample_accession",
                        "collection_date",
                        "geographic_location",
                        "host_name",
                        "isolation_source",
                    )
                ),
            ],
        )
        if not rows:
            return None
        row = rows[0]
        return BVBRCGenomeRecord(
            genome_id=str(row.get("genome_id", "")),
            genome_name=str(row.get("genome_name", "")),
            taxon_id=_optional_int(row.get("taxon_id")),
            assembly_accession=_optional_str(row.get("assembly_accession")),
            biosample_accession=_optional_str(row.get("biosample_accession")),
            collection_date=_optional_str(row.get("collection_date")),
            geographic_location=_optional_str(row.get("geographic_location")),
            host_name=_optional_str(row.get("host_name")),
            isolation_source=_optional_str(row.get("isolation_source")),
            raw=row,
        )

    def query_amr_by_taxon_and_antibiotic(
        self,
        *,
        taxon_id: int,
        antibiotic: str,
        limit: int = 1,
        offset: int = 0,
    ) -> tuple[BVBRCAMRRecord, ...]:
        if limit <= 0:
            raise ValueError("BV-BRC AMR queries require a positive limit")
        if offset < 0:
            raise ValueError("BV-BRC AMR queries require a non-negative offset")
        rows = self._query_collection(
            "genome_amr",
            [
                f"and(eq(taxon_id,{taxon_id}),eq(antibiotic,{_safe_rql_value(antibiotic)}))",
                _limit_clause(limit, offset),
                _select_clause(
                    (
                        "genome_id",
                        "genome_name",
                        "taxon_id",
                        "antibiotic",
                        "resistant_phenotype",
                        "measurement",
                        "laboratory_typing_method",
                        "testing_standard",
                    )
                ),
            ],
        )
        return tuple(
            BVBRCAMRRecord(
                genome_id=str(row.get("genome_id", "")),
                genome_name=str(row.get("genome_name", "")),
                taxon_id=_optional_int(row.get("taxon_id")),
                antibiotic=str(row.get("antibiotic", "")),
                resistant_phenotype=_optional_str(row.get("resistant_phenotype")),
                measurement=_optional_str(row.get("measurement")),
                laboratory_typing_method=_optional_str(row.get("laboratory_typing_method")),
                testing_standard=_optional_str(row.get("testing_standard")),
                raw=row,
            )
            for row in rows
        )

    def _query_collection(
        self,
        collection: str,
        query_parts: Sequence[str],
    ) -> list[dict[str, Any]]:
        if not collection.replace("_", "").isalnum():
            raise ValueError(f"Unsafe BV-BRC collection name: {collection}")
        cached_token = self.load_token()
        try:
            payload = self._query_collection_payload(
                collection=collection,
                query_parts=query_parts,
                token=cached_token or self.get_token(),
            )
        except HTTPError as exc:
            if cached_token is None or not self.username or not self.password:
                raise BVBRCError(f"BV-BRC {collection} query failed with HTTP {exc.code}") from exc
            try:
                payload = self._query_collection_payload(
                    collection=collection,
                    query_parts=query_parts,
                    token=self.get_token(refresh=True),
                )
            except HTTPError as retry_exc:
                raise BVBRCError(
                    f"BV-BRC {collection} query failed with HTTP {retry_exc.code}"
                ) from retry_exc
            except URLError as retry_exc:
                raise BVBRCError(
                    f"BV-BRC {collection} query failed: {retry_exc.reason}"
                ) from retry_exc
            except _RETRYABLE_READ_EXCEPTIONS as retry_exc:
                raise BVBRCError(
                    f"BV-BRC {collection} query failed: {retry_exc}"
                ) from retry_exc
        except URLError as exc:
            raise BVBRCError(f"BV-BRC {collection} query failed: {exc.reason}") from exc
        except _RETRYABLE_READ_EXCEPTIONS as exc:
            raise BVBRCError(f"BV-BRC {collection} query failed: {exc}") from exc
        try:
            rows = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise BVBRCError(f"BV-BRC {collection} query returned invalid JSON") from exc
        if not isinstance(rows, list):
            raise BVBRCError(f"BV-BRC {collection} query did not return a JSON list")
        return [row for row in rows if isinstance(row, dict)]

    def _query_collection_payload(
        self,
        *,
        collection: str,
        query_parts: Sequence[str],
        token: str,
    ) -> str:
        query = "&".join(query_parts)
        url = f"{self.api_base_url}/{collection}/?{query}"
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "Authorization": token,
                "User-Agent": "SafeSurveil-AIxBio/0.1",
            },
        )
        return self._read_response_bytes(request).decode("utf-8")

    def _read_response_bytes(self, request: Request) -> bytes:
        attempts = self.retry_count + 1
        for attempt_index in range(attempts):
            try:
                with self._open_request(request) as response:
                    return response.read()
            except _RETRYABLE_READ_EXCEPTIONS:
                if attempt_index + 1 < attempts:
                    continue
                raise
        raise AssertionError("unreachable BV-BRC read retry loop exit")

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


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None
