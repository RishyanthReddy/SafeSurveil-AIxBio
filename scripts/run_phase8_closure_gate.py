from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any
from urllib.parse import quote

from run_phase8_acceptance_matrix import (
    DEFAULT_SAMPLE_INPUT,
    REPO_ROOT,
    auto_detect_browser,
    normalize_base_url,
    service_url,
    write_json,
    write_text,
)


@dataclass
class ScreenshotCapture:
    name: str
    url: str
    output_path: str
    window_size: str
    passed: bool
    detail: str
    bytes_written: int | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the Phase 8 closure gate: execute the live acceptance matrix, "
            "capture the final screenshot pack, and write a closure verdict."
        )
    )
    parser.add_argument("--backend-base", default="http://127.0.0.1:8001")
    parser.add_argument("--frontend-base", default="http://127.0.0.1:4173")
    parser.add_argument("--sample-input", type=Path, default=DEFAULT_SAMPLE_INPUT)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for acceptance artifacts, screenshots, and the closure report.",
    )
    parser.add_argument(
        "--browser-executable",
        type=Path,
        default=None,
        help="Path to Chrome or Edge. Auto-detected on Windows when omitted.",
    )
    parser.add_argument("--timeout-seconds", type=int, default=90)
    parser.add_argument("--virtual-time-budget-ms", type=int, default=45000)
    return parser.parse_args()


def run_acceptance_matrix(
    *,
    backend_base: str,
    frontend_base: str,
    sample_input: Path,
    output_dir: Path,
    browser_executable: Path,
    timeout_seconds: int,
) -> tuple[int, dict[str, Any], str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "run_phase8_acceptance_matrix.py"),
        "--backend-base",
        backend_base.rstrip("/"),
        "--frontend-base",
        frontend_base.rstrip("/"),
        "--sample-input",
        str(sample_input),
        "--output-dir",
        str(output_dir),
        "--browser-executable",
        str(browser_executable),
        "--timeout-seconds",
        str(timeout_seconds),
    ]
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=max(timeout_seconds * 6, 300),
    )
    stdout_path = output_dir / "acceptance_stdout.txt"
    stderr_path = output_dir / "acceptance_stderr.txt"
    write_text(stdout_path, completed.stdout or "")
    write_text(stderr_path, completed.stderr or "")

    report_path = output_dir / "phase8_acceptance_matrix.json"
    if not report_path.exists():
        raise RuntimeError(
            "Phase 8 acceptance matrix did not produce a JSON report. "
            f"stdout: {stdout_path}; stderr: {stderr_path}"
        )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    return completed.returncode, report, str(stdout_path), str(stderr_path)


