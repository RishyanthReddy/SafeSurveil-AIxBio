from __future__ import annotations

import argparse
from dataclasses import dataclass, asdict
from datetime import UTC, datetime
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin
from urllib.request import Request, urlopen


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SAMPLE_INPUT = (
    REPO_ROOT
    / "artifacts"
    / "runs"
    / "phase6b_acceptance"
    / "latest"
    / "live_sample_input.json"
)


@dataclass
class MatrixCheck:
    name: str
    layer: str
    method: str
    url: str
    status_code: int | None
    passed: bool
    detail: str
    raw_output: str | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the Phase 8 backend, frontend proxy, and browser acceptance "
            "matrix against local live services."
        )
    )
    parser.add_argument("--backend-base", default="http://127.0.0.1:8001")
    parser.add_argument("--frontend-base", default="http://127.0.0.1:4173")
    parser.add_argument("--sample-input", type=Path, default=DEFAULT_SAMPLE_INPUT)
    parser.add_argument("--job-id", default=None, help="Reuse an existing job instead of creating one.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for JSON, Markdown, and raw response artifacts.",
    )
    parser.add_argument(
        "--browser-executable",
        type=Path,
        default=None,
        help="Path to Chrome or Edge. Auto-detected on Windows when omitted.",
    )
    parser.add_argument("--timeout-seconds", type=int, default=90)
    return parser.parse_args()


def normalize_base_url(value: str) -> str:
    return value.rstrip("/") + "/"


def service_url(base_url: str, path: str) -> str:
    return urljoin(normalize_base_url(base_url), path.lstrip("/"))


def safe_name(value: str) -> str:
    return (
        value.lower()
        .replace("/", "_")
        .replace("\\", "_")
        .replace(":", "_")
        .replace("?", "_")
        .replace("&", "_")
        .replace("=", "_")
    )


def write_text(path: Path, content: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return str(path)


def write_json(path: Path, payload: Any) -> str:
    return write_text(path, json.dumps(payload, indent=2, sort_keys=True))


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def http_json(
    *,
    method: str,
    url: str,
    output_dir: Path,
    raw_name: str,
    body: Any | None = None,
    timeout_seconds: int,
) -> tuple[int | None, Any | None, str, str | None]:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers, method=method)
    status_code: int | None = None
    text = ""
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            status_code = response.status
            text = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        status_code = exc.code
        text = exc.read().decode("utf-8", errors="replace")
    except URLError as exc:
        text = str(exc)
    except TimeoutError as exc:
        text = str(exc)

    raw_path = output_dir / f"raw_{safe_name(raw_name)}.json"
    parsed: Any | None = None
    if text:
        try:
            parsed = json.loads(text)
            write_json(raw_path, parsed)
        except json.JSONDecodeError:
            raw_path = output_dir / f"raw_{safe_name(raw_name)}.txt"
            write_text(raw_path, text)
    return status_code, parsed, text, str(raw_path) if text else None


def add_http_check(
    checks: list[MatrixCheck],
    *,
    name: str,
    layer: str,
    method: str,
    url: str,
    output_dir: Path,
    timeout_seconds: int,
    expected_status: set[int],
    body: Any | None = None,
    invariant: Any = None,
    attempts: int = 1,
) -> Any | None:
    status_code: int | None = None
    parsed: Any | None = None
    text = ""
    raw_path: str | None = None
    passed = False
    detail = f"HTTP {status_code}; expected {sorted(expected_status)}."
    final_attempt = 1
    for attempt_index in range(attempts):
        final_attempt = attempt_index + 1
        status_code, parsed, text, raw_path = http_json(
            method=method,
            url=url,
            output_dir=output_dir,
            raw_name=name,
            body=body,
            timeout_seconds=timeout_seconds,
        )
        passed = status_code in expected_status
        detail = f"HTTP {status_code}; expected {sorted(expected_status)}."
        if passed and invariant is not None:
            try:
                invariant_detail = invariant(parsed)
                detail = invariant_detail or detail
            except AssertionError as exc:
                passed = False
                detail = str(exc)
        elif not passed and text:
            detail = text[:500]
        if passed:
            if attempts > 1 and final_attempt > 1:
                detail = f"{detail} (attempt {final_attempt}/{attempts})"
            break
        if attempt_index + 1 >= attempts:
            break
    checks.append(
        MatrixCheck(
            name=name,
            layer=layer,
            method=method,
            url=url,
            status_code=status_code,
            passed=passed,
            detail=detail,
            raw_output=raw_path,
        )
    )
    return parsed


