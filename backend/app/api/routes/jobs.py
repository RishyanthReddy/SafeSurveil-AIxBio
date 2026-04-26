from __future__ import annotations

import base64
import json

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import ValidationError

from app.api.dependencies import get_analysis_service, get_copilot_service, get_persistence, get_settings
from app.contracts import (
    AnalyzeJobRequest,
    AnalyzeJobResponse,
    ArtifactManifest,
    ArtifactPreviewResponse,
    CopilotResponse,
    EvidenceGraph,
    ExecutionGateReport,
    JobCopilotResponse,
    JobDecisionResponse,
    JobSemanticUIResponse,
    JobStatus,
    JobThesysC1Response,
    ReasoningTrace,
    SemanticUIObject,
    ThesysC1RenderStatus,
    V2AuditBundle,
)
from app.demo_data import (
    get_demo_artifact_manifest,
    get_demo_artifact_preview,
    get_demo_copilot_response,
    get_demo_job_decision_response,
    get_demo_job_status,
    get_demo_semantic_ui_response,
)
from app.integrations import (
    ThesysC1ConfigurationError,
    ThesysC1Error,
    build_thesys_c1_client,
)
from app.llm import LLMClientError
from app.paths import path_is_within, resolve_local_path
from app.services import (
    AnalysisService,
    CopilotService,
    build_evidence_graph,
    build_execution_gate_report,
    build_reasoning_trace,
    build_v2_audit_bundle,
)
from app.settings import AppSettings
from app.storage import SQLitePersistence


router = APIRouter(prefix="/jobs", tags=["jobs"])


def _artifact_manifest_for_job(
    *,
    job_id: str,
    persistence: SQLitePersistence,
    settings: AppSettings,
) -> ArtifactManifest | None:
    artifact_manifest = persistence.get_job_artifact_manifest(job_id)
    if artifact_manifest is not None:
        return artifact_manifest
    if settings.demo_mode:
        return get_demo_artifact_manifest(job_id)
    return None


def _job_decision_response_for_job(
    *,
    job_id: str,
    persistence: SQLitePersistence,
    settings: AppSettings,
) -> JobDecisionResponse | None:
    decision_response = persistence.get_job_decision_response(job_id)
    if decision_response is not None:
        return decision_response
    if settings.demo_mode:
        return get_demo_job_decision_response(job_id)
    return None


def _artifact_record(
    artifact_manifest: ArtifactManifest | None,
    *,
    artifact_id: str,
):
    if artifact_manifest is None:
        return None
    for artifact in artifact_manifest.artifacts:
        if artifact.artifact_id == artifact_id:
            return artifact
    return None


def _read_artifact_json_text(
    *,
    artifact_manifest: ArtifactManifest | None,
    artifact_id: str,
    settings: AppSettings,
) -> str | None:
    artifact = _artifact_record(artifact_manifest, artifact_id=artifact_id)
    if artifact is None:
        return None
    artifact_path = resolve_local_path(artifact.path, repo_root=settings.repo_root)
    if not (
        path_is_within(artifact_path, root=settings.repo_root)
        or path_is_within(artifact_path, root=settings.artifact_root)
    ):
        return None
    try:
        return artifact_path.read_text(encoding="utf-8")
    except OSError:
        return None


def _cached_copilot_for_job(
    *,
    job_id: str,
    artifact_manifest: ArtifactManifest | None,
    settings: AppSettings,
) -> tuple[CopilotResponse | None, str | None]:
    for artifact_name in ("explanation", "queue_summary"):
        artifact_id = f"{job_id}_copilot_{artifact_name}_json"
        payload = _read_artifact_json_text(
            artifact_manifest=artifact_manifest,
            artifact_id=artifact_id,
            settings=settings,
        )
        if payload is None:
            continue
        try:
            return CopilotResponse.model_validate_json(payload), artifact_id
        except (ValidationError, ValueError):
            continue
    if settings.demo_mode:
        demo_response = get_demo_copilot_response(job_id, mode="explanation")
        if demo_response is not None:
            return demo_response.copilot, "demo_copilot_explanation"
    return None, None


