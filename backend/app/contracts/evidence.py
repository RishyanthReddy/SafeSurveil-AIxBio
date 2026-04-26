from __future__ import annotations

from pydantic import Field, field_validator, model_validator

from .common import ContractModel, MechanismSupportLevel, normalize_slug_like


class MechanisticEvidence(ContractModel):
    job_id: str = Field(min_length=3, max_length=80)
    sample_id: str = Field(min_length=3, max_length=80)
    target_drug: str = Field(min_length=3, max_length=80)
    source_tool: str = Field(default="amrfinderplus", min_length=3, max_length=40)
    gene_symbol: str | None = Field(default=None, min_length=1, max_length=64)
    mutation: str | None = Field(default=None, min_length=1, max_length=128)
    mechanism_class: str = Field(min_length=2, max_length=120)
    drug_association: list[str] = Field(default_factory=list)
    support_level: MechanismSupportLevel
    interpretation: str = Field(min_length=5, max_length=240)
    raw_row_index: int | None = Field(default=None, ge=0)
    raw_artifact_id: str | None = Field(default=None, min_length=3, max_length=120)

    @model_validator(mode="after")
    def require_gene_or_mutation(self) -> "MechanisticEvidence":
        if not self.gene_symbol and not self.mutation:
            raise ValueError("At least one of gene_symbol or mutation must be present.")
        return self

    @field_validator("job_id", "sample_id", "target_drug", "source_tool")
    @classmethod
    def normalize_slug_fields(cls, value: str) -> str:
        return normalize_slug_like(value)

    @field_validator("drug_association")
    @classmethod
    def normalize_drug_association(cls, value: list[str]) -> list[str]:
        return [normalize_slug_like(item) for item in value]
