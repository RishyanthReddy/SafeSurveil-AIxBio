from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.contracts import AssemblyQC, QCStatus


class EvidenceFailureCode(str, Enum):
    INPUT_INVALID = "input_invalid"
    TOOL_MISSING = "tool_missing"
    COMMAND_TIMEOUT = "command_timeout"
    PARSE_FAILURE = "parse_failure"
    NO_HITS = "no_hits"
    NO_REFERENCE_SKETCH = "no_reference_sketch"
    FIXTURE_FALLBACK = "fixture_fallback"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class EvidenceFailure:
    code: EvidenceFailureCode
    stage: str
    retryable: bool
    user_message: str
    detail: str


def classify_validation_failure(qc: AssemblyQC) -> EvidenceFailure | None:
    if qc.qc_status != QCStatus.FAIL:
        return None
    detail = "; ".join(qc.warnings) if qc.warnings else "Validation failed."
    return EvidenceFailure(
        code=EvidenceFailureCode.INPUT_INVALID,
        stage="validation",
        retryable=False,
        user_message="Input validation failed for the evidence workflow.",
        detail=detail,
    )


def build_fixture_fallback_failure(stage: str, detail: str) -> EvidenceFailure:
    return EvidenceFailure(
        code=EvidenceFailureCode.FIXTURE_FALLBACK,
        stage=stage,
        retryable=True,
        user_message="Fixture fallback was used for this evidence step.",
        detail=detail,
    )


def build_tool_missing_failure(tool_name: str, stage: str) -> EvidenceFailure:
    return EvidenceFailure(
        code=EvidenceFailureCode.TOOL_MISSING,
        stage=stage,
        retryable=True,
        user_message=f"{tool_name} is unavailable in the local environment.",
        detail=f"{tool_name} executable was not found for stage {stage}.",
    )


def build_parse_failure(stage: str, detail: str) -> EvidenceFailure:
    return EvidenceFailure(
        code=EvidenceFailureCode.PARSE_FAILURE,
        stage=stage,
        retryable=False,
        user_message="A raw evidence output could not be parsed safely.",
        detail=detail,
    )