def _cached_semantic_ui_for_job(
    *,
    job_id: str,
    artifact_manifest: ArtifactManifest | None,
    settings: AppSettings,
) -> tuple[SemanticUIObject | None, str | None]:
    artifact_id = f"{job_id}_semantic_ui_json"
    payload = _read_artifact_json_text(
        artifact_manifest=artifact_manifest,
        artifact_id=artifact_id,
        settings=settings,
    )
    if payload is not None:
        try:
            return SemanticUIObject.model_validate_json(payload), artifact_id
        except (ValidationError, ValueError):
            pass
    if settings.demo_mode:
        demo_response = get_demo_semantic_ui_response(job_id)
        if demo_response is not None:
            return demo_response.semantic_ui, "demo_semantic_ui"
    return None, None


def _preview_artifact_content(
    *,
    job_id: str,
    artifact_id: str,
    artifact_manifest: ArtifactManifest,
    settings: AppSettings,
    max_bytes: int,
) -> ArtifactPreviewResponse:
    normalized_artifact_id = artifact_id.strip().lower()
    artifact = next(
        (
            item
            for item in artifact_manifest.artifacts
            if item.artifact_id == normalized_artifact_id
        ),
        None,
    )
    if artifact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found.")
    if not artifact.preview_eligible:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Artifact media type is not previewable.",
        )
    artifact_path = resolve_local_path(artifact.path, repo_root=settings.repo_root)
    if not (
        path_is_within(artifact_path, root=settings.repo_root)
        or path_is_within(artifact_path, root=settings.artifact_root)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Artifact path is outside allowed preview roots.",
        )
    try:
        size_bytes = artifact_path.stat().st_size
        with artifact_path.open("rb") as handle:
            raw_content = handle.read(max_bytes + 1)
    except OSError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact file not found.") from exc

    truncated = len(raw_content) > max_bytes
    raw_content = raw_content[:max_bytes]
    encoding = "base64"
    content = base64.b64encode(raw_content).decode("ascii")
    if artifact.media_type.startswith("text/") or artifact.media_type == "application/json":
        encoding = "utf-8"
        content = raw_content.decode("utf-8", errors="replace")
        if artifact.media_type == "application/json":
            try:
                content = json.dumps(json.loads(content), indent=2, sort_keys=True)
            except json.JSONDecodeError:
                pass
    return ArtifactPreviewResponse(
        job_id=job_id,
        artifact_id=artifact.artifact_id,
        media_type=artifact.media_type,
        encoding=encoding,
        content=content,
        truncated=truncated,
        size_bytes=size_bytes,
    )


def _semantic_ui_response_for_job(
    *,
    job_id: str,
    service: CopilotService,
    settings: AppSettings,
) -> JobSemanticUIResponse:
    try:
        return service.build_semantic_ui(job_id)
    except LookupError as exc:
        if settings.demo_mode:
            demo_response = get_demo_semantic_ui_response(job_id)
            if demo_response is not None:
                return demo_response
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except LLMClientError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/analyze", response_model=AnalyzeJobResponse, status_code=status.HTTP_201_CREATED)
def analyze_job(
    request: AnalyzeJobRequest,
    service: AnalysisService = Depends(get_analysis_service),
) -> AnalyzeJobResponse:
    try:
        result = service.analyze(request)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return result.response


@router.get("/{job_id}/status", response_model=JobStatus)
def get_job_status(
    job_id: str,
    persistence: SQLitePersistence = Depends(get_persistence),
    settings: AppSettings = Depends(get_settings),
) -> JobStatus:
    job_status = persistence.get_job_status(job_id)
    if job_status is None:
        if settings.demo_mode:
            demo_status = get_demo_job_status(job_id)
            if demo_status is not None:
                return demo_status
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")
    return job_status


@router.get("/{job_id}/decision", response_model=JobDecisionResponse)
def get_job_decision(
    job_id: str,
    persistence: SQLitePersistence = Depends(get_persistence),
    settings: AppSettings = Depends(get_settings),
) -> JobDecisionResponse:
    decision_response = _job_decision_response_for_job(
        job_id=job_id,
        persistence=persistence,
        settings=settings,
    )
    if decision_response is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Decision not found.")
    return decision_response