def capture_screenshot(
    *,
    browser_executable: Path,
    url: str,
    output_path: Path,
    window_size: str,
    timeout_seconds: int,
    virtual_time_budget_ms: int,
) -> ScreenshotCapture:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="phase8_capture_") as profile_dir:
        command = [
            str(browser_executable),
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            "--disable-background-networking",
            "--disable-dev-shm-usage",
            "--hide-scrollbars",
            "--force-device-scale-factor=1",
            "--run-all-compositor-stages-before-draw",
            f"--user-data-dir={profile_dir}",
            f"--virtual-time-budget={virtual_time_budget_ms}",
            f"--window-size={window_size}",
            f"--screenshot={output_path}",
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
        except subprocess.TimeoutExpired:
            return ScreenshotCapture(
                name=output_path.stem,
                url=url,
                output_path=str(output_path),
                window_size=window_size,
                passed=False,
                detail="Screenshot capture timed out.",
            )

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip() or f"Browser exited with {completed.returncode}."
        return ScreenshotCapture(
            name=output_path.stem,
            url=url,
            output_path=str(output_path),
            window_size=window_size,
            passed=False,
            detail=detail[:500],
        )

    if not output_path.exists():
        return ScreenshotCapture(
            name=output_path.stem,
            url=url,
            output_path=str(output_path),
            window_size=window_size,
            passed=False,
            detail="Screenshot file was not created.",
        )

    byte_count = output_path.stat().st_size
    if byte_count < 10_000:
        return ScreenshotCapture(
            name=output_path.stem,
            url=url,
            output_path=str(output_path),
            window_size=window_size,
            passed=False,
            detail=f"Screenshot is unexpectedly small at {byte_count} bytes.",
            bytes_written=byte_count,
        )

    return ScreenshotCapture(
        name=output_path.stem,
        url=url,
        output_path=str(output_path),
        window_size=window_size,
        passed=True,
        detail=f"Captured {byte_count} bytes.",
        bytes_written=byte_count,
    )


def screenshot_specs(job_id: str) -> list[tuple[str, str, str]]:
    encoded_job = quote(job_id)
    return [
        ("01_dashboard_overview", "/", "1600,1400"),
        ("02_analyst_queue", f"/queue?q={encoded_job}", "1680,1600"),
        ("03_case_detail", f"/cases/{encoded_job}", "1680,2200"),
        ("04_fallback_renderer", f"/fallback-renderer?jobId={encoded_job}", "1600,1800"),
        ("05_evaluation_closure", f"/evaluation?jobId={encoded_job}", "1600,2100"),
    ]


def markdown_report(
    *,
    closure_report: dict[str, Any],
    acceptance_report: dict[str, Any],
    screenshots: list[ScreenshotCapture],
) -> str:
    screenshot_rows = [
        "| Screenshot | Result | Window | Detail |",
        "| --- | --- | --- | --- |",
    ]
    for capture in screenshots:
        screenshot_rows.append(
            f"| `{capture.name}` | {'PASS' if capture.passed else 'FAIL'} | `{capture.window_size}` | {capture.detail} |"
        )

    verdict = "PASS" if closure_report["closure_passed"] else "FAIL"
    return "\n".join(
        [
            "# Phase 8 Closure Gate",
            "",
            f"- Created at: `{closure_report['created_at']}`",
            f"- Backend base: `{closure_report['backend_base']}`",
            f"- Frontend base: `{closure_report['frontend_base']}`",
            f"- Browser executable: `{closure_report['browser_executable']}`",
            f"- Direct backend job: `{closure_report.get('direct_backend_job_id') or 'n/a'}`",
            f"- Matrix job: `{closure_report['matrix_job_id']}`",
            f"- Acceptance report: `{closure_report['acceptance_report']}`",
            f"- Screenshot directory: `{closure_report['screenshot_directory']}`",
            f"- Closure verdict: `{verdict}`",
            "",
            "## Acceptance Matrix Summary",
            "",
            f"- Total checks: `{acceptance_report['summary']['total']}`",
            f"- Passed checks: `{acceptance_report['summary']['passed']}`",
            f"- Failed checks: `{acceptance_report['summary']['failed']}`",
            "",
            "## Screenshot Pack",
            "",
            *screenshot_rows,
            "",
            "## Closure Rules",
            "",
            "- The acceptance matrix must pass on a fresh live-created job.",
            "- The screenshot pack must be captured against the same matrix job.",
            "- Phase 8 is closed only when both the matrix and screenshots pass in the same run.",
        ]
    )


def main() -> int:
    args = parse_args()
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_dir = args.output_dir or (REPO_ROOT / "artifacts" / "runs" / "phase8_closure_gate" / timestamp)
    output_dir.mkdir(parents=True, exist_ok=True)

    backend_base = normalize_base_url(args.backend_base)
    frontend_base = normalize_base_url(args.frontend_base)
    browser_executable = args.browser_executable or auto_detect_browser()
    if browser_executable is None:
        raise SystemExit(
            "No Chrome or Edge executable was found. Pass --browser-executable to run the closure gate."
        )

    acceptance_dir = output_dir / "acceptance_matrix"
    acceptance_code, acceptance_report, acceptance_stdout, acceptance_stderr = run_acceptance_matrix(
        backend_base=backend_base,
        frontend_base=frontend_base,
        sample_input=args.sample_input,
        output_dir=acceptance_dir,
        browser_executable=browser_executable,
        timeout_seconds=args.timeout_seconds,
    )
    matrix_job_id = acceptance_report.get("matrix_job_id")
    if not isinstance(matrix_job_id, str) or not matrix_job_id:
        raise SystemExit("Acceptance matrix report did not include a matrix_job_id.")

    screenshots: list[ScreenshotCapture] = []
    screenshot_dir = output_dir / "screenshots"
    acceptance_failed = acceptance_code != 0 or acceptance_report["summary"]["failed"] != 0
    if not acceptance_failed:
        for name, path, window_size in screenshot_specs(matrix_job_id):
            screenshots.append(
                capture_screenshot(
                    browser_executable=browser_executable,
                    url=service_url(frontend_base, path),
                    output_path=screenshot_dir / f"{name}.png",
                    window_size=window_size,
                    timeout_seconds=args.timeout_seconds,
                    virtual_time_budget_ms=args.virtual_time_budget_ms,
                )
            )

    closure_passed = (not acceptance_failed) and all(capture.passed for capture in screenshots)
    closure_report = {
        "created_at": datetime.now(UTC).isoformat(),
        "backend_base": backend_base.rstrip("/"),
        "frontend_base": frontend_base.rstrip("/"),
        "browser_executable": str(browser_executable),
        "direct_backend_job_id": acceptance_report.get("direct_backend_job_id"),
        "matrix_job_id": matrix_job_id,
        "acceptance_report": str(acceptance_dir / "phase8_acceptance_matrix.json"),
        "acceptance_markdown": str(acceptance_dir / "phase8_acceptance_matrix.md"),
        "acceptance_stdout": acceptance_stdout,
        "acceptance_stderr": acceptance_stderr,
        "screenshot_directory": str(screenshot_dir),
        "closure_passed": closure_passed,
        "screenshots": [asdict(capture) for capture in screenshots],
    }

    json_path = output_dir / "phase8_closure_gate.json"
    markdown_path = output_dir / "phase8_closure_gate.md"
    write_json(json_path, closure_report)
    write_text(
        markdown_path,
        markdown_report(
            closure_report=closure_report,
            acceptance_report=acceptance_report,
            screenshots=screenshots,
        ),
    )
    print(json.dumps({"closure_passed": closure_passed, "matrix_job_id": matrix_job_id}, indent=2))
    print(json_path)
    print(markdown_path)
    return 0 if closure_passed else 1


if __name__ == "__main__":
    sys.exit(main())
