from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Sequence

from app.contracts import (
    AnalyzeJobResponse,
    ArtifactKind,
    ArtifactRecord,
    AssemblyQC,
    DecisionObject,
    JobState,
    JobStatus,
    QueueItem,
    SampleInput,
    SeverityLevel,
    TriageOutcome,
)
from app.contracts.common import normalize_slug_like
from app.evidence import run_evidence_smoke
from app.paths import display_path, path_is_within, resolve_local_path
from app.prediction import (
    DEFAULT_FEATURE_MATRIX_FIXTURE_PATH,
    DEFAULT_TRAINING_LABEL_FIXTURE_PATH,
    build_decision_object,
    extract_baseline_feature_vector,
    load_feature_matrix_artifact,
    load_training_label_table,
    run_baseline_training_workflow,
    run_prediction_inference,
)
from app.settings import AppSettings
from app.storage import SQLitePersistence

SUPPORTED_ANALYSIS_ORGANISM = "e_coli"
SUPPORTED_ANALYSIS_TARGET_DRUG = "tetracycline"


@dataclass(frozen=True)
class AnalysisServiceResult:
    response: AnalyzeJobResponse
    job_status: JobStatus
    decision: DecisionObject | None


class AnalysisService:
    def __init__(self, *, settings: AppSettings, persistence: SQLitePersistence) -> None:
        self.settings = settings
        self.persistence = persistence

    def analyze(self, sample: SampleInput) -> AnalysisServiceResult:
        self._validate_request(sample)
        job_id = self._generate_job_id()
        self.persistence.upsert_sample(sample)

        queued_status = self._build_status(
            job_id=job_id,
            sample=sample,
            status=JobState.QUEUED,
            current_step="queued_for_local_analysis",
        )
        self.persistence.create_job(queued_status, sample=sample)

        artifact_dir = self.settings.artifact_root / "runs" / "jobs" / job_id
        try:
            running_status = self._build_status(
                job_id=job_id,
                sample=sample,
                status=JobState.RUNNING,
                current_step="running_evidence_pipeline",
                submitted_at=queued_status.submitted_at,
                created_at=queued_status.created_at,
            )
            self.persistence.update_job_status(running_status)

            evidence_result = run_evidence_smoke(
                sample,
                output_dir=artifact_dir / "evidence",
                fixture_mode=self.settings.use_fixtures,
                repo_root=self.settings.repo_root,
                job_id=job_id,
                settings=self.settings,
            )
            qc = AssemblyQC.model_validate_json(evidence_result.qc_path.read_text(encoding="utf-8"))
            self.persistence.save_assembly_qc(job_id=job_id, target_drug=sample.target_drug, qc=qc)
            self.persistence.replace_mechanistic_evidence(list(evidence_result.mechanistic_evidence))
            self.persistence.save_novelty(evidence_result.novelty_assessment)
            self.persistence.save_artifacts(list(evidence_result.artifact_manifest.artifacts))

            evidence_ready_status = self._build_status(
                job_id=job_id,
                sample=sample,
                status=JobState.EVIDENCE_READY,
                current_step="evidence_persisted",
                warnings=self._degraded_warnings(evidence_result.failures),
                submitted_at=queued_status.submitted_at,
                created_at=queued_status.created_at,
            )
            self.persistence.update_job_status(evidence_ready_status)

            training_result = run_baseline_training_workflow(
                label_table=load_training_label_table(self.settings.repo_root / DEFAULT_TRAINING_LABEL_FIXTURE_PATH),
                feature_matrix=load_feature_matrix_artifact(self.settings.repo_root / DEFAULT_FEATURE_MATRIX_FIXTURE_PATH),
                output_dir=artifact_dir / "model",
            )
            feature_vector = extract_baseline_feature_vector(
                sample,
                qc=qc,
                evidence_rows=list(evidence_result.mechanistic_evidence),
                novelty=evidence_result.novelty_assessment,
            )
            prediction = run_prediction_inference(
                job_id=job_id,
                sample_id=sample.sample_id,
                target_drug=sample.target_drug,
                feature_vector=feature_vector,
                model_artifact=training_result.model_artifact,
                calibration_policy=training_result.calibration_policy,
                input_source_context=sample.metadata.source_context,
                input_provenance_source=sample.metadata.provenance_source,
            )
            decision = build_decision_object(
                sample=sample,
                qc=qc,
                evidence_rows=list(evidence_result.mechanistic_evidence),
                prediction=prediction,
                novelty=evidence_result.novelty_assessment,
                job_id=job_id,
            )
            prediction_path = artifact_dir / "prediction.json"
            decision_path = artifact_dir / "decision.json"
            self._write_json(prediction_path, prediction.model_dump(mode="json"))
            self._write_json(decision_path, decision.model_dump(mode="json"))

            self.persistence.save_prediction(prediction)
            self.persistence.save_actionability(
                features=decision.actionability_features,
                triage=decision.triage_decision,
                decision_warnings=decision.warnings,
            )
            self.persistence.save_artifacts(
                [
                    *self._output_artifacts(
                        job_id=job_id,
                        sample=sample,
                        prediction_path=prediction_path,
                        decision_path=decision_path,
                    ),
                ]
            )

            decision_ready_status = self._build_status(
                job_id=job_id,
                sample=sample,
                status=JobState.DECISION_READY,
                current_step="decision_object_persisted",
                warnings=self._degraded_warnings(evidence_result.failures),
                submitted_at=queued_status.submitted_at,
                created_at=queued_status.created_at,
            )
            self.persistence.update_job_status(decision_ready_status)

            final_state = JobState.DEGRADED if self.settings.use_fixtures or evidence_result.failures else JobState.COMPLETED
            final_warnings = self._degraded_warnings(evidence_result.failures)
            if self.settings.use_fixtures:
                final_warnings = sorted(set([*final_warnings, "fixture_mode_active"]))
            self.persistence.save_queue_item(self._build_queue_item(decision, final_state))
            completed_status = self._build_status(
                job_id=job_id,
                sample=sample,
                status=final_state,
                current_step="analysis_complete",
                warnings=final_warnings,
                submitted_at=queued_status.submitted_at,
                created_at=queued_status.created_at,
                completed_at=datetime.now(UTC),
            )
            self.persistence.update_job_status(completed_status)
            return AnalysisServiceResult(
                response=AnalyzeJobResponse(job_id=job_id, status=completed_status.status),
                job_status=completed_status,
                decision=decision,
            )
        except Exception as exc:
            failed_status = self._build_status(
                job_id=job_id,
                sample=sample,
                status=JobState.FAILED,
                current_step="analysis_failed",
                failure_code=self._failure_code_for_exception(exc),
                warnings=[str(exc)],
                submitted_at=queued_status.submitted_at,
                created_at=queued_status.created_at,
                completed_at=datetime.now(UTC),
            )
            self.persistence.update_job_status(failed_status)
            return AnalysisServiceResult(
                response=AnalyzeJobResponse(job_id=job_id, status=failed_status.status),
                job_status=failed_status,
                decision=None,
            )

    def _validate_request(self, sample: SampleInput) -> None:
        if sample.fasta_uri is not None:
            raise ValueError("Analyze endpoint currently supports only local fasta_path inputs.")
        if sample.fasta_path is None:
            raise ValueError("Analyze endpoint requires a local fasta_path.")
        organism_hint = sample.organism_hint.value if sample.organism_hint is not None else None
        if (
            organism_hint != SUPPORTED_ANALYSIS_ORGANISM
            or sample.target_drug != SUPPORTED_ANALYSIS_TARGET_DRUG
        ):
            raise ValueError(
                "Analyze endpoint currently supports only e_coli/tetracycline because "
                "the bundled baseline model is scoped to that organism/drug."
            )
        try:
            resolved_path = resolve_local_path(sample.fasta_path, repo_root=self.settings.repo_root)
        except ValueError as exc:
            raise ValueError(
                "Analyze endpoint requires a repo-relative fasta_path without parent traversal."
            ) from exc
        allowed_roots = (
            self.settings.repo_root,
            self.settings.data_root,
            self.settings.integrations.dataset_root,
        )
        if not any(path_is_within(resolved_path, root=root) for root in allowed_roots):
            raise ValueError(
                "Analyze endpoint requires a local fasta_path under the repo or configured DATASET_ROOT."
            )

    def _generate_job_id(self) -> str:
        timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
        return normalize_slug_like(f"job_{timestamp}")

    def _build_status(
        self,
        *,
        job_id: str,
        sample: SampleInput,
        status: JobState,
        current_step: str,
        warnings: Sequence[str] = (),
        failure_code: str | None = None,
        submitted_at: datetime | None = None,
        created_at: datetime | None = None,
        completed_at: datetime | None = None,
    ) -> JobStatus:
        return JobStatus(
            job_id=job_id,
            sample_id=sample.sample_id,
            target_drug=sample.target_drug,
            status=status,
            current_step=current_step,
            failure_code=failure_code,
            warnings=list(warnings),
            submitted_at=submitted_at or datetime.now(UTC),
            updated_at=datetime.now(UTC),
            completed_at=completed_at,
            created_at=created_at or datetime.now(UTC),
        )

    def _degraded_warnings(self, failures: Sequence[object]) -> list[str]:
        warnings: list[str] = []
        for failure in failures:
            detail = getattr(failure, "detail", None)
            if detail:
                warnings.append(str(detail))
        return sorted(set(warnings))

    def _failure_code_for_exception(self, exc: Exception) -> str:
        try:
            return normalize_slug_like(f"analysis_{type(exc).__name__}")
        except ValueError:
            return "analysis_failed"

    def _build_queue_item(self, decision: DecisionObject, status: JobState) -> QueueItem:
        severity_priority = {
            SeverityLevel.CRITICAL: 0,
            SeverityLevel.HIGH: 10,
            SeverityLevel.MEDIUM: 20,
            SeverityLevel.LOW: 30,
        }
        triage_label = decision.triage_decision.triage.value.replace("_", " ")
        return QueueItem(
            job_id=decision.job_id or decision.phenotype_prediction.job_id,
            sample_id=decision.sample.sample_id,
            target_drug=decision.sample.target_drug,
            triage=decision.triage_decision.triage,
            severity=decision.triage_decision.severity,
            status=status,
            queue_priority=severity_priority[decision.triage_decision.severity],
            headline=f"{triage_label} for {decision.sample.sample_id} on {decision.sample.target_drug}",
            rationale_codes=decision.rationale_codes,
        )

    def _output_artifacts(
        self,
        *,
        job_id: str,
        sample: SampleInput,
        prediction_path: Path,
        decision_path: Path,
    ) -> list[ArtifactRecord]:
        records: list[ArtifactRecord] = []
        for artifact_id, kind, path, media_type, generated_by in (
            (f"{job_id}_prediction_json", ArtifactKind.PREDICTION_SUMMARY, prediction_path, "application/json", "prediction_inference"),
            (f"{job_id}_decision_json", ArtifactKind.DECISION_OBJECT, decision_path, "application/json", "decision_builder"),
        ):
            records.append(
                ArtifactRecord(
                    artifact_id=artifact_id,
                    job_id=job_id,
                    sample_id=sample.sample_id,
                    target_drug=sample.target_drug,
                    kind=kind,
                    path=self._display_path(path),
                    media_type=media_type,
                    generated_by=generated_by,
                    size_bytes=path.stat().st_size if path.exists() else None,
                )
            )
        return records

    def _display_path(self, path: Path) -> str:
        return display_path(path, repo_root=self.settings.repo_root)

    def _write_json(self, path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