def require_mapping(payload: Any, name: str) -> dict[str, Any]:
    assert isinstance(payload, dict), f"{name} did not return a JSON object."
    return payload


def require_job_id(payload: Any) -> str:
    data = require_mapping(payload, "analyze")
    job_id = data.get("job_id")
    assert isinstance(job_id, str) and job_id, "Analyze response did not include job_id."
    assert data.get("status") in {
        "queued",
        "running",
        "evidence_ready",
        "decision_ready",
        "completed",
        "degraded",
    }, "Analyze response returned an unexpected status."
    return job_id


def load_analyze_payload(path: Path, suffix: str) -> dict[str, Any]:
    payload = require_mapping(read_json(path), "sample input")
    original_sample_id = str(payload.get("sample_id") or "phase8_live_sample")
    payload["sample_id"] = f"{original_sample_id}_{suffix}"
    return payload


def first_previewable_artifact(artifacts_payload: Any) -> dict[str, Any]:
    data = require_mapping(artifacts_payload, "artifact manifest")
    artifacts = data.get("artifacts")
    assert isinstance(artifacts, list) and artifacts, "Artifact manifest is empty."
    for artifact in artifacts:
        if isinstance(artifact, dict) and artifact.get("preview_eligible") is True:
            return artifact
    raise AssertionError("Artifact manifest did not include a previewable artifact.")


def auto_detect_browser() -> Path | None:
    candidates = [
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    ]
    return next((candidate for candidate in candidates if candidate.exists()), None)


def dump_browser_dom(
    *,
    browser_executable: Path,
    url: str,
    output_dir: Path,
    name: str,
    timeout_seconds: int,
) -> tuple[int | None, str, str | None]:
    output_path = output_dir / f"browser_{safe_name(name)}.html"
    with tempfile.TemporaryDirectory(prefix="phase8_browser_") as profile_dir:
        command = [
            str(browser_executable),
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            "--disable-background-networking",
            "--disable-dev-shm-usage",
            f"--user-data-dir={profile_dir}",
            "--virtual-time-budget=45000",
            "--dump-dom",
            url,
        ]
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            text = (exc.stdout or "") + (exc.stderr or "")
            write_text(output_path, text)
            return None, "Browser DOM dump timed out.", str(output_path)

    dom = completed.stdout or completed.stderr
    write_text(output_path, dom)
    if completed.returncode != 0:
        return completed.returncode, f"Browser exited with code {completed.returncode}.", str(output_path)
    return completed.returncode, dom, str(output_path)


def add_browser_check(
    checks: list[MatrixCheck],
    *,
    name: str,
    url: str,
    browser_executable: Path,
    output_dir: Path,
    timeout_seconds: int,
    required_text: list[str],
    forbidden_text: list[str] | None = None,
) -> None:
    status_code, dom_or_error, raw_path = dump_browser_dom(
        browser_executable=browser_executable,
        url=url,
        output_dir=output_dir,
        name=name,
        timeout_seconds=timeout_seconds,
    )
    passed = status_code == 0
    detail = "Browser route rendered with required text."
    if passed:
        missing = [text for text in required_text if text not in dom_or_error]
        forbidden = [text for text in forbidden_text or [] if text in dom_or_error]
        if missing:
            passed = False
            detail = f"Browser DOM missing required text: {', '.join(missing)}."
        elif forbidden:
            passed = False
            detail = f"Browser DOM contained forbidden text: {', '.join(forbidden)}."
    else:
        detail = dom_or_error[:500]
    checks.append(
        MatrixCheck(
            name=name,
            layer="browser",
            method="GET",
            url=url,
            status_code=status_code,
            passed=passed,
            detail=detail,
            raw_output=raw_path,
        )
    )


