from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "run_v2_curl_matrix.py"


def load_script_module():
    spec = importlib.util.spec_from_file_location("run_v2_curl_matrix", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_v2_audit_invariant_accepts_pending_provider_proof_without_provider_calls() -> None:
    module = load_script_module()

    detail = module.invariant_v2_audit(
        {
            "schema_version": "v2.audit_bundle.v1",
            "provenance": {
                "live_input": True,
                "fixture_trained_baseline": True,
            },
            "summary": {
                "overall_status": "pending",
                "passing_checks": 17,
                "failed_checks": 0,
                "pending_checks": 2,
            },
            "metadata": {
                "provider_calls_triggered": False,
            },
        }
    )

    assert "pending" in detail
    assert "17 pass" in detail


def test_execution_gate_invariant_rejects_provider_calls() -> None:
    module = load_script_module()

    try:
        module.invariant_execution_gate(
            {
                "schema_version": "v2.execution_gate.v1",
                "gate_decision": "allow",
                "audit_fingerprint": "sha256:" + "a" * 64,
                "metadata": {
                    "provider_calls_triggered": True,
                },
            }
        )
    except AssertionError as exc:
        assert "provider calls" in str(exc)
    else:
        raise AssertionError("Expected invariant_execution_gate to reject provider calls.")


def test_markdown_report_documents_provider_boundary() -> None:
    module = load_script_module()
    report = {
        "created_at": "2026-04-26T00:00:00Z",
        "backend_base_url": "http://127.0.0.1:8001",
        "frontend_base_url": "http://127.0.0.1:4173",
        "job_id": "job_test",
        "provider_proof_requested": False,
        "frontend_proxy_included": False,
        "output_dir": "artifacts/runs/v2_curl_matrix/test",
        "summary": {"passed": 1, "warned": 1, "failed": 0},
        "checks": [
            {
                "layer": "backend",
                "name": "verification",
                "method": "GET",
                "status": "pass",
                "detail": "Execution gate returned allow.",
            }
        ],
    }

    markdown = module.markdown_report(report)

    assert "Provider routes run only when `--provider-proof` is supplied." in markdown
    assert "provider_calls_triggered=false" in markdown
