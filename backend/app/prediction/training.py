from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path

from app.contracts import (
    BaselineModelArtifact,
    CalibrationPolicyArtifact,
    FeatureMatrixArtifact,
    PredictedPhenotype,
    TrainingLabelRow,
    TrainingLabelTable,
)

from .uncertainty import build_calibration_policy

DEFAULT_TRAINING_LABEL_FIXTURE_PATH = "data/fixtures/prediction/fixture_training_labels.csv"
DEFAULT_BASELINE_MODEL_VERSION = "baseline_tetracycline_smoke_v1"
_WEIGHT_SCALE = 1.5
_WEIGHT_CLIP = 1.5


@dataclass(frozen=True)
class BaselineTrainingWorkflowResult:
    model_artifact: BaselineModelArtifact
    calibration_policy: CalibrationPolicyArtifact
    model_artifact_path: Path
    calibration_policy_path: Path


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise ValueError(f"Unsupported boolean value in training labels: {value}")


def load_training_label_table(path: Path) -> TrainingLabelTable:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = [
            TrainingLabelRow(
                sample_id=row["sample_id"],
                organism=row["organism"],
                target_drug=row["target_drug"],
                phenotype_label=row["phenotype_label"],
                split_context=row["split_context"],
                split_id=row["split_id"],
                snapshot_id=row["snapshot_id"],
                source_record_id=row["source_record_id"],
                label_source=row["label_source"],
                included_in_training=_parse_bool(row["included_in_training"]),
                exclusion_reason=row["exclusion_reason"] or None,
            )
            for row in reader
        ]

    if not rows:
        raise ValueError("Training label fixture must contain at least one row.")

    first_row = rows[0]
    return TrainingLabelTable(
        organism=first_row.organism,
        target_drug=first_row.target_drug,
        split_context=first_row.split_context,
        split_id=first_row.split_id,
        snapshot_id=first_row.snapshot_id,
        rows=rows,
    )


def _safe_logit(rate: float) -> float:
    bounded = min(max(rate, 1e-6), 1.0 - 1e-6)
    return math.log(bounded / (1.0 - bounded))


def _duplicate_sample_ids(feature_matrix: FeatureMatrixArtifact) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for row in feature_matrix.rows:
        if row.sample_id in seen:
            duplicates.add(row.sample_id)
        seen.add(row.sample_id)
    return sorted(duplicates)