@router.get("/{job_id}/artifacts", response_model=ArtifactManifest)
def get_job_artifacts(
    job_id: str,
    persistence: SQLitePersistence = Depends(get_persistence),
    settings: AppSettings = Depends(get_settings),
) -> ArtifactManifest:
    artifact_manifest = persistence.get_job_artifact_manifest(job_id)
    if artifact_manifest is None:
        if settings.demo_mode:
            demo_manifest = get_demo_artifact_manifest(job_id)
            if demo_manifest is not None:
                return demo_manifest
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifacts not found.")
    return artifact_manifest


@router.get("/{job_id}/verification", response_model=ExecutionGateReport)
def get_job_verification(
    job_id: str,
    persistence: SQLitePersistence = Depends(get_persistence),
    settings: AppSettings = Depends(get_settings),
) -> ExecutionGateReport:
    decision_response = _job_decision_response_for_job(
        job_id=job_id,
        persistence=persistence,
        settings=settings,
    )
    if decision_response is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Decision not found.")
    artifact_manifest = _artifact_manifest_for_job(
        job_id=job_id,
        persistence=persistence,
        settings=settings,
    )
    copilot, copilot_artifact_id = _cached_copilot_for_job(
        job_id=job_id,
        artifact_manifest=artifact_manifest,
        settings=settings,
    )
    semantic_ui, semantic_ui_artifact_id = _cached_semantic_ui_for_job(
        job_id=job_id,
        artifact_manifest=artifact_manifest,
        settings=settings,
    )
    if semantic_ui is None and copilot is not None and copilot.semantic_ui is not None:
        semantic_ui = copilot.semantic_ui
        semantic_ui_artifact_id = copilot_artifact_id
    reasoning_trace = build_reasoning_trace(decision_response.decision)
    return build_execution_gate_report(
        decision_response.decision,
        artifact_manifest=artifact_manifest,
        semantic_ui=semantic_ui,
        copilot=copilot,
        reasoning_trace=reasoning_trace,
        metadata={
            "job_status": decision_response.job_status.status.value,
            "artifact_manifest_available": artifact_manifest is not None,
            "copilot_sidecar_available": copilot is not None,
            "semantic_ui_sidecar_available": semantic_ui is not None,
            "reasoning_trace_available": True,
            "copilot_artifact_id": copilot_artifact_id,
            "semantic_ui_artifact_id": semantic_ui_artifact_id,
            "provider_calls_triggered": False,
        },
    )


@router.get("/{job_id}/reasoning-trace", response_model=ReasoningTrace)
def get_job_reasoning_trace(
    job_id: str,
    persistence: SQLitePersistence = Depends(get_persistence),
    settings: AppSettings = Depends(get_settings),
) -> ReasoningTrace:
    decision_response = _job_decision_response_for_job(
        job_id=job_id,
        persistence=persistence,
        settings=settings,
    )
    if decision_response is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Decision not found.")
    try:
        return build_reasoning_trace(decision_response.decision)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/{job_id}/evidence-graph", response_model=EvidenceGraph)