def markdown_report(
    *,
    checks: list[MatrixCheck],
    job_id: str,
    direct_job_id: str | None,
    output_dir: Path,
    backend_base: str,
    frontend_base: str,
) -> str:
    rows = [
        "| Layer | Check | Method | Result | Detail |",
        "| --- | --- | --- | --- | --- |",
    ]
    for check in checks:
        result = "PASS" if check.passed else "FAIL"
        detail = check.detail.replace("\n", " ")
        rows.append(
            f"| {check.layer} | `{check.name}` | `{check.method}` | {result} | {detail} |"
        )
    failed = sum(1 for check in checks if not check.passed)
    passed = len(checks) - failed
    direct_line = f"- Direct backend analyze job: `{direct_job_id}`\n" if direct_job_id else ""
    return "\n".join(
        [
            "# Phase 8 Backend, Proxy, and Browser Acceptance Run",
            "",
            f"- Created at: `{datetime.now(UTC).isoformat()}`",
            f"- Backend base: `{backend_base}`",
            f"- Frontend base: `{frontend_base}`",
            direct_line.rstrip(),
            f"- Matrix job: `{job_id}`",
            f"- Output directory: `{output_dir}`",
            f"- Passed checks: `{passed}`",
            f"- Failed checks: `{failed}`",
            "",
            "## Results",
            "",
            *rows,
            "",
            "## Closure Rules",
            "",
            "- The matrix job must be created in this run unless `--job-id` is explicitly supplied.",
            "- Backend and proxy reads must verify the same job.",
            "- Browser routes must render required text, not merely return the Vite shell.",
            "- Copilot and C1 checks require live provider output for Phase 8 closure.",
        ]
    )