def _duplicate_label_sample_ids(rows: list[TrainingLabelRow]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for row in rows:
        if row.sample_id in seen:
            duplicates.add(row.sample_id)
        seen.add(row.sample_id)
    return sorted(duplicates)


def run_baseline_training_workflow(
    *,
    label_table: TrainingLabelTable,
    feature_matrix: FeatureMatrixArtifact,
    output_dir: Path,
    model_version: str = DEFAULT_BASELINE_MODEL_VERSION,
) -> BaselineTrainingWorkflowResult:
    if feature_matrix.organism != label_table.organism:
        raise ValueError("Feature matrix organism must match the training label table organism.")
    if feature_matrix.target_drug != label_table.target_drug:
        raise ValueError("Feature matrix target_drug must match the training label table target_drug.")
    if feature_matrix.split_context != label_table.split_context:
        raise ValueError("Feature matrix split_context must match the training label table split_context.")
    if feature_matrix.split_id != label_table.split_id:
        raise ValueError("Feature matrix split_id must match the training label table split_id.")
    if feature_matrix.snapshot_id != label_table.snapshot_id:
        raise ValueError("Feature matrix snapshot_id must match the training label table snapshot_id.")

    duplicate_feature_ids = _duplicate_sample_ids(feature_matrix)
    if duplicate_feature_ids:
        joined_ids = ", ".join(duplicate_feature_ids)
        raise ValueError(f"Feature matrix rows must have unique sample_id values: {joined_ids}")

    feature_rows = {row.sample_id: row for row in feature_matrix.rows}
    included_rows = [row for row in label_table.rows if row.included_in_training]
    if not included_rows:
        raise ValueError("At least one included resistant/susceptible row is required for training.")

    non_binary_rows = [
        row
        for row in included_rows
        if row.phenotype_label not in {PredictedPhenotype.RESISTANT, PredictedPhenotype.SUSCEPTIBLE}
    ]
    if non_binary_rows:
        joined_ids = ", ".join(sorted(row.sample_id for row in non_binary_rows))
        raise ValueError(f"Included training labels must be resistant or susceptible: {joined_ids}")

    duplicate_label_ids = _duplicate_label_sample_ids(included_rows)
    if duplicate_label_ids:
        joined_ids = ", ".join(duplicate_label_ids)
        raise ValueError(f"Training labels must have unique included sample_id values: {joined_ids}")

    resistant_rows = [row for row in included_rows if row.phenotype_label == PredictedPhenotype.RESISTANT]
    susceptible_rows = [row for row in included_rows if row.phenotype_label == PredictedPhenotype.SUSCEPTIBLE]
    if not resistant_rows or not susceptible_rows:
        raise ValueError("Baseline training requires at least one resistant row and one susceptible row.")

    missing_features = [row.sample_id for row in included_rows if row.sample_id not in feature_rows]
    if missing_features:
        joined_ids = ", ".join(sorted(missing_features))
        raise ValueError(f"Training features are missing for rows: {joined_ids}")

    base_rate = len(resistant_rows) / len(included_rows)
    bias = round(_safe_logit(base_rate), 6)
    feature_centers: dict[str, float] = {}
    feature_weights: dict[str, float] = {}

    for feature_name in feature_matrix.feature_names:
        resistant_mean = sum(feature_rows[row.sample_id].values[feature_name] for row in resistant_rows) / len(resistant_rows)
        susceptible_mean = sum(feature_rows[row.sample_id].values[feature_name] for row in susceptible_rows) / len(susceptible_rows)
        overall_mean = sum(feature_rows[row.sample_id].values[feature_name] for row in included_rows) / len(included_rows)
        contrast = resistant_mean - susceptible_mean
        weight = max(-_WEIGHT_CLIP, min(_WEIGHT_CLIP, contrast * _WEIGHT_SCALE))

        feature_centers[feature_name] = round(overall_mean, 6)
        feature_weights[feature_name] = round(weight, 6)

    calibration_policy = build_calibration_policy(
        model_version=model_version,
        feature_set_version=feature_matrix.feature_set_version,
        observed_training_sample_count=len(included_rows),
    )
    model_artifact = BaselineModelArtifact(
        model_version=model_version,
        feature_set_version=feature_matrix.feature_set_version,
        organism=label_table.organism,
        target_drug=label_table.target_drug,
        split_context=label_table.split_context,
        split_id=label_table.split_id,
        snapshot_id=label_table.snapshot_id,
        algorithm="centered_feature_contrast_logit",
        bias=bias,
        feature_weights=feature_weights,
        feature_centers=feature_centers,
        training_sample_count=len(included_rows),
        resistant_sample_count=len(resistant_rows),
        susceptible_sample_count=len(susceptible_rows),
        calibration_status=calibration_policy.calibration_status,
        warnings=[
            "This baseline is fixture-backed and intentionally simple for the first smoke model.",
        ],
    )

    resolved_output_dir = Path(output_dir)
    model_artifact_path = resolved_output_dir / "baseline_model.json"
    calibration_policy_path = resolved_output_dir / "calibration_policy.json"
    _write_json(model_artifact_path, model_artifact.model_dump(mode="json"))
    _write_json(calibration_policy_path, calibration_policy.model_dump(mode="json"))

    return BaselineTrainingWorkflowResult(
        model_artifact=model_artifact,
        calibration_policy=calibration_policy,
        model_artifact_path=model_artifact_path,
        calibration_policy_path=calibration_policy_path,
    )