def get_job_evidence_graph(
    job_id: str,
    persistence: SQLitePersistence = Depends(get_persistence),
    settings: AppSettings = Depends(get_settings),
) -> EvidenceGraph:
    decision_response = _job_decision_response_for_job(
        job_id=job_id,
        persistence=persistence,
        settings=settings,
    )
    if decision_response is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Decision not found.")

    artifact_manifest = _artifact_manifest_for_job(
        job_id=job_id,
        persistence=persistence,
        settings=settings,
    )
    copilot, copilot_artifact_id = _cached_copilot_for_job(
        job_id=job_id,
        artifact_manifest=artifact_manifest,
        settings=settings,
    )
    semantic_ui, semantic_ui_artifact_id = _cached_semantic_ui_for_job(
        job_id=job_id,
        artifact_manifest=artifact_manifest,
        settings=settings,
    )
    if semantic_ui is None and copilot is not None and copilot.semantic_ui is not None:
        semantic_ui = copilot.semantic_ui
        semantic_ui_artifact_id = copilot_artifact_id

    try:
        reasoning_trace = build_reasoning_trace(decision_response.decision)
        execution_gate = build_execution_gate_report(
            decision_response.decision,
            artifact_manifest=artifact_manifest,
            semantic_ui=semantic_ui,
            copilot=copilot,
            reasoning_trace=reasoning_trace,
            metadata={
                "job_status": decision_response.job_status.status.value,
                "artifact_manifest_available": artifact_manifest is not None,
                "copilot_sidecar_available": copilot is not None,
                "semantic_ui_sidecar_available": semantic_ui is not None,
                "reasoning_trace_available": True,
                "copilot_artifact_id": copilot_artifact_id,
                "semantic_ui_artifact_id": semantic_ui_artifact_id,
                "provider_calls_triggered": False,
            },
        )
        graph = build_evidence_graph(
            decision_response.decision,
            artifact_manifest=artifact_manifest,
            copilot=copilot,
            execution_gate=execution_gate,
            reasoning_trace=reasoning_trace,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return graph


@router.get("/{job_id}/v2-audit", response_model=V2AuditBundle)
def get_job_v2_audit(
    job_id: str,
    persistence: SQLitePersistence = Depends(get_persistence),
    settings: AppSettings = Depends(get_settings),
) -> V2AuditBundle:
    decision_response = _job_decision_response_for_job(
        job_id=job_id,
        persistence=persistence,
        settings=settings,
    )
    if decision_response is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Decision not found.")

    artifact_manifest = _artifact_manifest_for_job(
        job_id=job_id,
        persistence=persistence,
        settings=settings,
    )
    copilot, copilot_artifact_id = _cached_copilot_for_job(
        job_id=job_id,
        artifact_manifest=artifact_manifest,
        settings=settings,
    )
    semantic_ui, semantic_ui_artifact_id = _cached_semantic_ui_for_job(
        job_id=job_id,
        artifact_manifest=artifact_manifest,
        settings=settings,
    )
    if semantic_ui is None and copilot is not None and copilot.semantic_ui is not None:
        semantic_ui = copilot.semantic_ui
        semantic_ui_artifact_id = copilot_artifact_id

    try:
        reasoning_trace = build_reasoning_trace(decision_response.decision)
        execution_gate = build_execution_gate_report(
            decision_response.decision,
            artifact_manifest=artifact_manifest,
            semantic_ui=semantic_ui,
            copilot=copilot,
            reasoning_trace=reasoning_trace,
            metadata={
                "job_status": decision_response.job_status.status.value,
                "artifact_manifest_available": artifact_manifest is not None,
                "copilot_sidecar_available": copilot is not None,
                "semantic_ui_sidecar_available": semantic_ui is not None,
                "reasoning_trace_available": True,
                "copilot_artifact_id": copilot_artifact_id,
                "semantic_ui_artifact_id": semantic_ui_artifact_id,
                "provider_calls_triggered": False,
            },
        )
        evidence_graph = build_evidence_graph(
            decision_response.decision,
            artifact_manifest=artifact_manifest,
            copilot=copilot,
            execution_gate=execution_gate,
            reasoning_trace=reasoning_trace,
        )
        return build_v2_audit_bundle(
            decision_response=decision_response,
            settings=settings,
            artifact_manifest=artifact_manifest,
            execution_gate=execution_gate,
            reasoning_trace=reasoning_trace,
            evidence_graph=evidence_graph,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/{job_id}/artifacts/{artifact_id}/preview", response_model=ArtifactPreviewResponse)
def preview_job_artifact(
    job_id: str,
    artifact_id: str,
    max_bytes: int = Query(default=65536, ge=1, le=262144),
    persistence: SQLitePersistence = Depends(get_persistence),
    settings: AppSettings = Depends(get_settings),
) -> ArtifactPreviewResponse:
    artifact_manifest = _artifact_manifest_for_job(
        job_id=job_id,
        persistence=persistence,
        settings=settings,
    )
    if artifact_manifest is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifacts not found.")
    if settings.demo_mode:
        demo_preview = get_demo_artifact_preview(
            job_id,
            artifact_id,
            max_bytes=max_bytes,
        )
        if demo_preview is not None:
            return demo_preview
    return _preview_artifact_content(
        job_id=job_id,
        artifact_id=artifact_id,
        artifact_manifest=artifact_manifest,
        settings=settings,
        max_bytes=max_bytes,
    )


@router.get("/{job_id}/copilot/explanation", response_model=JobCopilotResponse)
def get_job_copilot_explanation(
    job_id: str,
    refresh: bool = Query(default=False),
    service: CopilotService = Depends(get_copilot_service),
    settings: AppSettings = Depends(get_settings),
) -> JobCopilotResponse:
    try:
        return service.build_decision_explanation(job_id, force_refresh=refresh)
    except LookupError as exc:
        if settings.demo_mode:
            demo_response = get_demo_copilot_response(job_id, mode="explanation")
            if demo_response is not None:
                return demo_response
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except LLMClientError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.get("/{job_id}/copilot/answer", response_model=JobCopilotResponse)
def get_job_copilot_answer(
    job_id: str,
    question: str = Query(min_length=3, max_length=400),
    service: CopilotService = Depends(get_copilot_service),
    settings: AppSettings = Depends(get_settings),
) -> JobCopilotResponse:
    try:
        return service.answer_analyst_question(job_id, question=question)
    except LookupError as exc:
        if settings.demo_mode:
            demo_response = get_demo_copilot_response(job_id, mode="answer", question=question)
            if demo_response is not None:
                return demo_response
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except LLMClientError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.get("/{job_id}/copilot/queue-summary", response_model=JobCopilotResponse)
def get_job_copilot_queue_summary(
    job_id: str,
    service: CopilotService = Depends(get_copilot_service),
    settings: AppSettings = Depends(get_settings),
) -> JobCopilotResponse:
    try:
        return service.build_queue_summary(job_id)
    except LookupError as exc:
        if settings.demo_mode:
            demo_response = get_demo_copilot_response(job_id, mode="queue_summary")
            if demo_response is not None:
                return demo_response
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except LLMClientError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/{job_id}/semantic-ui", response_model=JobSemanticUIResponse)
def get_job_semantic_ui(
    job_id: str,
    service: CopilotService = Depends(get_copilot_service),
    settings: AppSettings = Depends(get_settings),
) -> JobSemanticUIResponse:
    return _semantic_ui_response_for_job(job_id=job_id, service=service, settings=settings)


@router.get("/{job_id}/semantic-ui/c1", response_model=JobThesysC1Response)
def get_job_semantic_ui_c1(
    job_id: str,
    service: CopilotService = Depends(get_copilot_service),
    settings: AppSettings = Depends(get_settings),
) -> JobThesysC1Response:
    semantic_ui_response = _semantic_ui_response_for_job(
        job_id=job_id,
        service=service,
        settings=settings,
    )
    job_status = service.persistence.get_job_status(job_id)
    if job_status is None and settings.demo_mode:
        job_status = get_demo_job_status(job_id)
    if job_status is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")
    origin_kwargs = {
        "provider": "thesys_c1",
        "detail": "React fallback remains available when C1 is unavailable.",
    }
    try:
        c1_response = build_thesys_c1_client(settings.thesys).render_semantic_ui(
            job_status=job_status,
            semantic_ui=semantic_ui_response.semantic_ui,
        )
    except ThesysC1ConfigurationError as exc:
        return JobThesysC1Response(
            job_id=semantic_ui_response.job_id,
            status=ThesysC1RenderStatus.UNAVAILABLE,
            output_origin={"mode": "fallback", **origin_kwargs},
            semantic_ui=semantic_ui_response.semantic_ui,
            c1_response=None,
            model=settings.thesys.model,
            reason=str(exc),
            fallback_required=True,
        )
    except ThesysC1Error as exc:
        return JobThesysC1Response(
            job_id=semantic_ui_response.job_id,
            status=ThesysC1RenderStatus.ERROR,
            output_origin={"mode": "fallback", **origin_kwargs},
            semantic_ui=semantic_ui_response.semantic_ui,
            c1_response=None,
            model=settings.thesys.model,
            reason=str(exc),
            fallback_required=True,
        )
    if c1_response is None:
        return JobThesysC1Response(
            job_id=semantic_ui_response.job_id,
            status=ThesysC1RenderStatus.ERROR,
            output_origin={"mode": "fallback", **origin_kwargs},
            semantic_ui=semantic_ui_response.semantic_ui,
            c1_response=None,
            model=settings.thesys.model,
            reason="Thesys C1 returned no renderable response.",
            fallback_required=True,
        )
    return JobThesysC1Response(
        job_id=semantic_ui_response.job_id,
        status=ThesysC1RenderStatus.RENDERED,
        output_origin={"mode": "live_llm", **origin_kwargs},
        semantic_ui=semantic_ui_response.semantic_ui,
        c1_response=c1_response,
        model=settings.thesys.model,
        reason=None,
        fallback_required=False,
    )
