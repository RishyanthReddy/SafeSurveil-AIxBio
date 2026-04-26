from __future__ import annotations

from datetime import date

from pydantic import Field, field_validator, model_validator

from .common import (
    ContractModel,
    OrganismConsistency,
    OrganismHint,
    ProvenanceSource,
    QCStatus,
    SourceContext,
    normalize_slug_like,
)

_ORGANISM_HINT_ALIASES = {
    "e_coli": OrganismHint.E_COLI.value,
    "escherichia_coli": OrganismHint.E_COLI.value,
    "s_aureus": OrganismHint.S_AUREUS.value,
    "staphylococcus_aureus": OrganismHint.S_AUREUS.value,
}


class SampleMetadata(ContractModel):
    accession: str | None = Field(default=None, max_length=64)
    collection_date: date | None = None
    source_context: SourceContext = Field(
        default=SourceContext.SURVEILLANCE_PROXY,
        alias="source",
    )
    country: str | None = Field(default=None, min_length=2, max_length=64)
    provenance_source: ProvenanceSource = ProvenanceSource.OTHER


class SampleInput(ContractModel):
    sample_id: str = Field(min_length=3, max_length=80)
    organism_hint: OrganismHint | None = None
    target_drug: str = Field(min_length=3, max_length=80)
    fasta_path: str | None = Field(default=None, min_length=1, max_length=512)
    fasta_uri: str | None = Field(default=None, min_length=1, max_length=512)
    metadata: SampleMetadata = Field(default_factory=SampleMetadata)

    @field_validator("organism_hint", mode="before")
    @classmethod
    def normalize_organism_hint(cls, value: OrganismHint | str | None) -> OrganismHint | str | None:
        if value is None or isinstance(value, OrganismHint):
            return value
        normalized = str(value).strip().lower().replace(" ", "_")
        return _ORGANISM_HINT_ALIASES.get(normalized, normalized)

    @model_validator(mode="after")
    def require_path_or_uri(self) -> "SampleInput":
        if not self.fasta_path and not self.fasta_uri:
            raise ValueError("Either fasta_path or fasta_uri must be provided.")
        return self

    @model_validator(mode="after")
    def reject_both_path_and_uri(self) -> "SampleInput":
        if self.fasta_path and self.fasta_uri:
            raise ValueError("Provide only one of fasta_path or fasta_uri for a single input.")
        return self

    @model_validator(mode="after")
    def validate_fasta_uri(self) -> "SampleInput":
        if self.fasta_uri and "://" not in self.fasta_uri:
            raise ValueError("fasta_uri must include a URI scheme.")
        return self

    @field_validator("sample_id", "target_drug")
    @classmethod
    def normalize_slug_fields(cls, value: str) -> str:
        return normalize_slug_like(value)


class AssemblyQC(ContractModel):
    job_id: str = Field(min_length=3, max_length=80)
    sample_id: str = Field(min_length=3, max_length=80)
    target_drug: str = Field(min_length=3, max_length=80)
    file_valid: bool
    sequence_count: int = Field(ge=0)
    total_bases: int = Field(ge=0)
    ambiguous_base_fraction: float = Field(ge=0.0, le=1.0)
    organism_consistency: OrganismConsistency = OrganismConsistency.UNKNOWN
    missing_metadata_fields: list[str] = Field(default_factory=list)
    qc_status: QCStatus
    warnings: list[str] = Field(default_factory=list)

    @field_validator("job_id", "sample_id", "target_drug")
    @classmethod
    def normalize_slug_fields(cls, value: str) -> str:
        return normalize_slug_like(value)
