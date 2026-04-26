from __future__ import annotations

from datetime import date

import pytest

from app.contracts import (
    AssemblyQC,
    CalibrationStatus,
    MechanisticEvidence,
    OrganismConsistency,
    OrganismHint,
    PhenotypePrediction,
    PredictedPhenotype,
    QCStatus,
    SCHEMA_VERSION,
    SampleInput,
    SampleMetadata,
    SourceContext,
)


def test_schema_version_constant_uses_semver() -> None:
    assert SCHEMA_VERSION == "0.1.0"


def test_sample_input_accepts_local_path() -> None:
    sample = SampleInput(
        sample_id="Sample_001",
        organism_hint=OrganismHint.E_COLI,
        target_drug="Tetracycline",
        fasta_path="data/fixtures/sample.fa",
        metadata=SampleMetadata(
            accession="ACC-001",
            collection_date=date(2026, 4, 20),
            source_context=SourceContext.BOVINE_MILK,
        ),
    )

    assert sample.sample_id == "sample_001"
    assert sample.target_drug == "tetracycline"
    assert sample.schema_version == SCHEMA_VERSION


def test_sample_input_rejects_missing_path_and_uri() -> None:
    with pytest.raises(ValueError, match="Either fasta_path or fasta_uri must be provided"):
        SampleInput(
            sample_id="sample_001",
            target_drug="tetracycline",
        )


def test_sample_input_rejects_path_and_uri_together() -> None:
    with pytest.raises(ValueError, match="Provide only one of fasta_path or fasta_uri"):
        SampleInput(
            sample_id="sample_001",
            target_drug="tetracycline",
            fasta_path="sample.fa",
            fasta_uri="s3://bucket/sample.fa",
        )


def test_assembly_qc_tracks_missing_metadata_and_status() -> None:
    qc = AssemblyQC(
        job_id="job_001",
        sample_id="sample_001",
        target_drug="tetracycline",
        file_valid=True,
        sequence_count=12,
        total_bases=5032121,
        ambiguous_base_fraction=0.01,
        organism_consistency=OrganismConsistency.MATCH,
        missing_metadata_fields=["country"],
        qc_status=QCStatus.WARN,
        warnings=["country missing"],
    )

    assert qc.sample_id == "sample_001"
    assert qc.job_id == "job_001"
    assert qc.target_drug == "tetracycline"
    assert qc.qc_status == QCStatus.WARN
    assert qc.missing_metadata_fields == ["country"]


def test_mechanistic_evidence_requires_signal() -> None:
    with pytest.raises(ValueError, match="At least one of gene_symbol or mutation must be present"):
        MechanisticEvidence(
            job_id="job_001",
            sample_id="sample_001",
            target_drug="tetracycline",
            mechanism_class="efflux",
            support_level="supported",
            interpretation="mechanism noted in fixture output",
        )


def test_mechanistic_evidence_normalizes_drug_association() -> None:
    evidence = MechanisticEvidence(
        job_id="job_001",
        sample_id="sample_001",
        target_drug="tetracycline",
        gene_symbol="tetA",
        mechanism_class="efflux",
        drug_association=["Tetracycline", "doxycycline"],
        support_level="supported",
        interpretation="supporting signal present in normalized output",
    )

    assert evidence.drug_association == ["tetracycline", "doxycycline"]


def test_prediction_contract_validates_core_fields() -> None:
    prediction = PhenotypePrediction(
        job_id="job_001",
        sample_id="sample_001",
        target_drug="tetracycline",
        predicted_phenotype=PredictedPhenotype.RESISTANT,
        probability=0.83,
        calibration_status=CalibrationStatus.NOT_AVAILABLE,
        uncertainty_score=0.17,
        feature_set_version="kmers_v1",
        model_version="baseline_v1",
    )

    assert prediction.sample_id == "sample_001"
    assert prediction.target_drug == "tetracycline"
    assert prediction.probability == pytest.approx(0.83)
