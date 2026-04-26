from __future__ import annotations

from dataclasses import dataclass
import hashlib
from http.client import IncompleteRead
import json
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from app.settings import AppSettings, IntegrationSettings

_RETRYABLE_HTTP_STATUS_CODES = {408, 429, 500, 502, 503, 504}
_RETRYABLE_TRANSPORT_EXCEPTIONS = (IncompleteRead, TimeoutError, ConnectionError)


class NCBIDatasetsError(RuntimeError):
    """Raised when the NCBI Datasets client cannot complete a request."""


@dataclass(frozen=True)
class NCBIDatasetsRequest:
    method: str
    url: str
    headers: dict[str, str]
    body: bytes | None
    timeout_seconds: int


@dataclass(frozen=True)
class NCBIDatasetsResponse:
    status_code: int
    body: bytes
    content_type: str | None = None


@dataclass(frozen=True)
class NCBIAssemblyReport:
    accessions: tuple[str, ...]
    reports: tuple[dict[str, Any], ...]
    raw: dict[str, Any]


@dataclass(frozen=True)
class NCBIGenomePackage:
    accessions: tuple[str, ...]
    path: Path
    byte_count: int
    sha256: str
    content_type: str | None


NCBITransport = Callable[[NCBIDatasetsRequest], NCBIDatasetsResponse]


def _default_transport(request: NCBIDatasetsRequest) -> NCBIDatasetsResponse:
    urllib_request = Request(
        request.url,
        data=request.body,
        headers=request.headers,
        method=request.method,
    )
    try:
        with urlopen(urllib_request, timeout=request.timeout_seconds) as response:
            return NCBIDatasetsResponse(
                status_code=response.status,
                body=response.read(),
                content_type=response.headers.get("Content-Type"),
            )
    except HTTPError as exc:
        return NCBIDatasetsResponse(
            status_code=exc.code,
            body=exc.read(),
            content_type=exc.headers.get("Content-Type") if exc.headers else None,
        )
    except URLError as exc:
        raise NCBIDatasetsError(f"NCBI Datasets request failed: {exc.reason}") from exc


def _validate_accessions(accessions: Sequence[str]) -> tuple[str, ...]:
    normalized = tuple(accession.strip() for accession in accessions if accession.strip())
    if not normalized:
        raise ValueError("At least one assembly accession is required")
    if len(normalized) > 100:
        raise ValueError("NCBI Datasets accession requests are limited to 100 accessions")
    unsafe = [accession for accession in normalized if "/" in accession or "\\" in accession]
    if unsafe:
        raise ValueError(f"Assembly accession contains path separators: {unsafe[0]}")
    return normalized


def _accession_path(accessions: Sequence[str]) -> str:
    return ",".join(quote(accession, safe="._-") for accession in accessions)


def _safe_cache_filename(accessions: Sequence[str], *, suffix: str = ".zip") -> str:
    joined = "_".join(accessions)
    safe = "".join(char if char.isalnum() or char in "._-" else "_" for char in joined)
    return f"{safe}{suffix}"


class NCBIDatasetsClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None = None,
        dataset_root: Path,
        timeout_seconds: int = 30,
        retry_count: int = 0,
        transport: NCBITransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key.strip() if api_key else None
        self.dataset_root = dataset_root
        self.timeout_seconds = timeout_seconds
        self.retry_count = retry_count
        self._transport = transport or _default_transport

    @classmethod
    def from_settings(
        cls,
        settings: AppSettings,
        *,
        transport: NCBITransport | None = None,
    ) -> "NCBIDatasetsClient":
        return cls.from_integration_settings(settings.integrations, transport=transport)

    @classmethod
    def from_integration_settings(
        cls,
        integrations: IntegrationSettings,
        *,
        transport: NCBITransport | None = None,
    ) -> "NCBIDatasetsClient":
        return cls(
            base_url=integrations.ncbi_datasets_base_url,
            api_key=integrations.ncbi_api_key,
            dataset_root=integrations.dataset_root,
            timeout_seconds=integrations.live_http_timeout_seconds,
            retry_count=integrations.live_http_retry_count,
            transport=transport,
        )

    def fetch_assembly_report(self, accessions: Sequence[str]) -> NCBIAssemblyReport:
        normalized = _validate_accessions(accessions)
        request = self._build_request(
            "GET",
            f"/genome/accession/{_accession_path(normalized)}/dataset_report",
            accept="application/json",
        )
        response = self._send(request, endpoint_name="genome dataset report")
        try:
            payload = json.loads(response.body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise NCBIDatasetsError("NCBI Datasets returned invalid JSON") from exc

        reports = payload.get("reports", [])
        if not isinstance(reports, list):
            raise NCBIDatasetsError("NCBI Datasets dataset report did not include a reports list")
        return NCBIAssemblyReport(
            accessions=normalized,
            reports=tuple(report for report in reports if isinstance(report, dict)),
            raw=payload,
        )

    def genome_package_cache_path(
        self,
        accessions: Sequence[str],
        *,
        filename: str | None = None,
    ) -> Path:
        normalized = _validate_accessions(accessions)
        output_filename = filename or _safe_cache_filename(normalized)
        if Path(output_filename).name != output_filename:
            raise ValueError("Genome package filename must not contain path separators")
        return self.dataset_root / "downloads" / "ncbi_datasets" / output_filename

    def download_genome_package(
        self,
        accessions: Sequence[str],
        *,
        output_path: Path | None = None,
        include_annotation_type: Sequence[str] = ("GENOME_FASTA",),
        hydrated: str | None = None,
        filename: str | None = None,
    ) -> NCBIGenomePackage:
        normalized = _validate_accessions(accessions)
        package_path = output_path or self.genome_package_cache_path(
            normalized,
            filename=filename,
        )
        query_params: list[tuple[str, str]] = []
        for annotation_type in include_annotation_type:
            query_params.append(("include_annotation_type", annotation_type))
        if hydrated:
            query_params.append(("hydrated", hydrated))
        query_params.append(("filename", filename or package_path.name))

        request = self._build_request(
            "GET",
            f"/genome/accession/{_accession_path(normalized)}/download",
            accept="application/zip",
            query_params=query_params,
        )
        response = self._send(request, endpoint_name="genome package download")
        package_path.parent.mkdir(parents=True, exist_ok=True)
        package_path.write_bytes(response.body)
        return NCBIGenomePackage(
            accessions=normalized,
            path=package_path,
            byte_count=len(response.body),
            sha256=hashlib.sha256(response.body).hexdigest(),
            content_type=response.content_type,
        )

    def _build_request(
        self,
        method: str,
        path: str,
        *,
        accept: str,
        query_params: Sequence[tuple[str, str]] = (),
    ) -> NCBIDatasetsRequest:
        url = f"{self.base_url}{path}"
        if query_params:
            url = f"{url}?{urlencode(query_params, doseq=True)}"
        headers = {
            "Accept": accept,
            "User-Agent": "SafeSurveil-AIxBio/0.1",
        }
        if self.api_key:
            headers["api-key"] = self.api_key
        return NCBIDatasetsRequest(
            method=method,
            url=url,
            headers=headers,
            body=None,
            timeout_seconds=self.timeout_seconds,
        )

    def _send(
        self,
        request: NCBIDatasetsRequest,
        *,
        endpoint_name: str,
    ) -> NCBIDatasetsResponse:
        attempts = self.retry_count + 1
        last_error: NCBIDatasetsError | None = None
        for attempt_index in range(attempts):
            try:
                response = self._transport(request)
            except NCBIDatasetsError as exc:
                last_error = exc
                if attempt_index + 1 < attempts:
                    continue
                raise
            except _RETRYABLE_TRANSPORT_EXCEPTIONS as exc:
                last_error = NCBIDatasetsError(f"NCBI Datasets request failed: {exc}")
                if attempt_index + 1 < attempts:
                    continue
                raise last_error from exc
            if response.status_code >= 200 and response.status_code < 300:
                return response
            if (
                response.status_code in _RETRYABLE_HTTP_STATUS_CODES
                and attempt_index + 1 < attempts
            ):
                continue
            raise NCBIDatasetsError(
                f"NCBI Datasets {endpoint_name} failed with HTTP {response.status_code}"
            )
        if last_error is not None:
            raise last_error
        raise NCBIDatasetsError(f"NCBI Datasets {endpoint_name} request failed")
