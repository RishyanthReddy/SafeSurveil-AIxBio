from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.contracts import AssemblyQC, SampleInput  # noqa: E402
from app.evidence import run_evidence_smoke  # noqa: E402
from app.prediction import (  # noqa: E402
    DEFAULT_FEATURE_MATRIX_FIXTURE_PATH,
    DEFAULT_TRAINING_LABEL_FIXTURE_PATH,
    build_baseline_feature_strategy,
    build_decision_object,
    extract_baseline_feature_vector,
    load_feature_matrix_artifact,
    load_training_label_table,
    run_baseline_training_workflow,
    run_prediction_inference,
)


def load_smoke_sample() -> SampleInput:
    payload = json.loads((REPO_ROOT / "data/fixtures/smoke/sample_001.metadata.json").read_text(encoding="utf-8"))
    return SampleInput(
        sample_id=payload["sample_id"],
        organism_hint=payload["organism_hint"],
        target_drug=payload["target_drug"],
        fasta_path=payload["fasta_path"],
        metadata=payload["metadata"],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Phase 5 prediction smoke workflow.")
    parser.add_argument(
        "--output-dir",
        default="artifacts/demo/prediction_smoke",
        help="Directory where smoke outputs should be written.",
    )
    args = parser.parse_args()

    sample = load_smoke_sample()
    job_id = "job_phase_5_prediction_smoke"
    output_dir = REPO_ROOT / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    evidence_result = run_evidence_smoke(
        sample,
        output_dir=output_dir / "evidence",
        fixture_mode=True,
        repo_root=REPO_ROOT,
        job_id=job_id,
    )
    feature_strategy = build_baseline_feature_strategy(repo_root=REPO_ROOT)
    qc = AssemblyQC.model_validate_json(evidence_result.qc_path.read_text(encoding="utf-8"))
    feature_vector = extract_baseline_feature_vector(
        sample,
        qc=qc,
        evidence_rows=list(evidence_result.mechanistic_evidence),
        novelty=evidence_result.novelty_assessment,
        feature_set_version=feature_strategy.feature_set_version,
    )
    label_table = load_training_label_table(REPO_ROOT / DEFAULT_TRAINING_LABEL_FIXTURE_PATH)
    feature_matrix = load_feature_matrix_artifact(REPO_ROOT / DEFAULT_FEATURE_MATRIX_FIXTURE_PATH)
    training_result = run_baseline_training_workflow(
        label_table=label_table,
        feature_matrix=feature_matrix,
        output_dir=output_dir / "model",
    )
    prediction = run_prediction_inference(
        job_id=job_id,
        sample_id=sample.sample_id,
        target_drug=sample.target_drug,
        feature_vector=feature_vector,
        model_artifact=training_result.model_artifact,
        calibration_policy=training_result.calibration_policy,
    )
    prediction_path = output_dir / "prediction.json"
    prediction_path.write_text(
        json.dumps(prediction.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )
    decision_object = build_decision_object(
        sample=sample,
        qc=qc,
        evidence_rows=list(evidence_result.mechanistic_evidence),
        prediction=prediction,
        novelty=evidence_result.novelty_assessment,
        job_id=job_id,
    )
    decision_path = output_dir / "decision.json"
    decision_path.write_text(
        json.dumps(decision_object.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "sample_id": sample.sample_id,
                "feature_set_version": feature_strategy.feature_set_version,
                "model_version": training_result.model_artifact.model_version,
                "prediction_path": str(prediction_path),
                "decision_path": str(decision_path),
                "predicted_phenotype": prediction.predicted_phenotype.value,
                "probability": prediction.probability,
                "calibration_status": prediction.calibration_status.value,
                "uncertainty_score": prediction.uncertainty_score,
                "actionability_score": decision_object.actionability_features.actionability_score,
                "triage": decision_object.triage_decision.triage.value,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
