from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION = "0.1.0"
_SAFE_TOKEN_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


class ContractModel(BaseModel):
    """Shared base model for schema-backed payloads."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        str_strip_whitespace=True,
        validate_assignment=True,
        use_enum_values=False,
    )

    schema_version: str = Field(default=SCHEMA_VERSION)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: str) -> str:
        if not re.fullmatch(r"\d+\.\d+\.\d+", value):
            raise ValueError("schema_version must use semantic version format, for example 0.1.0.")
        return value


class OrganismHint(str, Enum):
    E_COLI = "e_coli"
    S_AUREUS = "s_aureus"


class SourceContext(str, Enum):
    BOVINE_MILK = "bovine_mastitis"
    SURVEILLANCE_PROXY = "agricultural_surveillance_proxy"
    FIXTURE = "fixture"
    LOCAL = "local_upload"
    OTHER = "other"


class ProvenanceSource(str, Enum):
    BV_BRC = "bv_brc"
    NCBI = "ncbi_datasets"
    FIXTURE = "fixture"
    LOCAL = "local_upload"
    OTHER = "other"


class QCStatus(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


class OrganismConsistency(str, Enum):
    MATCH = "match"
    MISMATCH = "mismatch"
    UNKNOWN = "unknown"


class MechanismSupportLevel(str, Enum):
    SUPPORTED = "supported"
    PARTIAL = "partial"
    WEAK = "weak"
    SCREEN_ONLY = "screen_only"


class PredictedPhenotype(str, Enum):
    RESISTANT = "resistant"
    SUSCEPTIBLE = "susceptible"
    INTERMEDIATE = "intermediate"
    UNKNOWN = "unknown"


class CalibrationStatus(str, Enum):
    CALIBRATED = "calibrated"
    UNCALIBRATED = "uncalibrated"
    NOT_AVAILABLE = "not_available"


class SplitContext(str, Enum):
    SMOKE = "smoke"
    RANDOM = "random"
    LINEAGE_AWARE = "lineage_aware"
    FIXTURE = "fixture"


def normalize_slug_like(value: str) -> str:
    lowered = value.strip().lower().replace(" ", "_")
    if not _SAFE_TOKEN_PATTERN.fullmatch(lowered):
        msg = "Value must use lowercase letters, numbers, dots, underscores, or hyphens."
        raise ValueError(msg)
    return lowered
