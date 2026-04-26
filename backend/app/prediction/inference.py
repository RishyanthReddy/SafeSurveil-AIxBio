from __future__ import annotations

import math

from app.contracts import (
    BaselineModelArtifact,
    CalibrationPolicyArtifact,
    CalibrationStatus,
    FeatureVectorRecord,
    PhenotypePrediction,
    PredictedPhenotype,
    ProvenanceSource,
    SourceContext,
)
from app.contracts.common import normalize_slug_like

from .uncertainty import compute_prediction_entropy


def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-value))


def run_prediction_inference(
    *,
    job_id: str,
    sample_id: str,
    target_drug: str,
    feature_vector: FeatureVectorRecord,
    model_artifact: BaselineModelArtifact,
    calibration_policy: CalibrationPolicyArtifact,
    input_source_context: SourceContext = SourceContext.OTHER,
    input_provenance_source: ProvenanceSource = ProvenanceSource.OTHER,
) -> PhenotypePrediction:
    normalized_job_id = normalize_slug_like(job_id)
    normalized_sample_id = normalize_slug_like(sample_id)
    normalized_target_drug = normalize_slug_like(target_drug)

    if normalized_sample_id != feature_vector.sample_id:
        raise ValueError("Inference sample_id must match the feature vector sample_id.")
    if feature_vector.job_id != normalized_job_id:
        raise ValueError("Inference job_id must match the feature vector job_id.")
    if feature_vector.target_drug != normalized_target_drug:
        raise ValueError("Inference target_drug must match the feature vector target_drug.")
    if normalized_target_drug != model_artifact.target_drug:
        raise ValueError("Inference target_drug must match the model artifact target_drug.")
    if feature_vector.feature_set_version != model_artifact.feature_set_version:
        raise ValueError("Feature set version mismatch between feature vector and model artifact.")
    if model_artifact.model_version != calibration_policy.model_version:
        raise ValueError("Calibration policy must match the model artifact version.")
    if model_artifact.feature_set_version != calibration_policy.feature_set_version:
        raise ValueError("Calibration policy must match the model artifact feature_set_version.")
    if calibration_policy.observed_training_sample_count != model_artifact.training_sample_count:
        raise ValueError("Calibration policy training sample count must match the model artifact training_sample_count.")
    if calibration_policy.calibration_status != model_artifact.calibration_status:
        raise ValueError("Calibration policy calibration_status must match the model artifact calibration_status.")

    missing_features = sorted(set(model_artifact.feature_weights) - set(feature_vector.values))
    if missing_features:
        joined_features = ", ".join(missing_features)
        raise ValueError(f"Feature vector is missing trained model features: {joined_features}")

    raw_score = model_artifact.bias
    for feature_name, weight in model_artifact.feature_weights.items():
        centered_value = feature_vector.values[feature_name] - model_artifact.feature_centers[feature_name]
        raw_score += weight * centered_value

    probability = round(_sigmoid(raw_score), 6)
    predicted_phenotype = (
        PredictedPhenotype.RESISTANT
        if probability >= model_artifact.decision_threshold
        else PredictedPhenotype.SUSCEPTIBLE
    )
    warnings: list[str] = []
    if calibration_policy.calibration_status != CalibrationStatus.CALIBRATED:
        warnings.append(
            "Prediction uses entropy-based uncertainty because calibration is not yet available for this baseline."
        )
    warnings.extend(model_artifact.warnings)

    return PhenotypePrediction(
        job_id=normalized_job_id,
        sample_id=normalized_sample_id,
        target_drug=normalized_target_drug,
        predicted_phenotype=predicted_phenotype,
        probability=probability,
        calibration_status=calibration_policy.calibration_status,
        uncertainty_score=compute_prediction_entropy(probability),
        feature_set_version=model_artifact.feature_set_version,
        model_version=model_artifact.model_version,
        model_training_split_context=model_artifact.split_context,
        input_source_context=input_source_context,
        input_provenance_source=input_provenance_source,
        warnings=list(dict.fromkeys(warnings)),
    )
