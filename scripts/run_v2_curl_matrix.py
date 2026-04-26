from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import sys
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin
from urllib.request import Request, urlopen


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ANALYZE_PAYLOAD = (
    REPO_ROOT
    / "artifacts"
    / "runs"
    / "phase6b_acceptance"
    / "latest"
    / "live_sample_input.json"
)
DEFAULT_OUTPUT_DIR = REPO_ROOT / "artifacts" / "runs" / "v2_curl_matrix"


CheckStatus = str
Invariant = Callable[[Any], str]


@dataclass
class MatrixCheck:
    name: str
    layer: str
    method: str
    path: str
    status_code: int | None
    status: CheckStatus
    detail: str
    observed: dict[str, Any]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a reproducible V2 curl-style acceptance matrix against local "
            "SafeSurveil backend routes without printing secrets."
        )
    )
    parser.add_argument("--backend-base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--frontend-base-url", default="http://127.0.0.1:4173")
    parser.add_argument("--job-id", default=None, help="Persisted job to validate.")
    parser.add_argument(
        "--analyze-payload",
        type=Path,
        default=None,
        help=(
            "Optional JSON request body for POST /jobs/analyze. Used only when "
            "--job-id is omitted. Defaults to the Phase 6B live sample input if present."
        ),
    )
    parser.add_argument(
        "--provider-proof",
        action="store_true",
        help="Also call live OpenRouter/Thesys sidecar routes. This may take time and spend provider quota.",
    )
    parser.add_argument(
        "--include-frontend-proxy",
        action="store_true",
        help="Also run read checks through the Vite /api proxy.",
    )
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for the summary JSON and Markdown report.",
    )
    return parser.parse_args()


def normalize_base_url(value: str) -> str:
    return value.rstrip("/") + "/"


