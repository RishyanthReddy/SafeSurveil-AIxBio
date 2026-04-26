from __future__ import annotations

import math

from app.contracts import CalibrationPolicyArtifact, CalibrationStatus

DEFAULT_CALIBRATION_POLICY_VERSION = "entropy_fallback_v1"
MINIMUM_CALIBRATION_SAMPLE_COUNT = 25


def build_calibration_policy(
    *,
    model_version: str,
    feature_set_version: str,
    observed_training_sample_count: int,
    minimum_samples_required: int = MINIMUM_CALIBRATION_SAMPLE_COUNT,
) -> CalibrationPolicyArtifact:
    if observed_training_sample_count < minimum_samples_required:
        return CalibrationPolicyArtifact(
            policy_version=DEFAULT_CALIBRATION_POLICY_VERSION,
            model_version=model_version,
            feature_set_version=feature_set_version,
            calibration_status=CalibrationStatus.NOT_AVAILABLE,
            method="entropy_only_fixture_fallback",
            minimum_samples_required=minimum_samples_required,
            observed_training_sample_count=observed_training_sample_count,
            uncertainty_measure="binary_entropy",
            notes=[
                "Calibration remains unavailable until the training set reaches the minimum sample count.",
                "Phase 5 uses entropy as the default uncertainty signal for the first smoke baseline.",
            ],
        )

    return CalibrationPolicyArtifact(
        policy_version=DEFAULT_CALIBRATION_POLICY_VERSION,
        model_version=model_version,
        feature_set_version=feature_set_version,
        calibration_status=CalibrationStatus.UNCALIBRATED,
        method="entropy_until_calibrator_is_fit",
        minimum_samples_required=minimum_samples_required,
        observed_training_sample_count=observed_training_sample_count,
        uncertainty_measure="binary_entropy",
        notes=[
            "Enough samples exist to attempt calibration later, but no calibrator has been fit in the MVP yet.",
        ],
    )


def compute_prediction_entropy(probability: float) -> float:
    bounded = min(max(probability, 1e-9), 1.0 - 1e-9)
    entropy = -(
        bounded * math.log2(bounded)
        + (1.0 - bounded) * math.log2(1.0 - bounded)
    )
    return round(entropy, 6)
