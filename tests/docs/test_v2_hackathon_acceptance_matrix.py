from __future__ import annotations

from pathlib import Path


DOC_PATH = Path(__file__).resolve().parents[2] / "docs" / "v2_hackathon_acceptance_matrix.md"


def test_v2_acceptance_matrix_names_required_verdicts_and_routes() -> None:
    content = DOC_PATH.read_text(encoding="utf-8")

    for verdict in ("`pass`", "`warn`", "`fail`", "`pending`"):
        assert verdict in content

    for route in (
        "/health",
        "/health/integrations",
        "/jobs/{job_id}/decision",
        "/jobs/{job_id}/verification",
        "/jobs/{job_id}/reasoning-trace",
        "/jobs/{job_id}/evidence-graph",
        "/jobs/{job_id}/v2-audit",
        "/jobs/{job_id}/semantic-ui/c1",
    ):
        assert route in content


def test_v2_acceptance_matrix_keeps_provider_proof_explicit() -> None:
    content = DOC_PATH.read_text(encoding="utf-8")

    assert "--provider-proof" in content
    assert "provider_calls_triggered=false" in content
    assert "Cached copilot or semantic-UI sidecars" in content
    assert "Official final V2 closure proof" in content


def test_v2_acceptance_matrix_requires_final_browser_and_live_e2e() -> None:
    content = DOC_PATH.read_text(encoding="utf-8")

    assert "Final fresh frontend-created job" in content
    assert "Browser proof" in content
    assert "zero `pending` checks" in content
    assert "fixture-trained baseline" in content
