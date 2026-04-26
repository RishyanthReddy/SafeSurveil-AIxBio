from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.contracts import (
    CitationValiditySummary,
    EvidenceCoverageSummary,
    ExecutionGateCheck,
    ExecutionGateDecision,
    ExecutionGateIssue,
    ExecutionGateReport,
    EXECUTION_GATE_SCHEMA_VERSION,
    NumericConsistencySummary,
    PolicyAlignmentSummary,
)

_DIGEST_A = "sha256:" + "a" * 64
_DIGEST_B = "sha256:" + "b" * 64


def build_execution_gate_report(**overrides: object) -> ExecutionGateReport:
    payload: dict[str, object] = {
        "job_id": "JOB_001",
        "sample_id": "Sample_001",
        "decision": "review",
        "severity": "high",
        "gate_decision": "review",
        "summary": "Sidecar output is mostly grounded but needs analyst review.",
        "checks": [
            ExecutionGateCheck(
                check_id="Citation Coverage",
                category="citation_validity",
                status="warn",
                title="Citation coverage is partial",
                detail="One required evidence reference was not cited by the sidecar output.",
                evidence_refs=["decision_object__summary"],
            )
        ],
        "evidence_coverage": EvidenceCoverageSummary(
            required_evidence_ids=["decision_object__summary", "mechanistic_evidence__1"],
            covered_evidence_ids=["decision_object__summary"],
            missing_evidence_ids=["mechanistic_evidence__1"],
            coverage_ratio=0.5,
        ),
        "numeric_consistency": NumericConsistencySummary(
            checked_fields=["probability", "actionability_score"],
            matched_fields=["probability", "actionability_score"],
            mismatched_fields=[],
            consistency_ratio=1.0,
        ),
        "citation_validity": CitationValiditySummary(
            allowed_evidence_ids=["decision_object__summary", "mechanistic_evidence__1"],
            cited_evidence_ids=["decision_object__summary"],
            invalid_evidence_ids=[],
            missing_required_evidence_ids=["mechanistic_evidence__1"],
            validity_ratio=1.0,
        ),
        "policy_alignment": PolicyAlignmentSummary(
            policy_version="actionability_policy_v1",
            triage_matches_decision=True,
            severity_matches_decision=True,
            next_step_matches_decision=True,
            unsafe_claims_detected=False,
        ),
        "issues": [
            ExecutionGateIssue(
                issue_id="missing_mechanism_citation",
                category="evidence_coverage",
                severity="warning",
                title="Mechanistic evidence citation missing",
                detail="The generated sidecar did not cite one expected mechanistic evidence row.",
                evidence_refs=["mechanistic_evidence__1"],
            )
        ],
        "policy_hash": _DIGEST_A,
        "audit_fingerprint": _DIGEST_B,
    }
    payload.update(overrides)
    return ExecutionGateReport(**payload)


def test_execution_gate_report_accepts_review_payload_and_normalizes_ids() -> None:
    report = build_execution_gate_report()

    assert report.schema_version == EXECUTION_GATE_SCHEMA_VERSION
    assert report.job_id == "job_001"
    assert report.sample_id == "sample_001"
    assert report.gate_decision == ExecutionGateDecision.REVIEW
    assert report.checks[0].check_id == "citation_coverage"
    assert report.evidence_coverage.coverage_ratio == pytest.approx(0.5)


def test_execution_gate_report_rejects_non_v2_schema_version() -> None:
    with pytest.raises(ValidationError, match="schema_version"):
        build_execution_gate_report(schema_version="0.1.0")


def test_execution_gate_report_rejects_malformed_audit_digest() -> None:
    with pytest.raises(ValidationError, match="Digest fields"):
        build_execution_gate_report(audit_fingerprint="sha256:not-a-real-digest")


def test_execution_gate_report_rejects_allow_with_warnings() -> None:
    with pytest.raises(ValidationError, match="ALLOW gate reports cannot contain warning"):
        build_execution_gate_report(gate_decision="allow")


def test_execution_gate_report_rejects_block_without_failed_check_or_blocking_issue() -> None:
    with pytest.raises(ValidationError, match="BLOCK gate reports require"):
        build_execution_gate_report(gate_decision="block")


def test_evidence_coverage_ratio_must_match_sets() -> None:
    with pytest.raises(ValidationError, match="coverage_ratio"):
        EvidenceCoverageSummary(
            required_evidence_ids=["decision_object__summary", "mechanistic_evidence__1"],
            covered_evidence_ids=["decision_object__summary"],
            missing_evidence_ids=["mechanistic_evidence__1"],
            coverage_ratio=1.0,
        )


def test_numeric_consistency_rejects_unaccounted_checked_fields() -> None:
    with pytest.raises(ValidationError, match="account for all checked fields"):
        NumericConsistencySummary(
            checked_fields=["probability", "actionability_score"],
            matched_fields=["probability"],
            mismatched_fields=[],
            consistency_ratio=0.5,
        )


def test_citation_validity_rejects_incorrect_invalid_set() -> None:
    with pytest.raises(ValidationError, match="invalid_evidence_ids"):
        CitationValiditySummary(
            allowed_evidence_ids=["decision_object__summary"],
            cited_evidence_ids=["decision_object__summary", "invented_evidence"],
            invalid_evidence_ids=[],
            missing_required_evidence_ids=[],
            validity_ratio=0.5,
        )
