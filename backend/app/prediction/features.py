from __future__ import annotations

import json
from pathlib import Path

from app.contracts import (
    AssemblyQC,
    BaselineFeatureStrategy,
    FeatureMatrixArtifact,
    FeatureStorageFormat,
    FeatureVectorRecord,
    MechanismSupportLevel,
    MechanisticEvidence,
    NoveltyAssessment,
    SampleInput,
)

DEFAULT_BASELINE_FEATURE_SET_VERSION = "baseline_hybrid_v1"
DEFAULT_FEATURE_MATRIX_FIXTURE_PATH = "data/fixtures/prediction/fixture_feature_matrix.json"
DEFAULT_TARGET_SCOPE = "e_coli_tetracycline_smoke"
DEFAULT_BINARY_FEATURES = (
    "supported_target_mechanism_present",
    "any_target_mechanism_present",
    "tet_marker_present",
    "qc_warning_present",
    "ambiguity_flag",
)
DEFAULT_NUMERIC_FEATURES = (
    "metadata_complete",
    "novelty_score",
)
_EXPECTED_METADATA_FIELDS = 3


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def build_baseline_feature_strategy(repo_root: Path | None = None) -> BaselineFeatureStrategy:
    root = repo_root or _repo_root()
    artifact_path = (root / DEFAULT_FEATURE_MATRIX_FIXTURE_PATH).resolve()
    try:
        display_path = artifact_path.relative_to(root.resolve()).as_posix()
    except ValueError:
        display_path = artifact_path.as_posix()

    return BaselineFeatureStrategy(
        feature_set_version=DEFAULT_BASELINE_FEATURE_SET_VERSION,
        target_scope=DEFAULT_TARGET_SCOPE,
        primary_feature_family="hybrid_sparse_signal_and_risk",
        storage_format=FeatureStorageFormat.JSON,
        artifact_path=display_path,
        binary_features=list(DEFAULT_BINARY_FEATURES),
        numeric_features=list(DEFAULT_NUMERIC_FEATURES),
        notes=[
            "Phase 5 uses a fixture-backed, interpretable hybrid feature set for the first smoke model.",
            "Binary features capture target-specific mechanism support, while numeric features capture novelty and metadata quality.",
            "The first baseline stays repo-light by using JSON feature matrices before any heavier sparse storage is introduced.",
        ],
    )


def _metadata_completeness(qc: AssemblyQC) -> float:
    missing_count = min(len(qc.missing_metadata_fields), _EXPECTED_METADATA_FIELDS)
    return round(max(0.0, 1.0 - (missing_count / _EXPECTED_METADATA_FIELDS)), 6)


def _ambiguity_flag(qc: AssemblyQC, novelty: NoveltyAssessment) -> float:
    return 1.0 if qc.ambiguous_base_fraction > 0.02 or novelty.uncertainty_flag else 0.0


def _validate_feature_input_context(
    sample: SampleInput,
    *,
    qc: AssemblyQC,
    evidence_rows: list[MechanisticEvidence],
    novelty: NoveltyAssessment,
) -> None:
    expected_sample_id = sample.sample_id
    expected_target_drug = sample.target_drug
    expected_job_id = novelty.job_id
    mismatches: list[str] = []
    if qc.sample_id != expected_sample_id:
        mismatches.append(f"qc={qc.sample_id}")
    if qc.target_drug != expected_target_drug:
        mismatches.append(f"qc_target={qc.target_drug}")
    if qc.job_id != expected_job_id:
        mismatches.append(f"qc_job={qc.job_id}")
    if novelty.sample_id != expected_sample_id:
        mismatches.append(f"novelty={novelty.sample_id}")
    if novelty.target_drug != expected_target_drug:
        mismatches.append(f"novelty_target={novelty.target_drug}")

    evidence_sample_ids = sorted(
        {row.sample_id for row in evidence_rows if row.sample_id != expected_sample_id}
    )
    if evidence_sample_ids:
        mismatches.append(f"evidence={', '.join(evidence_sample_ids)}")
    evidence_target_drugs = sorted(
        {row.target_drug for row in evidence_rows if row.target_drug != expected_target_drug}
    )
    if evidence_target_drugs:
        mismatches.append(f"evidence_target={', '.join(evidence_target_drugs)}")
    evidence_job_ids = sorted(
        {row.job_id for row in evidence_rows if row.job_id != expected_job_id}
    )
    if evidence_job_ids:
        mismatches.append(f"evidence_job={', '.join(evidence_job_ids)}")

    if mismatches:
        joined_mismatches = "; ".join(mismatches)
        raise ValueError(
            f"Feature extraction inputs must match sample_id {expected_sample_id}, target_drug "
            f"{expected_target_drug}, and job_id {expected_job_id}: {joined_mismatches}"
        )


def extract_baseline_feature_vector(
    sample: SampleInput,
    *,
    qc: AssemblyQC,
    evidence_rows: list[MechanisticEvidence],
    novelty: NoveltyAssessment,
    feature_set_version: str = DEFAULT_BASELINE_FEATURE_SET_VERSION,
) -> FeatureVectorRecord:
    _validate_feature_input_context(
        sample,
        qc=qc,
        evidence_rows=evidence_rows,
        novelty=novelty,
    )
    relevant_rows = [row for row in evidence_rows if sample.target_drug in row.drug_association]
    supported_rows = [
        row
        for row in relevant_rows
        if row.support_level in {MechanismSupportLevel.SUPPORTED, MechanismSupportLevel.PARTIAL}
    ]
    tet_marker_present = any(
        (row.gene_symbol or "").lower().startswith("tet")
        for row in relevant_rows
    )

    return FeatureVectorRecord(
        job_id=novelty.job_id,
        sample_id=sample.sample_id,
        target_drug=sample.target_drug,
        feature_set_version=feature_set_version,
        values={
            "supported_target_mechanism_present": 1.0 if supported_rows else 0.0,
            "any_target_mechanism_present": 1.0 if relevant_rows else 0.0,
            "tet_marker_present": 1.0 if tet_marker_present else 0.0,
            "qc_warning_present": 1.0 if qc.qc_status.value != "pass" else 0.0,
            "ambiguity_flag": _ambiguity_flag(qc, novelty),
            "metadata_complete": _metadata_completeness(qc),
            "novelty_score": round(novelty.novelty_score or 0.0, 6),
        },
    )


def load_feature_matrix_artifact(path: Path) -> FeatureMatrixArtifact:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return FeatureMatrixArtifact(**payload)
