from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.contracts import (
    ReasoningTrace,
    ReasoningTraceCoverage,
    REASONING_TRACE_REQUIRED_STEP_TYPES,
    REASONING_TRACE_SCHEMA_VERSION,
)

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "v2_reasoning_trace_example.json"


def load_reasoning_trace_payload() -> dict[str, object]:
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


def test_reasoning_trace_fixture_validates_and_normalizes_source_refs() -> None:
    trace = ReasoningTrace(**load_reasoning_trace_payload())

    assert trace.schema_version == REASONING_TRACE_SCHEMA_VERSION
    assert trace.job_id == "job_trace_001"
    assert trace.sample_id == "sample_ecoli_001"
    assert trace.target_drug == "tetracycline"
    assert trace.coverage.coverage_ratio == pytest.approx(1.0)
    assert trace.steps[0].evidence_refs[0].evidence_id == "decision_object__summary"
    assert trace.steps[0].evidence_refs[0].source_type == "context"
    assert trace.caveats[0].caveat_id == "novelty_uncertainty"


def test_reasoning_trace_accepts_structured_source_references() -> None:
    payload = load_reasoning_trace_payload()
    first_step = payload["steps"][0]
    assert isinstance(first_step, dict)
    first_step["evidence_refs"] = [
        {
            "evidence_id": "Decision_Object__Summary",
            "source_type": "Decision Object",
            "label": "Decision summary",
            "detail": "Persisted decision object summary section.",
        }
    ]

    trace = ReasoningTrace(**payload)

    source_ref = trace.steps[0].evidence_refs[0]
    assert source_ref.evidence_id == "decision_object__summary"
    assert source_ref.source_type == "decision_object"
    assert source_ref.label == "Decision summary"


def test_reasoning_trace_rejects_out_of_order_steps() -> None:
    payload = load_reasoning_trace_payload()
    steps = payload["steps"]
    assert isinstance(steps, list)
    steps[0], steps[1] = steps[1], steps[0]
    for index, step in enumerate(steps, start=1):
        assert isinstance(step, dict)
        step["step_number"] = index

    with pytest.raises(ValidationError, match="required biological reasoning order"):
        ReasoningTrace(**payload)


def test_reasoning_trace_rejects_step_without_evidence_refs() -> None:
    payload = load_reasoning_trace_payload()
    steps = payload["steps"]
    assert isinstance(steps, list)
    first_step = steps[0]
    assert isinstance(first_step, dict)
    first_step["evidence_refs"] = []

    with pytest.raises(ValidationError, match="requires at least one evidence reference"):
        ReasoningTrace(**payload)


def test_reasoning_trace_rejects_coverage_that_does_not_match_steps() -> None:
    payload = load_reasoning_trace_payload()
    coverage = payload["coverage"]
    assert isinstance(coverage, dict)
    coverage["present_step_types"] = coverage["present_step_types"][:-1]
    coverage["missing_step_types"] = ["final_triage"]
    coverage["present_steps"] = 7
    coverage["coverage_ratio"] = 7 / 8

    with pytest.raises(ValidationError, match="coverage present_step_types must match"):
        ReasoningTrace(**payload)


def test_reasoning_trace_coverage_rejects_bad_ratio() -> None:
    with pytest.raises(ValidationError, match="coverage_ratio"):
        ReasoningTraceCoverage(
            required_step_types=list(REASONING_TRACE_REQUIRED_STEP_TYPES),
            present_step_types=list(REASONING_TRACE_REQUIRED_STEP_TYPES),
            missing_step_types=[],
            required_steps=8,
            present_steps=8,
            coverage_ratio=0.5,
        )


def test_reasoning_trace_rejects_unknown_caveat_id() -> None:
    payload = load_reasoning_trace_payload()
    steps = payload["steps"]
    assert isinstance(steps, list)
    first_step = steps[0]
    assert isinstance(first_step, dict)
    first_step["caveat_ids"] = ["missing_caveat"]

    with pytest.raises(ValidationError, match="caveat_ids must reference declared caveats"):
        ReasoningTrace(**payload)


def test_reasoning_trace_forbids_hidden_private_reasoning_fields() -> None:
    payload = load_reasoning_trace_payload()
    steps = payload["steps"]
    assert isinstance(steps, list)
    first_step = deepcopy(steps[0])
    assert isinstance(first_step, dict)
    first_step["private_reasoning"] = "Hidden chain-of-thought must not be exposed."
    steps[0] = first_step

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ReasoningTrace(**payload)
