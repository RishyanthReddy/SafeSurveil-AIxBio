from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from typing import Callable

from pydantic import ValidationError

from app.contracts import (
    ArtifactKind,
    ArtifactManifest,
    ArtifactRecord,
    CalibrationStatus,
    CopilotAnswerBlock,
    CopilotOutputMode,
    CopilotOutputOrigin,
    CopilotResponse,
    DecisionCardBlock,
    DecisionObject,
    JobCopilotResponse,
    JobState,
    JobSemanticUIResponse,
    JobStatus,
    ProvenanceSource,
    QCStatus,
    QueueBlock,
    QueueItem,
    SemanticUIObject,
    SourceContext,
)
from app.llm import (
    CopilotContextBuilder,
    DecisionExplanationPromptBuilder,
    GroundedAnalystQAPromptBuilder,
    LLMClient,
    LLMClientError,
    LLMResponseValidationError,
    QueueSummaryPromptBuilder,
    SemanticUIPromptBuilder,
    build_llm_client,
)
from app.paths import display_path, resolve_local_path
from app.settings import AppSettings
from app.storage import SQLitePersistence

_PHASE7_ACCEPTANCE_REPORT_MAX_AGE = timedelta(hours=24)


@dataclass(frozen=True)
class Phase7GateSnapshot:
    state: str
    status: str | None = None
    can_begin: bool = False
    created_at: datetime | None = None


@dataclass(frozen=True)
class EvidencePolicyAssessment:
    warnings: tuple[str, ...] = ()
    analyst_qa_refusal_reason: str | None = None


