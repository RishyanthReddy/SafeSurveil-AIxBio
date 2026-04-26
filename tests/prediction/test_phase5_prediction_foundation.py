from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.contracts import (
    ActionabilityFeatures,
    AssemblyQC,
    CalibrationStatus,
    PredictedPhenotype,
    RationaleCode,
    SampleInput,
    TriageOutcome,
)
from app.evidence import run_evidence_smoke
from app.prediction import (
    DEFAULT_ACTIONABILITY_THRESHOLD_VERSION,
    DEFAULT_FEATURE_MATRIX_FIXTURE_PATH,
    DEFAULT_TRAINING_LABEL_FIXTURE_PATH,
    ActionabilityPolicy,
    PredictionMechanismClass,
    apply_triage_policy,
    build_actionability_features,
    build_baseline_feature_strategy,
    build_decision_object,
    classify_mechanism_prediction_concordance,
    extract_baseline_feature_vector,
    load_feature_matrix_artifact,
    load_training_label_table,
    run_baseline_training_workflow,
    run_prediction_inference,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
JOB_ID = "job_phase_5_smoke"


def load_smoke_sample() -> SampleInput:
    payload = json.loads((REPO_ROOT / "data/fixtures/smoke/sample_001.metadata.json").read_text(encoding="utf-8"))
    return SampleInput(
        sample_id=payload["sample_id"],
        organism_hint=payload["organism_hint"],
        target_drug=payload["target_drug"],
        fasta_path=payload["fasta_path"],
        metadata=payload["metadata"],
    )


def build_smoke_prediction_context(tmp_path: Path):
    sample = load_smoke_sample()
    evidence_result = run_evidence_smoke(
        sample,
        output_dir=tmp_path / "evidence",
        fixture_mode=True,
        repo_root=REPO_ROOT,
        job_id=JOB_ID,
    )
    qc = AssemblyQC.model_validate_json(evidence_result.qc_path.read_text(encoding="utf-8"))
    label_table = load_training_label_table(REPO_ROOT / DEFAULT_TRAINING_LABEL_FIXTURE_PATH)
    feature_matrix = load_feature_matrix_artifact(REPO_ROOT / DEFAULT_FEATURE_MATRIX_FIXTURE_PATH)
    training_result = run_baseline_training_workflow(
        label_table=label_table,
        feature_matrix=feature_matrix,
        output_dir=tmp_path / "model",
    )
    feature_vector = extract_baseline_feature_vector(
        sample,
        qc=qc,
        evidence_rows=list(evidence_result.mechanistic_evidence),
        novelty=evidence_result.novelty_assessment,
    )
    prediction = run_prediction_inference(
        job_id=JOB_ID,
        sample_id=sample.sample_id,
        target_drug=sample.target_drug,
        feature_vector=feature_vector,
        model_artifact=training_result.model_artifact,
        calibration_policy=training_result.calibration_policy,
    )
    return sample, qc, list(evidence_result.mechanistic_evidence), evidence_result.novelty_assessment, prediction


def test_feature_strategy_points_to_fixture_backed_matrix() -> None:
    strategy = build_baseline_feature_strategy(repo_root=REPO_ROOT)

    assert strategy.feature_set_version == "baseline_hybrid_v1"
    assert strategy.target_scope == "e_coli_tetracycline_smoke"
    assert strategy.artifact_path == DEFAULT_FEATURE_MATRIX_FIXTURE_PATH
    assert "supported_target_mechanism_present" in strategy.binary_features
    assert "novelty_score" in strategy.numeric_features


def test_training_label_table_is_split_aware_and_traceable() -> None:
    table = load_training_label_table(REPO_ROOT / DEFAULT_TRAINING_LABEL_FIXTURE_PATH)

    assert table.organism.value == "e_coli"
    assert table.target_drug == "tetracycline"
    assert table.split_context.value == "fixture"
    assert table.split_id == "fixture_lineage_smoke_v1"
    assert len(table.rows) == 6
    assert sum(1 for row in table.rows if row.included_in_training) == 5
    assert any(row.exclusion_reason == "excluded_intermediate_fixture_row" for row in table.rows)


def test_feature_matrix_fixture_matches_strategy() -> None:
    matrix = load_feature_matrix_artifact(REPO_ROOT / DEFAULT_FEATURE_MATRIX_FIXTURE_PATH)
    strategy = build_baseline_feature_strategy(repo_root=REPO_ROOT)

    assert matrix.feature_set_version == strategy.feature_set_version
    assert matrix.organism.value == "e_coli"
    assert matrix.target_drug == "tetracycline"
    assert matrix.split_context.value == "fixture"
    assert matrix.split_id == "fixture_lineage_smoke_v1"
    assert set(matrix.feature_names) == set(strategy.binary_features + strategy.numeric_features)
    assert len(matrix.rows) == 6


def test_feature_extraction_converts_evidence_outputs_into_smoke_vector(tmp_path: Path) -> None:
    sample = load_smoke_sample()
    evidence_result = run_evidence_smoke(
        sample,
        output_dir=tmp_path / "evidence",
        fixture_mode=True,
        repo_root=REPO_ROOT,
        job_id=JOB_ID,
    )

    feature_vector = extract_baseline_feature_vector(
        sample,
        qc=AssemblyQC.model_validate_json(evidence_result.qc_path.read_text(encoding="utf-8")),
        evidence_rows=list(evidence_result.mechanistic_evidence),
        novelty=evidence_result.novelty_assessment,
    )

    assert feature_vector.sample_id == "sample_001"
    assert feature_vector.job_id == JOB_ID
    assert feature_vector.target_drug == "tetracycline"
    assert feature_vector.values["supported_target_mechanism_present"] == 1.0
    assert feature_vector.values["tet_marker_present"] == 1.0
    assert feature_vector.values["novelty_score"] == 0.34


def test_feature_extraction_rejects_mismatched_input_context(tmp_path: Path) -> None:
    sample = load_smoke_sample()
    evidence_result = run_evidence_smoke(
        sample,
        output_dir=tmp_path / "evidence",
        fixture_mode=True,
        repo_root=REPO_ROOT,
        job_id=JOB_ID,
    )
    qc = AssemblyQC.model_validate_json(evidence_result.qc_path.read_text(encoding="utf-8"))
    mismatched_qc = qc.model_copy(update={"sample_id": "other_sample_003"})
    evidence_rows = list(evidence_result.mechanistic_evidence)
    mismatched_rows = [
        evidence_rows[0].model_copy(update={"sample_id": "other_sample_001"}),
        *evidence_rows[1:],
    ]
    mismatched_novelty = evidence_result.novelty_assessment.model_copy(
        update={"sample_id": "other_sample_002"}
    )

    with pytest.raises(ValueError, match="Feature extraction inputs must match sample_id"):
        extract_baseline_feature_vector(
            sample,
            qc=mismatched_qc,
            evidence_rows=mismatched_rows,
            novelty=mismatched_novelty,
        )


def test_feature_extraction_rejects_mismatched_target_context(tmp_path: Path) -> None:
    sample = load_smoke_sample()
    evidence_result = run_evidence_smoke(
        sample,
        output_dir=tmp_path / "evidence",
        fixture_mode=True,
        repo_root=REPO_ROOT,
        job_id=JOB_ID,
    )
    qc = AssemblyQC.model_validate_json(evidence_result.qc_path.read_text(encoding="utf-8"))
    evidence_rows = list(evidence_result.mechanistic_evidence)
    mismatched_rows = [
        evidence_rows[0].model_copy(update={"target_drug": "ampicillin"}),
        *evidence_rows[1:],
    ]
    mismatched_novelty = evidence_result.novelty_assessment.model_copy(
        update={"target_drug": "ampicillin"}
    )

    with pytest.raises(ValueError, match="target_drug"):
        extract_baseline_feature_vector(
            sample,
            qc=qc,
            evidence_rows=mismatched_rows,
            novelty=mismatched_novelty,
        )


def test_feature_extraction_rejects_mismatched_job_context(tmp_path: Path) -> None:
    sample = load_smoke_sample()
    evidence_result = run_evidence_smoke(
        sample,
        output_dir=tmp_path / "evidence",
        fixture_mode=True,
        repo_root=REPO_ROOT,
        job_id=JOB_ID,
    )
    qc = AssemblyQC.model_validate_json(evidence_result.qc_path.read_text(encoding="utf-8"))
    evidence_rows = list(evidence_result.mechanistic_evidence)
    mismatched_rows = [
        evidence_rows[0].model_copy(update={"job_id": "job_phase_5_other"}),
        *evidence_rows[1:],
    ]

    with pytest.raises(ValueError, match="job_id"):
        extract_baseline_feature_vector(
            sample,
            qc=qc,
            evidence_rows=mismatched_rows,
            novelty=evidence_result.novelty_assessment,
        )


def test_feature_extraction_rejects_stale_qc_from_prior_job(tmp_path: Path) -> None:
    sample = load_smoke_sample()
    evidence_result = run_evidence_smoke(
        sample,
        output_dir=tmp_path / "evidence",
        fixture_mode=True,
        repo_root=REPO_ROOT,
        job_id=JOB_ID,
    )
    stale_qc = AssemblyQC.model_validate_json(
        evidence_result.qc_path.read_text(encoding="utf-8")
    ).model_copy(update={"job_id": "job_phase_5_prior"})

    with pytest.raises(ValueError, match="qc_job"):
        extract_baseline_feature_vector(
            sample,
            qc=stale_qc,
            evidence_rows=list(evidence_result.mechanistic_evidence),
            novelty=evidence_result.novelty_assessment,
        )


def test_training_workflow_saves_model_and_calibration_policy(tmp_path: Path) -> None:
    label_table = load_training_label_table(REPO_ROOT / DEFAULT_TRAINING_LABEL_FIXTURE_PATH)
    feature_matrix = load_feature_matrix_artifact(REPO_ROOT / DEFAULT_FEATURE_MATRIX_FIXTURE_PATH)

    result = run_baseline_training_workflow(
        label_table=label_table,
        feature_matrix=feature_matrix,
        output_dir=tmp_path,
    )

    assert result.model_artifact_path.exists()
    assert result.calibration_policy_path.exists()
    assert result.model_artifact.training_sample_count == 5
    assert result.model_artifact.resistant_sample_count == 3
    assert result.model_artifact.susceptible_sample_count == 2
    assert result.calibration_policy.calibration_status == CalibrationStatus.NOT_AVAILABLE


def test_prediction_inference_returns_schema_valid_prediction(tmp_path: Path) -> None:
    sample = load_smoke_sample()
    evidence_result = run_evidence_smoke(
        sample,
        output_dir=tmp_path / "evidence",
        fixture_mode=True,
        repo_root=REPO_ROOT,
        job_id=JOB_ID,
    )
    label_table = load_training_label_table(REPO_ROOT / DEFAULT_TRAINING_LABEL_FIXTURE_PATH)
    feature_matrix = load_feature_matrix_artifact(REPO_ROOT / DEFAULT_FEATURE_MATRIX_FIXTURE_PATH)
    training_result = run_baseline_training_workflow(
        label_table=label_table,
        feature_matrix=feature_matrix,
        output_dir=tmp_path / "model",
    )
    feature_vector = extract_baseline_feature_vector(
        sample,
        qc=AssemblyQC.model_validate_json(evidence_result.qc_path.read_text(encoding="utf-8")),
        evidence_rows=list(evidence_result.mechanistic_evidence),
        novelty=evidence_result.novelty_assessment,
    )

    prediction = run_prediction_inference(
        job_id=JOB_ID,
        sample_id=sample.sample_id,
        target_drug=sample.target_drug,
        feature_vector=feature_vector,
        model_artifact=training_result.model_artifact,
        calibration_policy=training_result.calibration_policy,
    )

    assert prediction.predicted_phenotype == PredictedPhenotype.RESISTANT
    assert prediction.probability > 0.5
    assert prediction.uncertainty_score is not None
    assert prediction.calibration_status == CalibrationStatus.NOT_AVAILABLE
    assert prediction.model_training_split_context.value == "fixture"
    assert prediction.input_source_context.value == "other"
    assert prediction.input_provenance_source.value == "other"
    assert any("fixture-backed" in warning for warning in prediction.warnings)


def test_prediction_inference_rejects_feature_set_mismatch(tmp_path: Path) -> None:
    sample = load_smoke_sample()
    evidence_result = run_evidence_smoke(
        sample,
        output_dir=tmp_path / "evidence",
        fixture_mode=True,
        repo_root=REPO_ROOT,
        job_id=JOB_ID,
    )
    label_table = load_training_label_table(REPO_ROOT / DEFAULT_TRAINING_LABEL_FIXTURE_PATH)
    feature_matrix = load_feature_matrix_artifact(REPO_ROOT / DEFAULT_FEATURE_MATRIX_FIXTURE_PATH)
    training_result = run_baseline_training_workflow(
        label_table=label_table,
        feature_matrix=feature_matrix,
        output_dir=tmp_path / "model",
    )
    feature_vector = extract_baseline_feature_vector(
        sample,
        qc=AssemblyQC.model_validate_json(evidence_result.qc_path.read_text(encoding="utf-8")),
        evidence_rows=list(evidence_result.mechanistic_evidence),
        novelty=evidence_result.novelty_assessment,
    ).model_copy(update={"feature_set_version": "different_feature_set_v1"})

    with pytest.raises(ValueError, match="Feature set version mismatch"):
        run_prediction_inference(
            job_id=JOB_ID,
            sample_id=sample.sample_id,
            target_drug=sample.target_drug,
            feature_vector=feature_vector,
            model_artifact=training_result.model_artifact,
            calibration_policy=training_result.calibration_policy,
        )


def test_prediction_inference_rejects_missing_model_feature(tmp_path: Path) -> None:
    sample = load_smoke_sample()
    evidence_result = run_evidence_smoke(
        sample,
        output_dir=tmp_path / "evidence",
        fixture_mode=True,
        repo_root=REPO_ROOT,
        job_id=JOB_ID,
    )
    label_table = load_training_label_table(REPO_ROOT / DEFAULT_TRAINING_LABEL_FIXTURE_PATH)
    feature_matrix = load_feature_matrix_artifact(REPO_ROOT / DEFAULT_FEATURE_MATRIX_FIXTURE_PATH)
    training_result = run_baseline_training_workflow(
        label_table=label_table,
        feature_matrix=feature_matrix,
        output_dir=tmp_path / "model",
    )
    feature_vector = extract_baseline_feature_vector(
        sample,
        qc=AssemblyQC.model_validate_json(evidence_result.qc_path.read_text(encoding="utf-8")),
        evidence_rows=list(evidence_result.mechanistic_evidence),
        novelty=evidence_result.novelty_assessment,
    )
    incomplete_vector = feature_vector.model_copy(
        update={
            "values": {
                key: value
                for key, value in feature_vector.values.items()
                if key != "novelty_score"
            }
        }
    )

    with pytest.raises(ValueError, match="missing trained model features"):
        run_prediction_inference(
            job_id=JOB_ID,
            sample_id=sample.sample_id,
            target_drug=sample.target_drug,
            feature_vector=incomplete_vector,
            model_artifact=training_result.model_artifact,
            calibration_policy=training_result.calibration_policy,
        )


def test_prediction_inference_rejects_mismatched_sample_id(tmp_path: Path) -> None:
    sample = load_smoke_sample()
    evidence_result = run_evidence_smoke(
        sample,
        output_dir=tmp_path / "evidence",
        fixture_mode=True,
        repo_root=REPO_ROOT,
        job_id=JOB_ID,
    )
    label_table = load_training_label_table(REPO_ROOT / DEFAULT_TRAINING_LABEL_FIXTURE_PATH)
    feature_matrix = load_feature_matrix_artifact(REPO_ROOT / DEFAULT_FEATURE_MATRIX_FIXTURE_PATH)
    training_result = run_baseline_training_workflow(
        label_table=label_table,
        feature_matrix=feature_matrix,
        output_dir=tmp_path / "model",
    )
    feature_vector = extract_baseline_feature_vector(
        sample,
        qc=AssemblyQC.model_validate_json(evidence_result.qc_path.read_text(encoding="utf-8")),
        evidence_rows=list(evidence_result.mechanistic_evidence),
        novelty=evidence_result.novelty_assessment,
    )

    with pytest.raises(ValueError, match="sample_id must match"):
        run_prediction_inference(
            job_id=JOB_ID,
            sample_id="different_sample_001",
            target_drug=sample.target_drug,
            feature_vector=feature_vector,
            model_artifact=training_result.model_artifact,
            calibration_policy=training_result.calibration_policy,
        )


def test_prediction_inference_rejects_mismatched_target_drug(tmp_path: Path) -> None:
    sample = load_smoke_sample()
    evidence_result = run_evidence_smoke(
        sample,
        output_dir=tmp_path / "evidence",
        fixture_mode=True,
        repo_root=REPO_ROOT,
        job_id=JOB_ID,
    )
    label_table = load_training_label_table(REPO_ROOT / DEFAULT_TRAINING_LABEL_FIXTURE_PATH)
    feature_matrix = load_feature_matrix_artifact(REPO_ROOT / DEFAULT_FEATURE_MATRIX_FIXTURE_PATH)
    training_result = run_baseline_training_workflow(
        label_table=label_table,
        feature_matrix=feature_matrix,
        output_dir=tmp_path / "model",
    )
    feature_vector = extract_baseline_feature_vector(
        sample,
        qc=AssemblyQC.model_validate_json(evidence_result.qc_path.read_text(encoding="utf-8")),
        evidence_rows=list(evidence_result.mechanistic_evidence),
        novelty=evidence_result.novelty_assessment,
    )

    with pytest.raises(ValueError, match="target_drug must match"):
        run_prediction_inference(
            job_id=JOB_ID,
            sample_id=sample.sample_id,
            target_drug="ciprofloxacin",
            feature_vector=feature_vector,
            model_artifact=training_result.model_artifact,
            calibration_policy=training_result.calibration_policy,
        )


def test_prediction_inference_rejects_mismatched_feature_vector_context(tmp_path: Path) -> None:
    sample = load_smoke_sample()
    evidence_result = run_evidence_smoke(
        sample,
        output_dir=tmp_path / "evidence",
        fixture_mode=True,
        repo_root=REPO_ROOT,
        job_id=JOB_ID,
    )
    label_table = load_training_label_table(REPO_ROOT / DEFAULT_TRAINING_LABEL_FIXTURE_PATH)
    feature_matrix = load_feature_matrix_artifact(REPO_ROOT / DEFAULT_FEATURE_MATRIX_FIXTURE_PATH)
    training_result = run_baseline_training_workflow(
        label_table=label_table,
        feature_matrix=feature_matrix,
        output_dir=tmp_path / "model",
    )
    feature_vector = extract_baseline_feature_vector(
        sample,
        qc=AssemblyQC.model_validate_json(evidence_result.qc_path.read_text(encoding="utf-8")),
        evidence_rows=list(evidence_result.mechanistic_evidence),
        novelty=evidence_result.novelty_assessment,
    )
    wrong_job_vector = feature_vector.model_copy(update={"job_id": "job_phase_5_other"})
    wrong_drug_vector = feature_vector.model_copy(update={"target_drug": "ciprofloxacin"})

    with pytest.raises(ValueError, match="job_id must match"):
        run_prediction_inference(
            job_id=JOB_ID,
            sample_id=sample.sample_id,
            target_drug=sample.target_drug,
            feature_vector=wrong_job_vector,
            model_artifact=training_result.model_artifact,
            calibration_policy=training_result.calibration_policy,
        )

    with pytest.raises(ValueError, match="target_drug must match the feature vector"):
        run_prediction_inference(
            job_id=JOB_ID,
            sample_id=sample.sample_id,
            target_drug=sample.target_drug,
            feature_vector=wrong_drug_vector,
            model_artifact=training_result.model_artifact,
            calibration_policy=training_result.calibration_policy,
        )


def test_prediction_inference_rejects_stale_calibration_policy(tmp_path: Path) -> None:
    sample = load_smoke_sample()
    evidence_result = run_evidence_smoke(
        sample,
        output_dir=tmp_path / "evidence",
        fixture_mode=True,
        repo_root=REPO_ROOT,
        job_id=JOB_ID,
    )
    label_table = load_training_label_table(REPO_ROOT / DEFAULT_TRAINING_LABEL_FIXTURE_PATH)
    feature_matrix = load_feature_matrix_artifact(REPO_ROOT / DEFAULT_FEATURE_MATRIX_FIXTURE_PATH)
    training_result = run_baseline_training_workflow(
        label_table=label_table,
        feature_matrix=feature_matrix,
        output_dir=tmp_path / "model",
    )
    feature_vector = extract_baseline_feature_vector(
        sample,
        qc=AssemblyQC.model_validate_json(evidence_result.qc_path.read_text(encoding="utf-8")),
        evidence_rows=list(evidence_result.mechanistic_evidence),
        novelty=evidence_result.novelty_assessment,
    )
    count_mismatch_policy = training_result.calibration_policy.model_copy(
        update={"observed_training_sample_count": training_result.model_artifact.training_sample_count + 1}
    )
    status_mismatch_policy = training_result.calibration_policy.model_copy(
        update={"calibration_status": CalibrationStatus.CALIBRATED}
    )

    with pytest.raises(ValueError, match="training sample count"):
        run_prediction_inference(
            job_id=JOB_ID,
            sample_id=sample.sample_id,
            target_drug=sample.target_drug,
            feature_vector=feature_vector,
            model_artifact=training_result.model_artifact,
            calibration_policy=count_mismatch_policy,
        )

    with pytest.raises(ValueError, match="calibration_status"):
        run_prediction_inference(
            job_id=JOB_ID,
            sample_id=sample.sample_id,
            target_drug=sample.target_drug,
            feature_vector=feature_vector,
            model_artifact=training_result.model_artifact,
            calibration_policy=status_mismatch_policy,
        )


def test_training_workflow_rejects_feature_matrix_scope_mismatch(tmp_path: Path) -> None:
    label_table = load_training_label_table(REPO_ROOT / DEFAULT_TRAINING_LABEL_FIXTURE_PATH)
    feature_matrix = load_feature_matrix_artifact(REPO_ROOT / DEFAULT_FEATURE_MATRIX_FIXTURE_PATH)
    mismatched_matrix = feature_matrix.model_copy(update={"split_id": "other_split_v1"})

    with pytest.raises(ValueError, match="Feature matrix split_id must match"):
        run_baseline_training_workflow(
            label_table=label_table,
            feature_matrix=mismatched_matrix,
            output_dir=tmp_path,
        )


def test_training_workflow_rejects_duplicate_feature_rows(tmp_path: Path) -> None:
    label_table = load_training_label_table(REPO_ROOT / DEFAULT_TRAINING_LABEL_FIXTURE_PATH)
    feature_matrix = load_feature_matrix_artifact(REPO_ROOT / DEFAULT_FEATURE_MATRIX_FIXTURE_PATH)
    duplicated_matrix = feature_matrix.model_copy(
        update={"rows": [*feature_matrix.rows, feature_matrix.rows[0]]}
    )

    with pytest.raises(ValueError, match="unique sample_id"):
        run_baseline_training_workflow(
            label_table=label_table,
            feature_matrix=duplicated_matrix,
            output_dir=tmp_path,
        )


def test_training_workflow_rejects_included_non_binary_labels(tmp_path: Path) -> None:
    label_table = load_training_label_table(REPO_ROOT / DEFAULT_TRAINING_LABEL_FIXTURE_PATH)
    feature_matrix = load_feature_matrix_artifact(REPO_ROOT / DEFAULT_FEATURE_MATRIX_FIXTURE_PATH)
    intermediate_row = next(
        row
        for row in label_table.rows
        if row.phenotype_label == PredictedPhenotype.INTERMEDIATE
    )
    included_intermediate_row = intermediate_row.model_copy(update={"included_in_training": True})
    invalid_table = label_table.model_copy(
        update={
            "rows": [
                included_intermediate_row if row.sample_id == intermediate_row.sample_id else row
                for row in label_table.rows
            ]
        }
    )

    with pytest.raises(ValueError, match="resistant or susceptible"):
        run_baseline_training_workflow(
            label_table=invalid_table,
            feature_matrix=feature_matrix,
            output_dir=tmp_path,
        )


def test_training_workflow_rejects_duplicate_included_labels(tmp_path: Path) -> None:
    label_table = load_training_label_table(REPO_ROOT / DEFAULT_TRAINING_LABEL_FIXTURE_PATH)
    feature_matrix = load_feature_matrix_artifact(REPO_ROOT / DEFAULT_FEATURE_MATRIX_FIXTURE_PATH)
    duplicated_table = label_table.model_copy(
        update={"rows": [*label_table.rows, label_table.rows[0]]}
    )

    with pytest.raises(ValueError, match="unique included sample_id"):
        run_baseline_training_workflow(
            label_table=duplicated_table,
            feature_matrix=feature_matrix,
            output_dir=tmp_path,
        )


def test_mechanism_prediction_concordance_detects_supported_prediction(tmp_path: Path) -> None:
    _, _, evidence_rows, _, prediction = build_smoke_prediction_context(tmp_path)

    mechanism_feature = classify_mechanism_prediction_concordance(
        prediction=prediction,
        evidence_rows=evidence_rows,
    )

    assert mechanism_feature.classification == PredictionMechanismClass.CONCORDANT_RESISTANT
    assert mechanism_feature.mechanism_concordance is True
    assert mechanism_feature.matched_gene_symbols


def test_mechanism_prediction_concordance_covers_review_classes(tmp_path: Path) -> None:
    _, _, evidence_rows, _, prediction = build_smoke_prediction_context(tmp_path)
    susceptible_prediction = prediction.model_copy(
        update={
            "predicted_phenotype": PredictedPhenotype.SUSCEPTIBLE,
            "probability": 0.2,
        }
    )

    susceptible_signal = classify_mechanism_prediction_concordance(
        prediction=susceptible_prediction,
        evidence_rows=evidence_rows,
    )
    unsupported_resistant = classify_mechanism_prediction_concordance(
        prediction=prediction,
        evidence_rows=[],
    )

    assert susceptible_signal.classification == PredictionMechanismClass.SUSCEPTIBLE_WITH_MECHANISM
    assert susceptible_signal.mechanism_concordance is False
    assert unsupported_resistant.classification == PredictionMechanismClass.UNSUPPORTED_RESISTANT
    assert unsupported_resistant.mechanism_concordance is False


def test_actionability_features_and_triage_are_schema_valid(tmp_path: Path) -> None:
    _, qc, evidence_rows, novelty, prediction = build_smoke_prediction_context(tmp_path)

    features = build_actionability_features(
        qc=qc,
        prediction=prediction,
        novelty=novelty,
        evidence_rows=evidence_rows,
    )
    decision = apply_triage_policy(features=features)

    assert features.threshold_version == DEFAULT_ACTIONABILITY_THRESHOLD_VERSION
    assert features.mechanism_concordance is True
    assert features.actionability_score > 0.7
    assert decision.triage == TriageOutcome.ACT
    assert RationaleCode.ACTIONABILITY_THRESHOLD_MET in decision.rationale_codes
    assert RationaleCode.CONCORDANT_SIGNAL_PRESENT in decision.rationale_codes


def test_triage_policy_covers_review_and_defer_examples() -> None:
    review_features = ActionabilityFeatures(
        job_id=JOB_ID,
        sample_id="sample_001",
        target_drug="tetracycline",
        actionability_score=0.55,
        mechanism_concordance=None,
        prediction_entropy=0.5,
        qc_risk=0.0,
        novelty_risk=0.4,
        metadata_completeness=1.0,
        threshold_version=DEFAULT_ACTIONABILITY_THRESHOLD_VERSION,
    )
    defer_features = ActionabilityFeatures(
        job_id=JOB_ID,
        sample_id="sample_001",
        target_drug="tetracycline",
        actionability_score=0.85,
        mechanism_concordance=True,
        prediction_entropy=0.2,
        qc_risk=0.0,
        novelty_risk=0.9,
        metadata_completeness=1.0,
        threshold_version=DEFAULT_ACTIONABILITY_THRESHOLD_VERSION,
    )

    review_decision = apply_triage_policy(features=review_features)
    defer_decision = apply_triage_policy(features=defer_features)

    assert review_decision.triage == TriageOutcome.REVIEW
    assert RationaleCode.MANUAL_CONFIRMATION_REQUIRED in review_decision.rationale_codes
    assert defer_decision.triage == TriageOutcome.DEFER_TO_LAB
    assert RationaleCode.HIGH_LINEAGE_NOVELTY in defer_decision.rationale_codes


def test_triage_policy_reports_the_policy_version_used() -> None:
    features = ActionabilityFeatures(
        job_id=JOB_ID,
        sample_id="sample_001",
        target_drug="tetracycline",
        actionability_score=0.75,
        mechanism_concordance=True,
        prediction_entropy=0.2,
        qc_risk=0.0,
        novelty_risk=0.2,
        metadata_completeness=1.0,
        threshold_version=DEFAULT_ACTIONABILITY_THRESHOLD_VERSION,
    )
    custom_policy = ActionabilityPolicy(
        threshold_version="custom_actionability_policy_v2",
        act_threshold=0.70,
    )

    decision = apply_triage_policy(features=features, policy=custom_policy)

    assert decision.job_id == JOB_ID
    assert decision.threshold_version == "custom_actionability_policy_v2"


def test_decision_builder_returns_complete_decision_object(tmp_path: Path) -> None:
    sample, qc, evidence_rows, novelty, prediction = build_smoke_prediction_context(tmp_path)

    decision_object = build_decision_object(
        sample=sample,
        qc=qc,
        evidence_rows=evidence_rows,
        prediction=prediction,
        novelty=novelty,
        job_id=JOB_ID,
        artifact_manifest_id="manifest_phase_5_smoke",
    )

    assert decision_object.sample.sample_id == sample.sample_id
    assert decision_object.actionability_features.target_drug == sample.target_drug
    assert decision_object.triage_decision.job_id == JOB_ID
    assert decision_object.triage_decision.triage == TriageOutcome.ACT
    assert decision_object.rationale_codes == decision_object.triage_decision.rationale_codes
    assert "analysis_input_source_context_agricultural_surveillance_proxy" in decision_object.provenance_notes
    assert "analysis_input_provenance_source_fixture" in decision_object.provenance_notes
    assert "analysis_input_fixture_fasta_path" in decision_object.provenance_notes
    assert "prediction_model_training_split_context_fixture" in decision_object.provenance_notes
    assert "prediction_model_fixture_backed_baseline" in decision_object.provenance_notes


def test_actionability_builder_rejects_mismatched_context(tmp_path: Path) -> None:
    _, qc, evidence_rows, novelty, prediction = build_smoke_prediction_context(tmp_path)
    mismatched_qc = qc.model_copy(update={"sample_id": "other_sample_001"})

    with pytest.raises(ValueError, match="Actionability inputs must match prediction sample_id"):
        build_actionability_features(
            qc=mismatched_qc,
            prediction=prediction,
            novelty=novelty,
            evidence_rows=evidence_rows,
        )


def test_actionability_builder_rejects_mismatched_target_context(tmp_path: Path) -> None:
    _, qc, evidence_rows, novelty, prediction = build_smoke_prediction_context(tmp_path)
    mismatched_novelty = novelty.model_copy(update={"target_drug": "ampicillin"})

    with pytest.raises(ValueError, match="Actionability inputs must match prediction target_drug"):
        build_actionability_features(
            qc=qc,
            prediction=prediction,
            novelty=mismatched_novelty,
            evidence_rows=evidence_rows,
        )


def test_actionability_builder_rejects_stale_qc_from_prior_job(tmp_path: Path) -> None:
    _, qc, evidence_rows, novelty, prediction = build_smoke_prediction_context(tmp_path)
    stale_qc = qc.model_copy(update={"job_id": "job_phase_5_prior"})

    with pytest.raises(ValueError, match="Actionability inputs must match prediction job_id"):
        build_actionability_features(
            qc=stale_qc,
            prediction=prediction,
            novelty=novelty,
            evidence_rows=evidence_rows,
        )


def test_phase5_acceptance_matrix_requires_complete_decision_output() -> None:
    matrix = json.loads((REPO_ROOT / "docs/prediction/PHASE_5_ACCEPTANCE_MATRIX.json").read_text(encoding="utf-8"))

    assert any("DecisionObject" in item for item in matrix["pass_criteria"])
    assert any("performance" in item.lower() for item in matrix["warn_conditions"])
    assert "Phase 6" in matrix["next_phase_gate"]