def main() -> int:
    args = parse_args()
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_dir = args.output_dir or (
        REPO_ROOT / "artifacts" / "runs" / "phase8_acceptance_matrix" / timestamp
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    backend_base = normalize_base_url(args.backend_base)
    frontend_base = normalize_base_url(args.frontend_base)
    browser_executable = args.browser_executable or auto_detect_browser()
    checks: list[MatrixCheck] = []

    if browser_executable is None:
        raise SystemExit(
            "No Chrome or Edge executable was found. Pass --browser-executable to run browser checks."
        )

    direct_job_id: str | None = None
    matrix_job_id = args.job_id
    suffix = datetime.now(UTC).strftime("%Y%m%d%H%M%S")

    add_http_check(
        checks,
        name="backend_health",
        layer="backend",
        method="GET",
        url=service_url(backend_base, "/health"),
        output_dir=output_dir,
        timeout_seconds=args.timeout_seconds,
        expected_status={200},
        invariant=lambda payload: (
            "Backend health is live-ready."
            if require_mapping(payload, "health").get("runtime", {}).get("live_mode_ready") is True
            else (_ for _ in ()).throw(AssertionError("Backend runtime is not live-ready."))
        ),
    )
    add_http_check(
        checks,
        name="backend_integrations",
        layer="backend",
        method="GET",
        url=service_url(backend_base, "/health/integrations"),
        output_dir=output_dir,
        timeout_seconds=args.timeout_seconds,
        expected_status={200},
        invariant=lambda payload: (
            "Integration health is ready and live."
            if require_mapping(payload, "integrations").get("status") == "ready"
            and require_mapping(payload, "integrations").get("mode") == "live"
            else (_ for _ in ()).throw(AssertionError("Integrations are not ready in live mode."))
        ),
    )

    if matrix_job_id is None:
        backend_payload = load_analyze_payload(args.sample_input, f"backend_{suffix}")
        backend_analyze = add_http_check(
            checks,
            name="backend_analyze",
            layer="backend",
            method="POST",
            url=service_url(backend_base, "/jobs/analyze"),
            output_dir=output_dir,
            timeout_seconds=args.timeout_seconds,
            expected_status={201},
            body=backend_payload,
            invariant=lambda payload: f"Created backend job {require_job_id(payload)}.",
        )
        if isinstance(backend_analyze, dict) and isinstance(backend_analyze.get("job_id"), str):
            direct_job_id = backend_analyze["job_id"]

        proxy_payload = load_analyze_payload(args.sample_input, f"proxy_{suffix}")
        proxy_analyze = add_http_check(
            checks,
            name="proxy_analyze",
            layer="proxy",
            method="POST",
            url=service_url(frontend_base, "/api/jobs/analyze"),
            output_dir=output_dir,
            timeout_seconds=args.timeout_seconds,
            expected_status={201},
            body=proxy_payload,
            invariant=lambda payload: f"Created proxy job {require_job_id(payload)}.",
        )
        if isinstance(proxy_analyze, dict) and isinstance(proxy_analyze.get("job_id"), str):
            matrix_job_id = proxy_analyze["job_id"]

    if not matrix_job_id:
        write_json(output_dir / "phase8_acceptance_matrix.json", {"checks": [asdict(check) for check in checks]})
        raise SystemExit("No matrix job_id is available; analyze checks failed.")

    encoded_question = quote("What evidence supports this triage decision?")
    backend_artifacts = add_http_check(
        checks,
        name="backend_artifacts",
        layer="backend",
        method="GET",
        url=service_url(backend_base, f"/jobs/{matrix_job_id}/artifacts"),
        output_dir=output_dir,
        timeout_seconds=args.timeout_seconds,
        expected_status={200},
        invariant=lambda payload: (
            f"Artifact manifest includes {len(require_mapping(payload, 'artifacts').get('artifacts', []))} artifacts."
            if isinstance(require_mapping(payload, "artifacts").get("artifacts"), list)
            and require_mapping(payload, "artifacts").get("artifacts")
            else (_ for _ in ()).throw(AssertionError("Artifact manifest is empty."))
        ),
    )
    preview_artifact = first_previewable_artifact(backend_artifacts)
    preview_id = preview_artifact["artifact_id"]

    backend_read_checks = [
        (
            "backend_queue",
            "/queue",
            lambda payload: (
                "Queue contains the matrix job."
                if any(
                    isinstance(item, dict) and item.get("job_id") == matrix_job_id
                    for item in require_mapping(payload, "queue").get("items", [])
                )
                else (_ for _ in ()).throw(AssertionError("Queue does not contain the matrix job."))
            ),
            1,
        ),
        (
            "backend_status",
            f"/jobs/{matrix_job_id}/status",
            lambda payload: (
                "Job status is completed or degraded but persisted."
                if require_mapping(payload, "status").get("job_id") == matrix_job_id
                and require_mapping(payload, "status").get("status") in {"completed", "degraded"}
                else (_ for _ in ()).throw(AssertionError("Job status is not persisted as completed/degraded."))
            ),
            1,
        ),
        (
            "backend_decision",
            f"/jobs/{matrix_job_id}/decision",
            lambda payload: (
                "Decision contains actionability and triage fields."
                if isinstance(require_mapping(payload, "decision").get("decision", {}).get("actionability_features"), dict)
                and isinstance(require_mapping(payload, "decision").get("decision", {}).get("triage_decision"), dict)
                else (_ for _ in ()).throw(AssertionError("Decision payload is missing actionability or triage."))
            ),
            1,
        ),
        (
            "backend_artifact_preview",
            f"/jobs/{matrix_job_id}/artifacts/{preview_id}/preview?max_bytes=2048",
            lambda payload: (
                "Artifact preview returned bounded content."
                if require_mapping(payload, "preview").get("artifact_id") == preview_id
                and isinstance(require_mapping(payload, "preview").get("content"), str)
                else (_ for _ in ()).throw(AssertionError("Artifact preview did not return content."))
            ),
            1,
        ),
        (
            "backend_semantic_ui",
            f"/jobs/{matrix_job_id}/semantic-ui",
            lambda payload: (
                "Semantic UI was generated by live LLM."
                if require_mapping(payload, "semantic ui").get("output_origin", {}).get("mode") == "live_llm"
                and isinstance(require_mapping(payload, "semantic ui").get("semantic_ui"), dict)
                else (_ for _ in ()).throw(AssertionError("Semantic UI is not live_llm with a payload."))
            ),
            4,
        ),
        (
            "backend_semantic_ui_c1",
            f"/jobs/{matrix_job_id}/semantic-ui/c1",
            lambda payload: (
                "Thesys C1 rendered without fallback."
                if require_mapping(payload, "c1").get("status") == "rendered"
                and require_mapping(payload, "c1").get("fallback_required") is False
                else (_ for _ in ()).throw(AssertionError("Thesys C1 did not render cleanly."))
            ),
            1,
        ),
        (
            "backend_copilot_explanation",
            f"/jobs/{matrix_job_id}/copilot/explanation",
            lambda payload: (
                "Copilot explanation is live and non-refusal."
                if require_mapping(payload, "explanation").get("output_origin", {}).get("mode") == "live_llm"
                and require_mapping(payload, "explanation").get("copilot", {}).get("refusal_required") is False
                else (_ for _ in ()).throw(AssertionError("Copilot explanation is not live non-refusal."))
            ),
            4,
        ),
        (
            "backend_copilot_answer",
            f"/jobs/{matrix_job_id}/copilot/answer?question={encoded_question}",
            lambda payload: (
                "Copilot answer is live and non-refusal."
                if require_mapping(payload, "answer").get("output_origin", {}).get("mode") == "live_llm"
                and require_mapping(payload, "answer").get("copilot", {}).get("refusal_required") is False
                else (_ for _ in ()).throw(AssertionError("Copilot answer is not live non-refusal."))
            ),
            4,
        ),
        (
            "backend_copilot_queue_summary",
            f"/jobs/{matrix_job_id}/copilot/queue-summary",
            lambda payload: (
                "Copilot queue summary is live and non-refusal."
                if require_mapping(payload, "queue summary").get("output_origin", {}).get("mode") == "live_llm"
                and require_mapping(payload, "queue summary").get("copilot", {}).get("refusal_required") is False
                else (_ for _ in ()).throw(AssertionError("Copilot queue summary is not live non-refusal."))
            ),
            4,
        ),
    ]

    for name, path, invariant, attempts in backend_read_checks:
        if name == "backend_artifacts":
            continue
        add_http_check(
            checks,
            name=name,
            layer="backend",
            method="GET",
            url=service_url(backend_base, path),
            output_dir=output_dir,
            timeout_seconds=args.timeout_seconds,
            expected_status={200},
            invariant=invariant,
            attempts=attempts,
        )

    proxy_paths = [
        (
            "proxy_health",
            "/api/health",
            lambda payload: (
                "Proxy health returned live-ready runtime."
                if require_mapping(payload, "health").get("runtime", {}).get("live_mode_ready") is True
                else (_ for _ in ()).throw(AssertionError("Proxy health is not live-ready."))
            ),
            1,
        ),
        (
            "proxy_integrations",
            "/api/health/integrations",
            lambda payload: (
                "Proxy integration health is ready and live."
                if require_mapping(payload, "integrations").get("status") == "ready"
                and require_mapping(payload, "integrations").get("mode") == "live"
                else (_ for _ in ()).throw(AssertionError("Proxy integrations are not ready/live."))
            ),
            1,
        ),
        (
            "proxy_queue",
            "/api/queue",
            lambda payload: (
                "Proxy queue contains the matrix job."
                if any(
                    isinstance(item, dict) and item.get("job_id") == matrix_job_id
                    for item in require_mapping(payload, "queue").get("items", [])
                )
                else (_ for _ in ()).throw(AssertionError("Proxy queue does not contain the matrix job."))
            ),
            1,
        ),
        ("proxy_status", f"/api/jobs/{matrix_job_id}/status", lambda payload: "Proxy job status returned.", 1),
        ("proxy_decision", f"/api/jobs/{matrix_job_id}/decision", lambda payload: "Proxy decision returned.", 1),
        ("proxy_artifacts", f"/api/jobs/{matrix_job_id}/artifacts", lambda payload: "Proxy artifacts returned.", 1),
        (
            "proxy_artifact_preview",
            f"/api/jobs/{matrix_job_id}/artifacts/{preview_id}/preview?max_bytes=2048",
            lambda payload: "Proxy artifact preview returned.",
            1,
        ),
        ("proxy_semantic_ui", f"/api/jobs/{matrix_job_id}/semantic-ui", lambda payload: "Proxy semantic UI returned.", 4),
        (
            "proxy_semantic_ui_c1",
            f"/api/jobs/{matrix_job_id}/semantic-ui/c1",
            lambda payload: (
                "Proxy C1 rendered without fallback."
                if require_mapping(payload, "c1").get("status") == "rendered"
                and require_mapping(payload, "c1").get("fallback_required") is False
                else (_ for _ in ()).throw(AssertionError("Proxy C1 did not render cleanly."))
            ),
            1,
        ),
        (
            "proxy_copilot_explanation",
            f"/api/jobs/{matrix_job_id}/copilot/explanation",
            lambda payload: (
                "Proxy copilot explanation is live-backed or cached from the matrix job."
                if require_mapping(payload, "explanation").get("output_origin", {}).get("mode")
                in {"live_llm", "cached"}
                else (_ for _ in ()).throw(AssertionError("Proxy explanation is not live-backed/cached."))
            ),
            4,
        ),
        (
            "proxy_copilot_answer",
            f"/api/jobs/{matrix_job_id}/copilot/answer?question={encoded_question}",
            lambda payload: (
                "Proxy copilot answer is live-backed or cached from the matrix job."
                if require_mapping(payload, "answer").get("output_origin", {}).get("mode")
                in {"live_llm", "cached"}
                else (_ for _ in ()).throw(AssertionError("Proxy answer is not live-backed/cached."))
            ),
            4,
        ),
        (
            "proxy_copilot_queue_summary",
            f"/api/jobs/{matrix_job_id}/copilot/queue-summary",
            lambda payload: (
                "Proxy copilot queue summary is live-backed or cached from the matrix job."
                if require_mapping(payload, "queue summary").get("output_origin", {}).get("mode")
                in {"live_llm", "cached"}
                else (_ for _ in ()).throw(AssertionError("Proxy queue summary is not live-backed/cached."))
            ),
            4,
        ),
    ]
    for name, path, invariant, attempts in proxy_paths:
        add_http_check(
            checks,
            name=name,
            layer="proxy",
            method="GET",
            url=service_url(frontend_base, path),
            output_dir=output_dir,
            timeout_seconds=args.timeout_seconds,
            expected_status={200},
            invariant=invariant,
            attempts=attempts,
        )

    browser_routes = [
        (
            "browser_dashboard",
            "/",
            ["Clinical command surface", "Backend runtime"],
            ["did not load", "Internal Server Error"],
        ),
        (
            "browser_queue",
            f"/queue?q={quote(matrix_job_id)}",
            ["Analyst Queue", matrix_job_id, "Actionability"],
            ["did not load", "Internal Server Error"],
        ),
        (
            "browser_case_detail",
            f"/cases/{matrix_job_id}",
            ["Case decision screen", matrix_job_id, "Grounded copilot"],
            ["Case bundle did not load", "Internal Server Error"],
        ),
        (
            "browser_fallback_renderer",
            f"/fallback-renderer?jobId={quote(matrix_job_id)}",
            ["Thesys C1 primary render", matrix_job_id, "Thesys C1 boundary"],
            ["Semantic UI did not load", "Thesys boundary did not load", "Internal Server Error"],
        ),
        (
            "browser_evaluation",
            f"/evaluation?jobId={quote(matrix_job_id)}",
            ["Live operator checklist", "Selected acceptance job", matrix_job_id],
            ["did not load", "Internal Server Error"],
        ),
        (
            "browser_new_analysis",
            "/analysis/new",
            ["Submit", "Analysis"],
            ["did not load", "Internal Server Error"],
        ),
    ]
    for name, path, required_text, forbidden_text in browser_routes:
        add_browser_check(
            checks,
            name=name,
            url=service_url(frontend_base, path),
            browser_executable=browser_executable,
            output_dir=output_dir,
            timeout_seconds=args.timeout_seconds,
            required_text=required_text,
            forbidden_text=forbidden_text,
        )

    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "backend_base": backend_base.rstrip("/"),
        "frontend_base": frontend_base.rstrip("/"),
        "browser_executable": str(browser_executable),
        "direct_backend_job_id": direct_job_id,
        "matrix_job_id": matrix_job_id,
        "output_dir": str(output_dir),
        "summary": {
            "total": len(checks),
            "passed": sum(1 for check in checks if check.passed),
            "failed": sum(1 for check in checks if not check.passed),
        },
        "checks": [asdict(check) for check in checks],
    }
    report_path = output_dir / "phase8_acceptance_matrix.json"
    markdown_path = output_dir / "phase8_acceptance_matrix.md"
    write_json(report_path, report)
    write_text(
        markdown_path,
        markdown_report(
            checks=checks,
            job_id=matrix_job_id,
            direct_job_id=direct_job_id,
            output_dir=output_dir,
            backend_base=backend_base.rstrip("/"),
            frontend_base=frontend_base.rstrip("/"),
        ),
    )
    print(json.dumps(report["summary"], indent=2))
    print(report_path)
    print(markdown_path)
    return 0 if report["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