@dataclass(frozen=True)
class CopilotService:
    settings: AppSettings
    persistence: SQLitePersistence
    llm_client: LLMClient | None = None
    llm_client_factory: Callable[[], LLMClient] | None = None
    context_builder: CopilotContextBuilder = field(default_factory=CopilotContextBuilder)
    decision_explanation_builder: DecisionExplanationPromptBuilder = field(
        default_factory=DecisionExplanationPromptBuilder
    )
    analyst_qa_builder: GroundedAnalystQAPromptBuilder = field(
        default_factory=GroundedAnalystQAPromptBuilder
    )
    queue_summary_builder: QueueSummaryPromptBuilder = field(
        default_factory=QueueSummaryPromptBuilder
    )
    semantic_ui_builder: SemanticUIPromptBuilder = field(default_factory=SemanticUIPromptBuilder)

    def build_decision_explanation(
        self,
        job_id: str,
        *,
        force_refresh: bool = False,
    ) -> JobCopilotResponse:
        job_status, decision, artifact_manifest = self._load_job_inputs(job_id)
        assessment = self._evaluate_evidence_policy(
            job_status=job_status,
            decision=decision,
            artifact_manifest=artifact_manifest,
        )
        if not force_refresh:
            cached = self._load_cached_copilot_output(
                artifact_manifest,
                artifact_id=self._copilot_artifact_id(job_id, "explanation"),
            )
            if cached is not None:
                return JobCopilotResponse(
                    job_status=job_status,
                    output_origin=self._cached_output_origin(job_status.job_id, detail="explanation"),
                    copilot=self._apply_runtime_labels_to_copilot(
                        cached,
                        job_status=job_status,
                        assessment=assessment,
                    ),
                )
        context = self.context_builder.build(
            decision,
            artifact_manifest=artifact_manifest,
            extra_warnings=assessment.warnings,
        )
        request = self.decision_explanation_builder.build_request(decision, context)
        copilot = self._run_copilot_request(request)
        self._persist_copilot_output(
            job_status=job_status,
            artifact_name="explanation",
            copilot=copilot,
        )
        return JobCopilotResponse(
            job_status=job_status,
            output_origin=self._llm_output_origin(),
            copilot=self._apply_runtime_labels_to_copilot(
                copilot,
                job_status=job_status,
                assessment=assessment,
            ),
        )

    def answer_analyst_question(self, job_id: str, *, question: str) -> JobCopilotResponse:
        job_status, decision, artifact_manifest = self._load_job_inputs(job_id)
        normalized_question = question.strip()
        if not normalized_question:
            raise ValueError("Analyst question must not be empty.")
        assessment = self._evaluate_evidence_policy(
            job_status=job_status,
            decision=decision,
            artifact_manifest=artifact_manifest,
            question=normalized_question,
        )
        if assessment.analyst_qa_refusal_reason is not None:
            refusal = self._build_policy_refusal(
                decision=decision,
                reason=assessment.analyst_qa_refusal_reason,
                block_id="qa_policy_refusal",
                title="Unavailable Evidence Slice",
                warnings=assessment.warnings,
            )
            return JobCopilotResponse(
                job_status=job_status,
                output_origin=self._fallback_output_origin(detail="policy_refusal"),
                copilot=self._apply_grounding_state_to_copilot(refusal, job_status=job_status),
            )
        context = self.context_builder.build(
            decision,
            artifact_manifest=artifact_manifest,
            user_question=normalized_question,
            extra_warnings=assessment.warnings,
        )
        request = self.analyst_qa_builder.build_request(
            decision,
            context,
            question=normalized_question,
        )
        copilot = self._run_copilot_request(request)
        return JobCopilotResponse(
            job_status=job_status,
            output_origin=self._llm_output_origin(),
            copilot=self._apply_runtime_labels_to_copilot(
                copilot,
                job_status=job_status,
                assessment=assessment,
            ),
        )

    def build_queue_summary(self, job_id: str) -> JobCopilotResponse:
        job_status, decision, artifact_manifest = self._load_job_inputs(job_id)
        assessment = self._evaluate_evidence_policy(
            job_status=job_status,
            decision=decision,
            artifact_manifest=artifact_manifest,
        )
        cached = self._load_cached_copilot_output(
            artifact_manifest,
            artifact_id=self._copilot_artifact_id(job_id, "queue_summary"),
        )
        if cached is not None:
            return JobCopilotResponse(
                job_status=job_status,
                output_origin=self._cached_output_origin(job_status.job_id, detail="queue_summary"),
                copilot=self._apply_runtime_labels_to_copilot(
                    cached,
                    job_status=job_status,
                    assessment=assessment,
                ),
            )
        queue_item = self._require_queue_item(job_id)
        context = self.context_builder.build(
            decision,
            artifact_manifest=artifact_manifest,
            extra_warnings=assessment.warnings,
        )
        request = self.queue_summary_builder.build_request(decision, context, queue_item)
        copilot = self._run_copilot_request(request)
        self._persist_copilot_output(
            job_status=job_status,
            artifact_name="queue_summary",
            copilot=copilot,
        )
        return JobCopilotResponse(
            job_status=job_status,
            output_origin=self._llm_output_origin(),
            copilot=self._apply_runtime_labels_to_copilot(
                copilot,
                job_status=job_status,
                assessment=assessment,
            ),
        )

    def build_semantic_ui(self, job_id: str) -> JobSemanticUIResponse:
        job_status, decision, artifact_manifest = self._load_job_inputs(job_id)
        assessment = self._evaluate_evidence_policy(
            job_status=job_status,
            decision=decision,
            artifact_manifest=artifact_manifest,
        )
        cached = self._load_cached_semantic_ui(
            artifact_manifest,
            artifact_id=self._semantic_ui_artifact_id(job_id),
        )
        queue_item = self._require_queue_item(job_id)
        if cached is not None:
            cached = self._refresh_semantic_ui_queue_block(cached, queue_item=queue_item)
            return JobSemanticUIResponse(
                job_id=job_status.job_id,
                output_origin=self._cached_output_origin(job_status.job_id, detail="semantic_ui"),
                semantic_ui=self._apply_runtime_labels_to_semantic_ui(
                    cached,
                    job_status=job_status,
                    assessment=assessment,
                ),
            )
        context = self.context_builder.build(
            decision,
            artifact_manifest=artifact_manifest,
            extra_warnings=assessment.warnings,
        )
        request = self.semantic_ui_builder.build_request(decision, context, queue_item)
        response = self._run_copilot_request(request)
        semantic_ui = response.semantic_ui
        if semantic_ui is None:
            if response.refusal_required:
                fallback_semantic_ui = self._build_semantic_ui_refusal_fallback(
                    decision=decision,
                    queue_item=queue_item,
                    reason=response.refusal_reason,
                )
                return JobSemanticUIResponse(
                    job_id=job_status.job_id,
                    output_origin=self._fallback_output_origin(detail="semantic_ui_refusal"),
                    semantic_ui=self._apply_runtime_labels_to_semantic_ui(
                        fallback_semantic_ui,
                        job_status=job_status,
                        assessment=assessment,
                    ),
                )
            raise LLMClientError("semantic_ui_payload did not include semantic_ui.")
        semantic_ui = self._refresh_semantic_ui_queue_block(semantic_ui, queue_item=queue_item)
        self._persist_semantic_ui(job_status=job_status, semantic_ui=semantic_ui)
        return JobSemanticUIResponse(
            job_id=job_status.job_id,
            output_origin=self._llm_output_origin(),
            semantic_ui=self._apply_runtime_labels_to_semantic_ui(
                semantic_ui,
                job_status=job_status,
                assessment=assessment,
            ),
        )

    def _load_job_inputs(self, job_id: str):
        job_status = self.persistence.get_job_status(job_id)
        if job_status is None:
            raise LookupError("Job not found.")
        decision_response = self.persistence.get_job_decision_response(job_id)
        if decision_response is None:
            raise LookupError("Decision not found.")
        artifact_manifest = self.persistence.get_job_artifact_manifest(job_id)
        return job_status, decision_response.decision, artifact_manifest

    def _require_queue_item(self, job_id: str) -> QueueItem:
        queue_item = self.persistence.get_queue_item(job_id)
        if queue_item is None:
            raise RuntimeError("Queue context is not ready for this job.")
        return queue_item

    def _run_copilot_request(self, request) -> CopilotResponse:
        runtime_request = self._runtime_llm_request(request)
        client = self._resolve_llm_client()
        validation_replays = 3
        last_error: LLMResponseValidationError | None = None
        for attempt_index in range(validation_replays):
            try:
                validated = client.generate_validated(runtime_request, CopilotResponse)
                return validated.parsed
            except LLMResponseValidationError as exc:
                last_error = exc
                if (
                    self._is_retryable_llm_validation_error(exc, runtime_request.operation)
                    and attempt_index + 1 < validation_replays
                ):
                    continue
                raise
        if last_error is not None:
            raise last_error
        raise LLMClientError(f"{runtime_request.operation} did not return a validated copilot response.")

    def _is_retryable_llm_validation_error(self, error: LLMResponseValidationError, operation: str) -> bool:
        message = str(error)
        return message in {
            f"{operation} did not return valid JSON output.",
            f"{operation} did not match CopilotResponse.",
        }

    def _resolve_llm_client(self) -> LLMClient:
        if self.llm_client is not None:
            return self.llm_client
        if self.llm_client_factory is not None:
            return self.llm_client_factory()
        return build_llm_client(self.settings)

    def _runtime_llm_request(self, request):
        return request.__class__(
            operation=request.operation,
            messages=request.messages,
            metadata=request.metadata,
            output_format=request.output_format,
            timeout_seconds=self.settings.llm.timeout_seconds,
            retry_count=self.settings.llm.retry_count,
            model=request.model,
            max_output_tokens=request.max_output_tokens,
            reasoning_enabled=request.reasoning_enabled,
        )

    def _llm_output_origin(self) -> CopilotOutputOrigin:
        mode = CopilotOutputMode.MOCK if self.settings.llm.mock_mode else CopilotOutputMode.LIVE_LLM
        return CopilotOutputOrigin(
            mode=mode,
            provider=self.settings.llm.provider,
            detail="provider_response",
        )

    def _cached_output_origin(self, job_id: str, *, detail: str) -> CopilotOutputOrigin:
        return CopilotOutputOrigin(
            mode=CopilotOutputMode.CACHED,
            provider=self.settings.llm.provider,
            detail=f"{job_id}_{detail}",
        )

    def _fallback_output_origin(self, *, detail: str) -> CopilotOutputOrigin:
        return CopilotOutputOrigin(
            mode=CopilotOutputMode.FALLBACK,
            provider=self.settings.llm.provider,
            detail=detail,
        )

    def _apply_runtime_labels_to_copilot(
        self,
        copilot: CopilotResponse,
        *,
        job_status: JobStatus,
        assessment: EvidencePolicyAssessment,
    ) -> CopilotResponse:
        policy_labeled = self._apply_evidence_policy_to_copilot(copilot, assessment=assessment)
        return self._apply_grounding_state_to_copilot(policy_labeled, job_status=job_status)

    def _apply_runtime_labels_to_semantic_ui(
        self,
        semantic_ui: SemanticUIObject,
        *,
        job_status: JobStatus,
        assessment: EvidencePolicyAssessment,
    ) -> SemanticUIObject:
        policy_labeled = self._apply_evidence_policy_to_semantic_ui(semantic_ui, assessment=assessment)
        return self._apply_grounding_state_to_semantic_ui(policy_labeled, job_status=job_status)

    def _apply_grounding_state_to_copilot(
        self,
        copilot: CopilotResponse,
        *,
        job_status: JobStatus,
    ) -> CopilotResponse:
        grounding_message = self._grounding_status_message(job_status)
        warnings = self._prepend_unique(copilot.warnings, grounding_message)
        if copilot.refusal_required:
            return copilot.model_copy(update={"warnings": warnings})

        cited_evidence_ids = self._prepend_unique(copilot.cited_evidence_ids, "decision_object__summary")
        answer_blocks = [
            block
            for block in copilot.answer_blocks
            if block.block_id != "grounding_status"
        ]
        answer_blocks.insert(
            0,
            CopilotAnswerBlock(
                block_id="grounding_status",
                block_type="bullets",
                title="Grounding Status",
                content=grounding_message,
                cited_evidence_ids=["decision_object__summary"],
            ),
        )
        return copilot.model_copy(
            update={
                "warnings": warnings,
                "cited_evidence_ids": cited_evidence_ids,
                "answer_blocks": answer_blocks,
            }
        )

    def _apply_evidence_policy_to_copilot(
        self,
        copilot: CopilotResponse,
        *,
        assessment: EvidencePolicyAssessment,
    ) -> CopilotResponse:
        if not assessment.warnings:
            return copilot
        warnings = list(copilot.warnings)
        for warning in reversed(assessment.warnings):
            warnings = self._prepend_unique(warnings, warning)
        if copilot.refusal_required:
            return copilot.model_copy(update={"warnings": warnings})

        cited_evidence_ids = self._prepend_unique(copilot.cited_evidence_ids, "decision_object__warnings")
        answer_blocks = [
            block
            for block in copilot.answer_blocks
            if block.block_id != "evidence_limitations"
        ]
        answer_blocks.insert(
            0,
            CopilotAnswerBlock(
                block_id="evidence_limitations",
                block_type="bullets",
                title="Evidence Limitations",
                content="\n".join(f"- {warning}" for warning in assessment.warnings[:3]),
                cited_evidence_ids=["decision_object__warnings"],
            ),
        )
        return copilot.model_copy(
            update={
                "warnings": warnings,
                "cited_evidence_ids": cited_evidence_ids,
                "answer_blocks": answer_blocks,
            }
        )

    def _apply_grounding_state_to_semantic_ui(
        self,
        semantic_ui: SemanticUIObject,
        *,
        job_status: JobStatus,
    ) -> SemanticUIObject:
        grounding_message = self._grounding_status_message(job_status)
        notes = self._prepend_unique(semantic_ui.notes, grounding_message)
        return semantic_ui.model_copy(update={"notes": notes})

    def _apply_evidence_policy_to_semantic_ui(
        self,
        semantic_ui: SemanticUIObject,
        *,
        assessment: EvidencePolicyAssessment,
    ) -> SemanticUIObject:
        if not assessment.warnings:
            return semantic_ui
        notes = list(semantic_ui.notes)
        for warning in reversed(assessment.warnings):
            notes = self._prepend_unique(notes, warning)
        return semantic_ui.model_copy(update={"notes": notes})

    def _refresh_semantic_ui_queue_block(
        self,
        semantic_ui: SemanticUIObject,
        *,
        queue_item: QueueItem,
    ) -> SemanticUIObject:
        queue_block = semantic_ui.queue_block
        if queue_block is None:
            queue_block = QueueBlock(title="Analyst Queue", items=[queue_item])
        else:
            queue_block = queue_block.model_copy(update={"items": [queue_item]})
        return semantic_ui.model_copy(update={"queue_block": queue_block})

    def _grounding_status_message(self, job_status: JobStatus) -> str:
        phase7_gate = self._phase7_gate_snapshot()
        if phase7_gate.state == "missing":
            return (
                "Grounding status: not live-grounded. No current Phase 6B acceptance report was found, "
                "so this output must not be presented as live-grounded."
            )
        if phase7_gate.state == "stale":
            return (
                "Grounding status: not live-grounded. The current Phase 6B acceptance report is stale, "
                "so live-grounded status cannot be confirmed."
            )
        if phase7_gate.state == "blocked":
            return (
                "Grounding status: not live-grounded. The current Phase 6B gate is blocked, "
                "so this output must be treated as degraded/demo context."
            )
        if job_status.status == JobState.DEGRADED:
            return (
                "Grounding status: degraded. The persisted job status is degraded, "
                "so this output must not be described as fully live-grounded."
            )
        if job_status.status != JobState.COMPLETED:
            return (
                f"Grounding status: not live-grounded. The persisted job status is {job_status.status.value}, "
                "not completed."
            )
        return (
            "Grounding status: live-grounded. The current Phase 6B gate is ready and the persisted job status is completed."
        )

    def _phase7_gate_snapshot(self) -> Phase7GateSnapshot:
        report_path = self._latest_phase6b_acceptance_report_path()
        if report_path is None:
            return Phase7GateSnapshot(state="missing")
        try:
            payload = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return Phase7GateSnapshot(state="missing")
        phase7_gate = payload.get("phase7_gate")
        if not isinstance(phase7_gate, dict):
            return Phase7GateSnapshot(state="missing")
        created_at = self._parse_report_timestamp(payload.get("created_at"))
        if created_at is None or datetime.now(UTC) - created_at > _PHASE7_ACCEPTANCE_REPORT_MAX_AGE:
            return Phase7GateSnapshot(
                state="stale",
                status=str(phase7_gate.get("status", "")).strip().lower() or None,
                can_begin=bool(phase7_gate.get("can_begin")),
                created_at=created_at,
            )
        gate_status = str(phase7_gate.get("status", "")).strip().lower()
        can_begin = bool(phase7_gate.get("can_begin"))
        if gate_status != "ready" or not can_begin:
            return Phase7GateSnapshot(
                state="blocked",
                status=gate_status or None,
                can_begin=can_begin,
                created_at=created_at,
            )
        return Phase7GateSnapshot(
            state="ready",
            status=gate_status or None,
            can_begin=can_begin,
            created_at=created_at,
        )

    def _parse_report_timestamp(self, value: object) -> datetime | None:
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    def _evaluate_evidence_policy(
        self,
        *,
        job_status: JobStatus,
        decision: DecisionObject,
        artifact_manifest: ArtifactManifest | None,
        question: str | None = None,
    ) -> EvidencePolicyAssessment:
        warnings: list[str] = []
        if artifact_manifest is None or not artifact_manifest.artifacts:
            warnings.append("Evidence policy: Artifact provenance is unavailable in the persisted manifest.")
        gate_snapshot = self._phase7_gate_snapshot()
        if gate_snapshot.state == "stale":
            warnings.append(
                "Evidence policy: The current Phase 6B acceptance report is stale, so live-grounded availability cannot be confirmed."
            )
        if (
            job_status.status == JobState.DEGRADED
            or "fixture_mode_active" in job_status.warnings
            or decision.sample.metadata.source_context == SourceContext.FIXTURE
            or decision.sample.metadata.provenance_source == ProvenanceSource.FIXTURE
        ):
            warnings.append("Evidence policy: The saved job context is degraded or fixture-backed.")
        if decision.assembly_qc.qc_status != QCStatus.PASS:
            warnings.append("Evidence policy: Assembly QC is not fully passing.")
        if not decision.mechanistic_evidence:
            warnings.append("Evidence policy: Mechanistic evidence is unavailable in the saved decision.")
        if decision.novelty_assessment.missing_reference:
            warnings.append("Evidence policy: Novelty reference support is unavailable in the saved decision.")
        elif decision.novelty_assessment.uncertainty_flag:
            warnings.append("Evidence policy: Novelty evidence is sparse or uncertain.")
        if decision.phenotype_prediction.calibration_status != CalibrationStatus.CALIBRATED:
            warnings.append("Evidence policy: Prediction calibration is unavailable or not fully supported.")
        if decision.actionability_features.metadata_completeness < 1.0:
            warnings.append("Evidence policy: Metadata completeness is partial.")
        return EvidencePolicyAssessment(
            warnings=tuple(warnings),
            analyst_qa_refusal_reason=self._analyst_qa_refusal_reason(
                decision=decision,
                artifact_manifest=artifact_manifest,
                question=question,
            ),
        )

    def _analyst_qa_refusal_reason(
        self,
        *,
        decision: DecisionObject,
        artifact_manifest: ArtifactManifest | None,
        question: str | None,
    ) -> str | None:
        if question is None:
            return None
        normalized = question.strip().lower()
        if not normalized:
            return None
        if self._question_is_unsafe_bio_request(normalized):
            return (
                "This copilot does not assist with pathogen design, evasion, or other unsafe biological requests."
            )
        if self._question_is_private_threshold_request(normalized):
            return (
                "Private threshold details are not included in the grounded context, so the requested threshold-level answer is unavailable in evidence."
            )
        if self._question_is_clinical_overclaim_request(normalized):
            return (
                "This copilot cannot provide clinical directives or claim confirmed diagnosis from a surveillance triage record."
            )
        if self._question_targets_slice(
            normalized,
            ("mechanism", "mechanistic", "gene", "mutation", "marker", "supported signal"),
        ) and not decision.mechanistic_evidence:
            return (
                "Mechanistic evidence is not recorded for this job, so the requested mechanism-level answer is unavailable in evidence."
            )
        if self._question_targets_slice(
            normalized,
            ("novelty", "neighbor", "reference snapshot", "lineage"),
        ) and decision.novelty_assessment.missing_reference:
            return (
                "Novelty reference support is not recorded for this job, so the requested novelty-level answer is unavailable in evidence."
            )
        if self._question_targets_slice(
            normalized,
            ("artifact", "manifest", "provenance", "raw output", "source file"),
        ) and (artifact_manifest is None or not artifact_manifest.artifacts):
            return (
                "Artifact provenance is not recorded for this job, so the requested provenance-level answer is unavailable in evidence."
            )
        if self._question_targets_slice(
            normalized,
            ("calibration", "calibrated"),
        ) and decision.phenotype_prediction.calibration_status != CalibrationStatus.CALIBRATED:
            return (
                "Prediction calibration is not available for this job, so a calibration-level answer would not be supported by evidence."
            )
        return None

    def _question_targets_slice(self, question: str, keywords: tuple[str, ...]) -> bool:
        return any(keyword in question for keyword in keywords)

    def _question_is_unsafe_bio_request(self, question: str) -> bool:
        return any(
            keyword in question
            for keyword in (
                "engineer",
                "increase virulence",
                "make it more virulent",
                "evade detection",
                "bypass surveillance",
                "pathogen design",
                "optimize resistance",
                "improve spread",
            )
        )

    def _question_is_private_threshold_request(self, question: str) -> bool:
        return any(
            keyword in question
            for keyword in (
                "exact threshold",
                "internal threshold",
                "private threshold",
                "cutoff value",
                "what cutoff",
            )
        )

    def _question_is_clinical_overclaim_request(self, question: str) -> bool:
        return any(
            keyword in question
            for keyword in (
                "should we treat",
                "what should i prescribe",
                "what antibiotic should",
                "is this diagnosis",
                "clinically confirmed",
                "confirm diagnosis",
            )
        )

    def _build_policy_refusal(
        self,
        *,
        decision: DecisionObject,
        reason: str,
        block_id: str,
        title: str,
        warnings: tuple[str, ...] = (),
    ) -> CopilotResponse:
        return CopilotResponse(
            job_id=decision.job_id or decision.triage_decision.job_id,
            sample_id=decision.sample.sample_id,
            target_drug=decision.sample.target_drug,
            summary=None,
            next_steps=[],
            refusal_required=True,
            refusal_reason=reason,
            cited_evidence_ids=[],
            answer_blocks=[
                CopilotAnswerBlock(
                    block_id=block_id,
                    block_type="refusal",
                    title=title,
                    content=reason,
                    cited_evidence_ids=[],
                )
            ],
            warnings=list(warnings),
        )

    def _build_semantic_ui_refusal_fallback(
        self,
        *,
        decision: DecisionObject,
        queue_item: QueueItem,
        reason: str | None,
    ) -> SemanticUIObject:
        summary = reason or (
            "Grounded semantic UI is unavailable because the available evidence is too weak "
            "to construct a safe structured analyst surface."
        )
        return SemanticUIObject(
            decision_card=DecisionCardBlock(
                title="Grounded Semantic UI Unavailable",
                triage_decision=decision.triage_decision.triage,
                severity=decision.triage_decision.severity,
                summary=summary,
                metrics=[],
            ),
            queue_block=QueueBlock(
                title="Analyst Queue",
                items=[queue_item],
            ),
            notes=[
                "Semantic UI fallback: provider returned a grounded refusal for this job.",
                summary,
            ],
        )

    def _latest_phase6b_acceptance_report_path(self) -> Path | None:
        acceptance_root = self.settings.artifact_root / "runs" / "phase6b_acceptance"
        preferred = acceptance_root / "latest" / "phase6b_acceptance_report.json"
        if preferred.exists():
            return preferred
        try:
            candidates = sorted(
                acceptance_root.rglob("phase6b_acceptance_report.json"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return None
        return candidates[0] if candidates else None

    def _load_cached_copilot_output(
        self,
        artifact_manifest: ArtifactManifest | None,
        *,
        artifact_id: str,
    ) -> CopilotResponse | None:
        record = self._artifact_record(artifact_manifest, artifact_id=artifact_id)
        if record is None:
            return None
        try:
            path = resolve_local_path(record.path, repo_root=self.settings.repo_root)
            return CopilotResponse.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError, ValueError):
            return None

    def _load_cached_semantic_ui(
        self,
        artifact_manifest: ArtifactManifest | None,
        *,
        artifact_id: str,
    ) -> SemanticUIObject | None:
        record = self._artifact_record(artifact_manifest, artifact_id=artifact_id)
        if record is None:
            return None
        try:
            path = resolve_local_path(record.path, repo_root=self.settings.repo_root)
            return SemanticUIObject.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError, ValueError):
            return None

    def _persist_copilot_output(
        self,
        *,
        job_status: JobStatus,
        artifact_name: str,
        copilot: CopilotResponse,
    ) -> None:
        path = self._job_output_dir(job_status.job_id) / f"{artifact_name}.json"
        self._write_json(path, copilot.model_dump(mode="json"))
        self.persistence.save_artifacts(
            [
                ArtifactRecord(
                    artifact_id=self._copilot_artifact_id(job_status.job_id, artifact_name),
                    job_id=job_status.job_id,
                    sample_id=job_status.sample_id,
                    target_drug=job_status.target_drug,
                    kind=ArtifactKind.COPILOT_OUTPUT,
                    path=display_path(path, repo_root=self.settings.repo_root),
                    media_type="application/json",
                    generated_by="copilot_service",
                    size_bytes=path.stat().st_size,
                )
            ]
        )

    def _persist_semantic_ui(
        self,
        *,
        job_status: JobStatus,
        semantic_ui: SemanticUIObject,
    ) -> None:
        path = self._job_output_dir(job_status.job_id) / "semantic_ui.json"
        self._write_json(path, semantic_ui.model_dump(mode="json"))
        self.persistence.save_artifacts(
            [
                ArtifactRecord(
                    artifact_id=self._semantic_ui_artifact_id(job_status.job_id),
                    job_id=job_status.job_id,
                    sample_id=job_status.sample_id,
                    target_drug=job_status.target_drug,
                    kind=ArtifactKind.SEMANTIC_UI,
                    path=display_path(path, repo_root=self.settings.repo_root),
                    media_type="application/json",
                    generated_by="copilot_service",
                    size_bytes=path.stat().st_size,
                )
            ]
        )

    def _artifact_record(
        self,
        artifact_manifest: ArtifactManifest | None,
        *,
        artifact_id: str,
    ) -> ArtifactRecord | None:
        if artifact_manifest is None:
            return None
        for artifact in artifact_manifest.artifacts:
            if artifact.artifact_id == artifact_id:
                return artifact
        return None

    def _job_output_dir(self, job_id: str) -> Path:
        return self.settings.artifact_root / "runs" / "jobs" / job_id / "copilot"

    def _copilot_artifact_id(self, job_id: str, artifact_name: str) -> str:
        return f"{job_id}_copilot_{artifact_name}_json"

    def _semantic_ui_artifact_id(self, job_id: str) -> str:
        return f"{job_id}_semantic_ui_json"

    def _write_json(self, path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _prepend_unique(self, items: list[str], value: str) -> list[str]:
        normalized = [item for item in items if item != value]
        return [value, *normalized]
