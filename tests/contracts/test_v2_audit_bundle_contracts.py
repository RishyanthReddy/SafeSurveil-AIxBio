from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.contracts import (
    V2_AUDIT_BUNDLE_SCHEMA_VERSION,
    V2_AUDIT_REQUIRED_SECTIONS,
    V2AuditBundle,
    V2AuditSectionId,
    V2AuditStatus,
)

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "v2_audit_bundle_example.json"


def load_audit_bundle_payload() -> dict[str, object]:
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


def _section(payload: dict[str, object], section_id: str) -> dict[str, object]:
    sections = payload["sections"]
    assert isinstance(sections, list)
    for section in sections:
        assert isinstance(section, dict)
        if section["section_id"] == section_id:
            return section
    raise AssertionError(f"Missing section {section_id}")


def _first_check(section: dict[str, object]) -> dict[str, object]:
    checks = section["checks"]
    assert isinstance(checks, list)
    first_check = checks[0]
    assert isinstance(first_check, dict)
    return first_check


def _set_single_check_state(
    payload: dict[str, object],
    *,
    section_id: str,
    status: str,
    live_ready: bool,
) -> None:
    section = _section(payload, section_id)
    section["status"] = status
    check = _first_check(section)
    check["status"] = status
    if status == "fail":
        check["blocking"] = True
    summary = payload["summary"]
    assert isinstance(summary, dict)
    summary["overall_status"] = status
    summary["passing_checks"] = 8
    summary["warning_checks"] = 1 if status == "warn" else 0
    summary["failed_checks"] = 1 if status == "fail" else 0
    summary["pending_checks"] = 1 if status == "pending" else 0
    summary["live_ready"] = live_ready


def test_v2_audit_bundle_fixture_validates_and_normalizes_identity() -> None:
    bundle = V2AuditBundle(**load_audit_bundle_payload())

    assert bundle.schema_version == V2_AUDIT_BUNDLE_SCHEMA_VERSION
    assert bundle.job_id == "job_v2_audit_001"
    assert bundle.sample_id == "sample_live_001"
    assert bundle.target_drug == "tetracycline"
    assert bundle.provenance.live_input is True
    assert bundle.provenance.fixture_trained_baseline is True
    assert bundle.summary.overall_status == V2AuditStatus.PASS
    assert bundle.summary.total_checks == 9
    assert {section.section_id for section in bundle.sections} == set(V2_AUDIT_REQUIRED_SECTIONS)


def test_v2_audit_bundle_accepts_warn_state() -> None:
    payload = load_audit_bundle_payload()
    _set_single_check_state(payload, section_id="evidence_graph", status="warn", live_ready=True)

    bundle = V2AuditBundle(**payload)

    assert bundle.summary.overall_status == V2AuditStatus.WARN
    assert bundle.summary.warning_checks == 1
    assert bundle.summary.live_ready is True


def test_v2_audit_bundle_accepts_fail_state() -> None:
    payload = load_audit_bundle_payload()
    _set_single_check_state(payload, section_id="execution_gate", status="fail", live_ready=False)

    bundle = V2AuditBundle(**payload)

    assert bundle.summary.overall_status == V2AuditStatus.FAIL
    assert bundle.summary.failed_checks == 1
    assert any(check.blocking for section in bundle.sections for check in section.checks)


def test_v2_audit_bundle_accepts_pending_state_when_live_ready_is_false() -> None:
    payload = load_audit_bundle_payload()
    _set_single_check_state(payload, section_id="openrouter_proof", status="pending", live_ready=False)

    bundle = V2AuditBundle(**payload)

    assert bundle.summary.overall_status == V2AuditStatus.PENDING
    assert bundle.summary.pending_checks == 1
    assert bundle.summary.live_ready is False


def test_v2_audit_bundle_rejects_summary_count_mismatch() -> None:
    payload = load_audit_bundle_payload()
    summary = payload["summary"]
    assert isinstance(summary, dict)
    summary["passing_checks"] = 8

    with pytest.raises(ValidationError, match="passing_checks"):
        V2AuditBundle(**payload)


def test_v2_audit_bundle_rejects_section_status_mismatch() -> None:
    payload = load_audit_bundle_payload()
    section = _section(payload, "reasoning_trace")
    section["status"] = "warn"

    with pytest.raises(ValidationError, match="status must match"):
        V2AuditBundle(**payload)


def test_v2_audit_bundle_rejects_missing_required_section() -> None:
    payload = load_audit_bundle_payload()
    sections = payload["sections"]
    assert isinstance(sections, list)
    payload["sections"] = [section for section in sections if section["section_id"] != "thesys_proof"]
    summary = payload["summary"]
    assert isinstance(summary, dict)
    summary["section_count"] = 8
    summary["total_checks"] = 8
    summary["passing_checks"] = 8

    with pytest.raises(ValidationError, match="required V2 audit section"):
        V2AuditBundle(**payload)


def test_v2_audit_bundle_rejects_live_fixture_input_ambiguity() -> None:
    payload = load_audit_bundle_payload()
    provenance = payload["provenance"]
    assert isinstance(provenance, dict)
    provenance["input_provenance"] = "fixture"

    with pytest.raises(ValidationError, match="live_input cannot be true"):
        V2AuditBundle(**payload)


def test_v2_audit_bundle_rejects_live_ready_pending_report() -> None:
    payload = load_audit_bundle_payload()
    _set_single_check_state(payload, section_id="openrouter_proof", status="pending", live_ready=True)

    with pytest.raises(ValidationError, match="live_ready cannot be true"):
        V2AuditBundle(**payload)


def test_v2_audit_bundle_rejects_provenance_label_that_hides_split() -> None:
    payload = load_audit_bundle_payload()
    provenance = payload["provenance"]
    assert isinstance(provenance, dict)
    provenance["provenance_split_label"] = "Ready for live demo"

    with pytest.raises(ValidationError, match="live input and fixture baseline"):
        V2AuditBundle(**payload)


def test_v2_audit_bundle_rejects_duplicate_sections() -> None:
    payload = load_audit_bundle_payload()
    sections = payload["sections"]
    assert isinstance(sections, list)
    sections.append(deepcopy(sections[0]))
    summary = payload["summary"]
    assert isinstance(summary, dict)
    summary["section_count"] = 10
    summary["total_checks"] = 10
    summary["passing_checks"] = 10

    with pytest.raises(ValidationError, match="must not repeat section_id"):
        V2AuditBundle(**payload)


def test_v2_audit_bundle_rejects_hidden_private_payloads() -> None:
    payload = load_audit_bundle_payload()
    payload["private_reasoning"] = "Hidden chain-of-thought must not be exposed."

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        V2AuditBundle(**payload)


def test_v2_audit_section_id_enum_lists_required_sections() -> None:
    assert V2AuditSectionId.OPENROUTER_PROOF in V2_AUDIT_REQUIRED_SECTIONS
    assert V2AuditSectionId.THESYS_PROOF in V2_AUDIT_REQUIRED_SECTIONS
    assert V2AuditSectionId.EVIDENCE_GRAPH in V2_AUDIT_REQUIRED_SECTIONS