def service_url(base_url: str, path: str) -> str:
    return urljoin(normalize_base_url(base_url), path.lstrip("/"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def as_mapping(payload: Any, label: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise AssertionError(f"{label} did not return a JSON object.")
    return payload


def get_nested(payload: dict[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def request_json(
    *,
    method: str,
    url: str,
    body: Any | None,
    timeout_seconds: int,
) -> tuple[int | None, Any | None, str]:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            text = response.read().decode("utf-8", errors="replace")
            parsed = json.loads(text) if text else None
            return response.status, parsed, text[:500]
    except HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(text) if text else None
        except json.JSONDecodeError:
            parsed = None
        return exc.code, parsed, text[:500]
    except (TimeoutError, URLError, OSError) as exc:
        return None, None, str(exc)[:500]
    except json.JSONDecodeError as exc:
        return None, None, f"Response was not valid JSON: {exc}"


def add_check(
    checks: list[MatrixCheck],
    *,
    name: str,
    layer: str,
    method: str,
    base_url: str,
    path: str,
    timeout_seconds: int,
    expected_status: set[int] = {200},
    body: Any | None = None,
    invariant: Invariant | None = None,
    attempts: int = 1,
) -> Any | None:
    status_code: int | None = None
    payload: Any | None = None
    snippet = ""
    detail = ""
    passed = False
    url = service_url(base_url, path)
    for attempt in range(1, attempts + 1):
        status_code, payload, snippet = request_json(
            method=method,
            url=url,
            body=body,
            timeout_seconds=timeout_seconds,
        )
        passed = status_code in expected_status
        detail = f"HTTP {status_code}; expected {sorted(expected_status)}."
        if passed and invariant is not None:
            try:
                detail = invariant(payload)
            except AssertionError as exc:
                passed = False
                detail = str(exc)
        elif not passed and snippet:
            detail = snippet
        if passed:
            if attempt > 1:
                detail = f"{detail} (attempt {attempt}/{attempts})"
            break

    checks.append(
        MatrixCheck(
            name=name,
            layer=layer,
            method=method,
            path=path,
            status_code=status_code,
            status="pass" if passed else "fail",
            detail=detail,
            observed=compact_observation(payload),
        )
    )
    return payload


def add_warn(
    checks: list[MatrixCheck],
    *,
    name: str,
    layer: str,
    detail: str,
    observed: dict[str, Any] | None = None,
) -> None:
    checks.append(
        MatrixCheck(
            name=name,
            layer=layer,
            method="N/A",
            path="N/A",
            status_code=None,
            status="warn",
            detail=detail,
            observed=observed or {},
        )
    )


def compact_observation(payload: Any | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    observed: dict[str, Any] = {}
    for key in (
        "schema_version",
        "status",
        "mode",
        "job_id",
        "sample_id",
        "target_drug",
        "gate_decision",
        "audit_fingerprint",
    ):
        value = payload.get(key)
        if value is not None:
            observed[key] = value
    runtime = payload.get("runtime")
    if isinstance(runtime, dict):
        observed["live_mode_ready"] = runtime.get("live_mode_ready")
        observed["acceptance_mode"] = runtime.get("acceptance_mode")
    output_origin = payload.get("output_origin")
    if isinstance(output_origin, dict):
        observed["output_origin_mode"] = output_origin.get("mode")
        observed["output_origin_provider"] = output_origin.get("provider")
    job_status = payload.get("job_status")
    if isinstance(job_status, dict):
        observed["job_id"] = job_status.get("job_id")
        observed["sample_id"] = job_status.get("sample_id")
        observed["target_drug"] = job_status.get("target_drug")
        observed["status"] = job_status.get("status")
    summary = payload.get("summary")
    if isinstance(summary, dict):
        observed["overall_status"] = summary.get("overall_status")
        observed["failed_checks"] = summary.get("failed_checks")
        observed["pending_checks"] = summary.get("pending_checks")
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        observed["provider_calls_triggered"] = metadata.get("provider_calls_triggered")
        observed["artifact_count"] = metadata.get("artifact_count")
        observed["execution_gate_decision"] = metadata.get("execution_gate_decision")
        observed["reasoning_trace_coverage_ratio"] = metadata.get("reasoning_trace_coverage_ratio")
        observed["evidence_graph_completeness_ratio"] = metadata.get("evidence_graph_completeness_ratio")
    return observed


def load_analyze_payload(path: Path) -> dict[str, Any]:
    payload = as_mapping(json.loads(path.read_text(encoding="utf-8")), "analyze payload")
    sample_id = str(payload.get("sample_id") or "v2_curl_matrix_sample")
    suffix = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    payload["sample_id"] = f"{sample_id}_v2curl_{suffix}"
    return payload


def extract_job_id(payload: Any) -> str:
    data = as_mapping(payload, "analyze response")
    job_id = data.get("job_id")
    if not isinstance(job_id, str) or not job_id:
        raise AssertionError("Analyze response did not include a job_id.")
    return job_id


def first_previewable_artifact(payload: Any) -> str | None:
    data = as_mapping(payload, "artifact manifest")
    artifacts = data.get("artifacts")
    if not isinstance(artifacts, list):
        return None
    for artifact in artifacts:
        if isinstance(artifact, dict) and artifact.get("preview_eligible") is True:
            artifact_id = artifact.get("artifact_id")
            if isinstance(artifact_id, str) and artifact_id:
                return artifact_id
    return None


def invariant_health(payload: Any) -> str:
    data = as_mapping(payload, "health")
    if data.get("status") != "ok":
        raise AssertionError("/health did not report status=ok.")
    runtime = as_mapping(data.get("runtime"), "health.runtime")
    if runtime.get("live_mode_ready") is not True:
        raise AssertionError("/health runtime is not live-ready.")
    return "API health is ok and live-mode-ready."


def invariant_integrations(payload: Any) -> str:
    data = as_mapping(payload, "integrations")
    if data.get("status") != "ready" or data.get("mode") != "live":
        raise AssertionError("/health/integrations is not ready in live mode.")
    secrets = as_mapping(data.get("secrets"), "integrations.secrets")
    if secrets.get("redacted") is not True or secrets.get("values_exposed") is not False:
        raise AssertionError("Integration health did not keep secrets redacted.")
    return "Integrations are ready in live mode with secrets redacted."


def invariant_queue(job_id: str | None) -> Invariant:
    def check(payload: Any) -> str:
        data = as_mapping(payload, "queue")
        items = data.get("items")
        if not isinstance(items, list) or not items:
            raise AssertionError("Queue response did not include any items.")
        if job_id and not any(isinstance(item, dict) and item.get("job_id") == job_id for item in items):
            raise AssertionError(f"Queue response did not include {job_id}.")
        return f"Queue returned {len(items)} items" + (f" including {job_id}." if job_id else ".")

    return check


def invariant_status(job_id: str) -> Invariant:
    def check(payload: Any) -> str:
        data = as_mapping(payload, "job status")
        if data.get("job_id") != job_id:
            raise AssertionError("Job status response job_id did not match.")
        if data.get("status") not in {"completed", "degraded"}:
            raise AssertionError("Job is not persisted as completed/degraded.")
        return f"Job {job_id} is persisted with status {data.get('status')}."

    return check


def invariant_decision(job_id: str) -> Invariant:
    def check(payload: Any) -> str:
        data = as_mapping(payload, "decision response")
        response_job_id = data.get("job_id") or get_nested(data, "job_status", "job_id")
        if response_job_id != job_id:
            raise AssertionError("Decision response job_id did not match.")
        decision = as_mapping(data.get("decision"), "decision")
        if decision.get("job_id") != job_id:
            raise AssertionError("Decision object job_id did not match.")
        if not isinstance(decision.get("actionability_features"), dict):
            raise AssertionError("Decision is missing actionability features.")
        if not isinstance(decision.get("triage_decision"), dict):
            raise AssertionError("Decision is missing triage fields.")
        sample_id = get_nested(decision, "sample", "sample_id") or data.get("sample_id")
        return f"Decision loaded for sample {sample_id}."

    return check


def invariant_artifacts(payload: Any) -> str:
    data = as_mapping(payload, "artifact manifest")
    artifacts = data.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise AssertionError("Artifact manifest is empty.")
    previewable = sum(
        1 for artifact in artifacts if isinstance(artifact, dict) and artifact.get("preview_eligible") is True
    )
    return f"Artifact manifest has {len(artifacts)} artifacts and {previewable} preview candidates."


def invariant_artifact_preview(artifact_id: str) -> Invariant:
    def check(payload: Any) -> str:
        data = as_mapping(payload, "artifact preview")
        if data.get("artifact_id") != artifact_id:
            raise AssertionError("Artifact preview id did not match.")
        if not isinstance(data.get("content"), str):
            raise AssertionError("Artifact preview did not return bounded content.")
        return f"Artifact preview returned bounded content for {artifact_id}."

    return check


def invariant_execution_gate(payload: Any) -> str:
    data = as_mapping(payload, "execution gate")
    if data.get("schema_version") != "v2.execution_gate.v1":
        raise AssertionError("Execution gate schema version did not match.")
    if data.get("gate_decision") not in {"allow", "review", "block"}:
        raise AssertionError("Execution gate did not return ALLOW/REVIEW/BLOCK.")
    if not str(data.get("audit_fingerprint") or "").startswith("sha256:"):
        raise AssertionError("Execution gate did not include an audit fingerprint.")
    metadata = as_mapping(data.get("metadata"), "execution gate metadata")
    if metadata.get("provider_calls_triggered") is not False:
        raise AssertionError("Execution gate unexpectedly triggered provider calls.")
    return f"Execution gate returned {data.get('gate_decision')} with an audit fingerprint."


def invariant_reasoning_trace(payload: Any) -> str:
    data = as_mapping(payload, "reasoning trace")
    coverage = as_mapping(data.get("coverage"), "reasoning trace coverage")
    ratio = coverage.get("coverage_ratio")
    if ratio != 1.0:
        raise AssertionError(f"Reasoning trace coverage is {ratio}, expected 1.0.")
    metadata = as_mapping(data.get("metadata"), "reasoning trace metadata")
    if metadata.get("provider_calls_triggered") is not False:
        raise AssertionError("Reasoning trace unexpectedly triggered provider calls.")
    return f"Reasoning trace covers {coverage.get('present_steps')}/{coverage.get('required_steps')} steps."


def invariant_evidence_graph(payload: Any) -> str:
    data = as_mapping(payload, "evidence graph")
    stats = as_mapping(data.get("stats"), "evidence graph stats")
    if stats.get("completeness_ratio") != 1.0:
        raise AssertionError("Evidence graph required-node completeness is not 1.0.")
    if stats.get("weakly_connected") is not True:
        raise AssertionError("Evidence graph is not weakly connected.")
    metadata = as_mapping(data.get("metadata"), "evidence graph metadata")
    if metadata.get("provider_calls_triggered") is not False:
        raise AssertionError("Evidence graph unexpectedly triggered provider calls.")
    return f"Evidence graph has {stats.get('node_count')} nodes and {stats.get('edge_count')} edges."


def invariant_v2_audit(payload: Any) -> str:
    data = as_mapping(payload, "v2 audit bundle")
    if data.get("schema_version") != "v2.audit_bundle.v1":
        raise AssertionError("V2 audit schema version did not match.")
    provenance = as_mapping(data.get("provenance"), "v2 audit provenance")
    if provenance.get("live_input") is not True:
        raise AssertionError("V2 audit did not report live input provenance.")
    if provenance.get("fixture_trained_baseline") is not True:
        raise AssertionError("V2 audit did not disclose the fixture-trained baseline.")
    metadata = as_mapping(data.get("metadata"), "v2 audit metadata")
    if metadata.get("provider_calls_triggered") is not False:
        raise AssertionError("V2 audit unexpectedly triggered provider calls.")
    summary = as_mapping(data.get("summary"), "v2 audit summary")
    failed = summary.get("failed_checks")
    if failed not in {0, None}:
        raise AssertionError(f"V2 audit has {failed} failed checks.")
    return (
        f"V2 audit is {summary.get('overall_status')} with "
        f"{summary.get('passing_checks')} pass, {summary.get('pending_checks')} pending."
    )


def invariant_provider_response(label: str, *, allow_cached: bool = False) -> Invariant:
    def check(payload: Any) -> str:
        data = as_mapping(payload, label)
        origin = as_mapping(data.get("output_origin"), f"{label}.output_origin")
        allowed_modes = {"live_llm"} | ({"cached"} if allow_cached else set())
        if origin.get("mode") not in allowed_modes:
            raise AssertionError(f"{label} output origin is not {sorted(allowed_modes)}.")
        copilot = data.get("copilot")
        if isinstance(copilot, dict) and copilot.get("refusal_required") is True:
            raise AssertionError(f"{label} returned a refusal.")
        return f"{label} output origin is {origin.get('mode')} via {origin.get('provider')}."

    return check


def invariant_semantic_ui(payload: Any) -> str:
    data = as_mapping(payload, "semantic ui")
    origin = as_mapping(data.get("output_origin"), "semantic ui.output_origin")
    if origin.get("mode") not in {"live_llm", "cached"}:
        raise AssertionError("Semantic UI output origin is not live/cached.")
    if data.get("semantic_ui") is None:
        raise AssertionError("Semantic UI payload is missing.")
    return f"Semantic UI returned with output origin {origin.get('mode')}."


def invariant_c1(payload: Any) -> str:
    data = as_mapping(payload, "thesys c1")
    if data.get("status") != "rendered":
        raise AssertionError(f"Thesys C1 status is {data.get('status')}, expected rendered.")
    if data.get("fallback_required") is not False:
        raise AssertionError("Thesys C1 required fallback.")
    return "Thesys C1 rendered without fallback."


def backend_read_matrix(job_id: str) -> list[tuple[str, str, Invariant, int]]:
    return [
        ("queue", "/queue", invariant_queue(job_id), 1),
        ("job_status", f"/jobs/{job_id}/status", invariant_status(job_id), 1),
        ("decision", f"/jobs/{job_id}/decision", invariant_decision(job_id), 1),
        ("verification", f"/jobs/{job_id}/verification", invariant_execution_gate, 1),
        ("reasoning_trace", f"/jobs/{job_id}/reasoning-trace", invariant_reasoning_trace, 1),
        ("evidence_graph", f"/jobs/{job_id}/evidence-graph", invariant_evidence_graph, 1),
        ("v2_audit", f"/jobs/{job_id}/v2-audit", invariant_v2_audit, 1),
    ]


def provider_proof_matrix(job_id: str) -> list[tuple[str, str, Invariant, int]]:
    question = quote("Which evidence IDs support this recommendation?")
    return [
        (
            "copilot_explanation_live",
            f"/jobs/{job_id}/copilot/explanation?refresh=true",
            invariant_provider_response("copilot explanation"),
            4,
        ),
        (
            "copilot_answer_live",
            f"/jobs/{job_id}/copilot/answer?question={question}",
            invariant_provider_response("copilot answer"),
            4,
        ),
        (
            "copilot_queue_summary_live",
            f"/jobs/{job_id}/copilot/queue-summary",
            invariant_provider_response("copilot queue summary", allow_cached=True),
            4,
        ),
        ("semantic_ui", f"/jobs/{job_id}/semantic-ui", invariant_semantic_ui, 4),
        ("semantic_ui_c1", f"/jobs/{job_id}/semantic-ui/c1", invariant_c1, 1),
    ]


def proxy_path(path: str) -> str:
    if path == "/health":
        return "/api/health"
    if path == "/health/integrations":
        return "/api/health/integrations"
    if path == "/queue":
        return "/api/queue"
    return f"/api{path}"


def markdown_report(report: dict[str, Any]) -> str:
    rows = [
        "| Layer | Check | Method | Status | Detail |",
        "| --- | --- | --- | --- | --- |",
    ]
    for check in report["checks"]:
        rows.append(
            "| {layer} | `{name}` | `{method}` | {status} | {detail} |".format(
                layer=check["layer"],
                name=check["name"],
                method=check["method"],
                status=check["status"].upper(),
                detail=str(check["detail"]).replace("\n", " "),
            )
        )
    return "\n".join(
        [
            "# V2 Curl Matrix Run",
            "",
            f"- Created at: `{report['created_at']}`",
            f"- Backend base: `{report['backend_base_url']}`",
            f"- Frontend base: `{report['frontend_base_url']}`",
            f"- Job ID: `{report['job_id']}`",
            f"- Provider proof requested: `{report['provider_proof_requested']}`",
            f"- Frontend proxy included: `{report['frontend_proxy_included']}`",
            f"- Output directory: `{report['output_dir']}`",
            f"- Passed: `{report['summary']['passed']}`",
            f"- Warnings: `{report['summary']['warned']}`",
            f"- Failed: `{report['summary']['failed']}`",
            "",
            "## Results",
            "",
            *rows,
            "",
            "## Guardrails",
            "",
            "- This report stores compact observations only, not full response bodies.",
            "- Provider routes run only when `--provider-proof` is supplied.",
            "- Read-only V2 routes must report `provider_calls_triggered=false`.",
        ]
    )


def main() -> int:
    args = parse_args()
    backend_base_url = normalize_base_url(args.backend_base_url)
    frontend_base_url = normalize_base_url(args.frontend_base_url)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_dir = args.output_dir or DEFAULT_OUTPUT_DIR / timestamp
    output_dir.mkdir(parents=True, exist_ok=True)
    checks: list[MatrixCheck] = []

    health = add_check(
        checks,
        name="health",
        layer="backend",
        method="GET",
        base_url=backend_base_url,
        path="/health",
        timeout_seconds=args.timeout_seconds,
        invariant=invariant_health,
    )
    add_check(
        checks,
        name="integrations",
        layer="backend",
        method="GET",
        base_url=backend_base_url,
        path="/health/integrations",
        timeout_seconds=args.timeout_seconds,
        invariant=invariant_integrations,
    )

    job_id = args.job_id
    if job_id is None:
        payload_path = args.analyze_payload or DEFAULT_ANALYZE_PAYLOAD
        if not payload_path.exists():
            raise SystemExit(
                "No --job-id supplied and no analyze payload is available. "
                f"Expected {payload_path} or pass --analyze-payload."
            )
        analyze_payload = load_analyze_payload(payload_path)
        analyze_response = add_check(
            checks,
            name="analyze",
            layer="backend",
            method="POST",
            base_url=backend_base_url,
            path="/jobs/analyze",
            timeout_seconds=args.timeout_seconds,
            expected_status={201},
            body=analyze_payload,
            invariant=lambda payload: f"Analyze created job {extract_job_id(payload)}.",
        )
        if isinstance(analyze_response, dict):
            job_id = extract_job_id(analyze_response)

    if not isinstance(job_id, str) or not job_id:
        raise SystemExit("No job_id is available for the V2 curl matrix.")

    if args.provider_proof:
        for name, path, invariant, attempts in provider_proof_matrix(job_id):
            add_check(
                checks,
                name=name,
                layer="backend",
                method="GET",
                base_url=backend_base_url,
                path=path,
                timeout_seconds=args.timeout_seconds,
                invariant=invariant,
                attempts=attempts,
            )

    artifact_payload = add_check(
        checks,
        name="artifacts",
        layer="backend",
        method="GET",
        base_url=backend_base_url,
        path=f"/jobs/{job_id}/artifacts",
        timeout_seconds=args.timeout_seconds,
        invariant=invariant_artifacts,
    )
    preview_id = first_previewable_artifact(artifact_payload)
    if preview_id:
        add_check(
            checks,
            name="artifact_preview",
            layer="backend",
            method="GET",
            base_url=backend_base_url,
            path=f"/jobs/{job_id}/artifacts/{preview_id}/preview?max_bytes=2048",
            timeout_seconds=args.timeout_seconds,
            invariant=invariant_artifact_preview(preview_id),
        )
    else:
        add_warn(
            checks,
            name="artifact_preview",
            layer="backend",
            detail="No previewable artifact was available; manifest route still passed.",
        )

    for name, path, invariant, attempts in backend_read_matrix(job_id):
        add_check(
            checks,
            name=name,
            layer="backend",
            method="GET",
            base_url=backend_base_url,
            path=path,
            timeout_seconds=args.timeout_seconds,
            invariant=invariant,
            attempts=attempts,
        )

    if not args.provider_proof:
        provider_ready = False
        if isinstance(health, dict):
            provider_ready = get_nested(health, "runtime", "llm_mode") == "live"
        add_warn(
            checks,
            name="provider_proof_not_requested",
            layer="providers",
            detail=(
                "OpenRouter and Thesys routes were not called. Re-run with "
                "`--provider-proof` for explicit live-provider proof."
            ),
            observed={"llm_runtime_live": provider_ready},
        )

    if args.include_frontend_proxy:
        proxy_paths = [
            ("proxy_health", "/health", invariant_health, 1),
            ("proxy_integrations", "/health/integrations", invariant_integrations, 1),
            ("proxy_queue", "/queue", invariant_queue(job_id), 1),
            ("proxy_status", f"/jobs/{job_id}/status", invariant_status(job_id), 1),
            ("proxy_decision", f"/jobs/{job_id}/decision", invariant_decision(job_id), 1),
            ("proxy_artifacts", f"/jobs/{job_id}/artifacts", invariant_artifacts, 1),
            ("proxy_verification", f"/jobs/{job_id}/verification", invariant_execution_gate, 1),
            ("proxy_reasoning_trace", f"/jobs/{job_id}/reasoning-trace", invariant_reasoning_trace, 1),
            ("proxy_evidence_graph", f"/jobs/{job_id}/evidence-graph", invariant_evidence_graph, 1),
            ("proxy_v2_audit", f"/jobs/{job_id}/v2-audit", invariant_v2_audit, 1),
        ]
        for name, path, invariant, attempts in proxy_paths:
            add_check(
                checks,
                name=name,
                layer="frontend_proxy",
                method="GET",
                base_url=frontend_base_url,
                path=proxy_path(path),
                timeout_seconds=args.timeout_seconds,
                invariant=invariant,
                attempts=attempts,
            )

    summary = {
        "total": len(checks),
        "passed": sum(check.status == "pass" for check in checks),
        "warned": sum(check.status == "warn" for check in checks),
        "failed": sum(check.status == "fail" for check in checks),
    }
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "backend_base_url": backend_base_url.rstrip("/"),
        "frontend_base_url": frontend_base_url.rstrip("/"),
        "job_id": job_id,
        "provider_proof_requested": args.provider_proof,
        "frontend_proxy_included": args.include_frontend_proxy,
        "output_dir": str(output_dir),
        "summary": summary,
        "checks": [asdict(check) for check in checks],
    }
    report_path = output_dir / "v2_curl_matrix_summary.json"
    markdown_path = output_dir / "v2_curl_matrix_report.md"
    write_json(report_path, report)
    write_text(markdown_path, markdown_report(report))
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(report_path)
    print(markdown_path)
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
