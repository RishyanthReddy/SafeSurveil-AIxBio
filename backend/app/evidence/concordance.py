from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from app.contracts import MechanismSupportLevel, MechanisticEvidence, PredictedPhenotype
from app.contracts.common import normalize_slug_like


class MechanismConcordanceClassification(str, Enum):
    SUPPORTED = "supported"
    MISSING = "missing"
    AMBIGUOUS = "ambiguous"
    CONFLICTING = "conflicting"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class MechanismConcordanceResult:
    classification: MechanismConcordanceClassification
    mechanism_concordance: bool | None
    matched_gene_symbols: tuple[str, ...]
    explanation: str


def assess_mechanism_concordance(
    *,
    target_drug: str,
    predicted_phenotype: PredictedPhenotype | str,
    evidence_rows: Sequence[MechanisticEvidence],
) -> MechanismConcordanceResult:
    normalized_drug = normalize_slug_like(target_drug)
    phenotype_value = (
        predicted_phenotype.value
        if isinstance(predicted_phenotype, PredictedPhenotype)
        else normalize_slug_like(str(predicted_phenotype))
    )
    if phenotype_value != PredictedPhenotype.RESISTANT.value:
        return MechanismConcordanceResult(
            classification=MechanismConcordanceClassification.NOT_APPLICABLE,
            mechanism_concordance=None,
            matched_gene_symbols=(),
            explanation="Mechanism concordance is only applied to resistant predictions in the MVP.",
        )

    relevant_rows = [row for row in evidence_rows if normalized_drug in row.drug_association]
    matched_symbols = tuple(
        row.gene_symbol or row.mechanism_class
        for row in relevant_rows
    )
    if not relevant_rows:
        return MechanismConcordanceResult(
            classification=MechanismConcordanceClassification.MISSING,
            mechanism_concordance=False,
            matched_gene_symbols=(),
            explanation="No drug-specific mechanism evidence matched the target drug.",
        )

    conflicting_rows = [
        row
        for row in relevant_rows
        if row.mechanism_class in {"wild_type_marker", "susceptibility_marker"}
        or "does not support resistance" in row.interpretation.lower()
    ]
    if conflicting_rows:
        return MechanismConcordanceResult(
            classification=MechanismConcordanceClassification.CONFLICTING,
            mechanism_concordance=False,
            matched_gene_symbols=tuple(row.gene_symbol or row.mechanism_class for row in conflicting_rows),
            explanation="Evidence matched the target drug but conflicted with a resistant interpretation.",
        )

    supported_rows = [
        row
        for row in relevant_rows
        if row.support_level in {MechanismSupportLevel.SUPPORTED, MechanismSupportLevel.PARTIAL}
    ]
    if supported_rows:
        return MechanismConcordanceResult(
            classification=MechanismConcordanceClassification.SUPPORTED,
            mechanism_concordance=True,
            matched_gene_symbols=tuple(row.gene_symbol or row.mechanism_class for row in supported_rows),
            explanation="Drug-specific mechanism evidence supports the resistant prediction.",
        )

    return MechanismConcordanceResult(
        classification=MechanismConcordanceClassification.AMBIGUOUS,
        mechanism_concordance=None,
        matched_gene_symbols=matched_symbols,
        explanation="Only weak or screen-only mechanism evidence matched the target drug.",
    )
