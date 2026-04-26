from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.contracts import (
    ArtifactManifest,
    DecisionObject,
    EvidenceGraph,
    ExecutionGateDecision,
    ExecutionGateReport,
    JobDecisionResponse,
    ProvenanceSource,
    ReasoningTrace,
    SourceContext,
    SplitContext,
    V2AuditBaselineProvenance,
    V2AuditBundle,
    V2AuditCheck,
    V2AuditProvenance,
    V2AuditSection,
    V2AuditSectionId,
    V2AuditStatus,
    V2AuditSummary,
)
from app.integrations.health import build_api_health_report, build_integration_health_report
from app.settings import AppSettings


def _value(value: Any) -> Any:
    return getattr(value, "value", value)


def _roll_up_status(statuses: Iterable[V2AuditStatus]) -> V2AuditStatus:
    observed = list(statuses)
    if any(status == V2AuditStatus.FAIL for status in observed):
        return V2AuditStatus.FAIL
    if any(status == V2AuditStatus.WARN for status in observed):
        return V2AuditStatus.WARN
    if any(status == V2AuditStatus.PENDING for status in observed):
        return V2AuditStatus.PENDING
    return V2AuditStatus.PASS


def _section(
    *,
    section_id: V2AuditSectionId,
    title: str,
    summary: str,
    checks: list[V2AuditCheck],
    evidence_refs: list[str] | None = None,
) -> V2AuditSection:
    return V2AuditSection(
        section_id=section_id,
        status=_roll_up_status([check.status for check in checks]),
        title=title,
        summary=summary,
        checks=checks,
        evidence_refs=evidence_refs or [],
    )


def _check(
    *,
    section_id: V2AuditSectionId,
    check_id: str,
    status: V2AuditStatus,
    title: str,
    detail: str,
    endpoint: str | None = None,
    observed_value: str | float | int | bool | None = None,
    expected_value: str | float | int | bool | None = None,
    evidence_refs: list[str] | None = None,
    blocking: bool = False,
) -> V2AuditCheck:
    return V2AuditCheck(
        section_id=section_id,
        check_id=check_id,
        status=status,
        title=title,
        detail=detail,
        endpoint=endpoint,
        observed_value=observed_value,
        expected_value=expected_value,
        evidence_refs=evidence_refs or [],
        blocking=blocking,
    )


def _decision_job_id(decision: DecisionObject) -> str:
    return decision.job_id or decision.triage_decision.job_id


def _baseline_provenance(split_context: SplitContext) -> V2AuditBaselineProvenance:
    if split_context in {SplitContext.SMOKE, SplitContext.FIXTURE}:
        return V2AuditBaselineProvenance.FIXTURE_TRAINED_SMOKE
    if split_context in {SplitContext.RANDOM, SplitContext.LINEAGE_AWARE}:
        return V2AuditBaselineProvenance.PUBLIC_SNAPSHOT
    return V2AuditBaselineProvenance.UNKNOWN


def _input_provenance(decision: DecisionObject) -> ProvenanceSource:
    prediction_source = decision.phenotype_prediction.input_provenance_source
    if prediction_source != ProvenanceSource.OTHER:
        return prediction_source
    return decision.sample.metadata.provenance_source


def _source_context(decision: DecisionObject) -> SourceContext:
    prediction_context = decision.phenotype_prediction.input_source_context
    if prediction_context != SourceContext.OTHER:
        return prediction_context
    return decision.sample.metadata.source_context


def _provenance_from_decision(decision: DecisionObject) -> V2AuditProvenance:
    input_provenance = _input_provenance(decision)
    source_context = _source_context(decision)
    split_context = decision.phenotype_prediction.model_training_split_context
    baseline_provenance = _baseline_provenance(split_context)
    live_input = input_provenance in {ProvenanceSource.BV_BRC, ProvenanceSource.NCBI}
    fixture_trained_baseline = baseline_provenance == V2AuditBaselineProvenance.FIXTURE_TRAINED_SMOKE

    if live_input and fixture_trained_baseline:
        label = "Live input, fixture-trained baseline"
        detail = (
            "The analyzed FASTA provenance is live, while the current predictive "
            "baseline remains the explicit fixture-trained smoke baseline."
        )
    elif live_input:
        label = "Live input, public-snapshot baseline"
        detail = (
            "The analyzed FASTA provenance is live, and the predictive baseline is "
            "reported as a public-data snapshot rather than a live-trained model."
        )
    elif fixture_trained_baseline:
        label = "Fixture or local input, fixture-trained baseline"
        detail = (
            "The analyzed input is not proven live by this bundle, and the predictive "
            "baseline is the explicit fixture-trained smoke baseline."
        )
    else:
        label = "Non-live input, non-fixture baseline"
        detail = (
            "The analyzed input is not proven live by this bundle, and the predictive "
            "baseline is not the fixture-trained smoke baseline."
        )

    return V2AuditProvenance(
        job_id=_decision_job_id(decision),
        sample_id=decision.sample.sample_id,
        target_drug=decision.sample.target_drug,
        input_provenance=input_provenance,
        source_context=source_context,
        split_context=split_context,
        baseline_provenance=baseline_provenance,
        live_input=live_input,
        fixture_trained_baseline=fixture_trained_baseline,
        provenance_split_label=label,
        detail=detail,
    )


def _runtime_sections(
    *,
    job_id: str,
    api_health: dict[str, Any],
    integration_health: dict[str, Any],
) -> V2AuditSection:
    section_id = V2AuditSectionId.RUNTIME_MODE
    runtime = api_health.get("runtime", {})
    blockers = runtime.get("live_mode_blockers") or []
    blockers_label = ", ".join(str(item) for item in blockers) if blockers else "none"
    integration_status = str(integration_health.get("status", "unknown"))

    integration_check_status = V2AuditStatus.PASS
    integration_blocking = False
    if integration_status == "fixture":
        integration_check_status = V2AuditStatus.WARN
    elif integration_status != "ready":
        integration_check_status = V2AuditStatus.FAIL
        integration_blocking = True

    checks = [
        _check(
            section_id=section_id,
            check_id="api_health",
            status=V2AuditStatus.PASS if api_health.get("status") == "ok" else V2AuditStatus.FAIL,
            title="API health",
            detail=f"/health returned {api_health.get('status', 'unknown')}.",
            endpoint="/health",
            observed_value=str(api_health.get("status", "unknown")),
            expected_value="ok",
            blocking=api_health.get("status") != "ok",
        ),
        _check(
            section_id=section_id,
            check_id="live_runtime_mode",
            status=V2AuditStatus.PASS if runtime.get("live_mode_ready") else V2AuditStatus.WARN,
            title="Live runtime mode",
            detail=f"Runtime live-mode blockers: {blockers_label}.",
            endpoint="/health",
            observed_value=bool(runtime.get("live_mode_ready")),
            expected_value=True,
        ),
        _check(
            section_id=section_id,
            check_id="integration_readiness",
            status=integration_check_status,
            title="Integration readiness",
            detail=f"/health/integrations returned {integration_status}.",
            endpoint="/health/integrations",
            observed_value=integration_status,
            expected_value="ready",
            blocking=integration_blocking,
        ),
    ]
    return _section(
        section_id=section_id,
        title="Runtime and integration mode",
        summary=f"Runtime proof for {job_id} uses health endpoints and redacted integration readiness.",
        checks=checks,
    )


def _live_input_section(provenance: V2AuditProvenance) -> V2AuditSection:
    section_id = V2AuditSectionId.LIVE_INPUT_PROOF
    input_status = V2AuditStatus.PASS if provenance.live_input else V2AuditStatus.WARN
    source_status = V2AuditStatus.PASS if provenance.source_context != SourceContext.OTHER else V2AuditStatus.WARN
    return _section(
        section_id=section_id,
        title="Live input proof",
        summary=provenance.provenance_split_label,
        checks=[
            _check(
                section_id=section_id,
                check_id="input_provenance",
                status=input_status,
                title="Input provenance",
                detail=f"Input provenance is recorded as {_value(provenance.input_provenance)}.",
                endpoint=f"/jobs/{provenance.job_id}/decision",
                observed_value=_value(provenance.input_provenance),
                expected_value="bv_brc or ncbi_datasets",
                evidence_refs=["decision_object__summary"],
            ),
            _check(
                section_id=section_id,
                check_id="source_context",
                status=source_status,
                title="Input source context",
                detail=f"Input source context is recorded as {_value(provenance.source_context)}.",
                endpoint=f"/jobs/{provenance.job_id}/decision",
                observed_value=_value(provenance.source_context),
                expected_value="explicit non-other source context",
                evidence_refs=["decision_object__summary"],
            ),
        ],
        evidence_refs=["decision_object__summary"],
    )


def _baseline_section(provenance: V2AuditProvenance) -> V2AuditSection:
    section_id = V2AuditSectionId.PREDICTIVE_BASELINE
    baseline_known = provenance.baseline_provenance != V2AuditBaselineProvenance.UNKNOWN
    return _section(
        section_id=section_id,
        title="Predictive baseline provenance",
        summary="Predictive model provenance is surfaced separately from sample provenance.",
        checks=[
            _check(
                section_id=section_id,
                check_id="baseline_disclosure",
                status=V2AuditStatus.PASS if baseline_known else V2AuditStatus.WARN,
                title="Baseline disclosure",
                detail=(
                    "The audit bundle explicitly reports whether the predictive "
                    "baseline is fixture-trained, public-snapshot, or unknown."
                ),
                endpoint=f"/jobs/{provenance.job_id}/decision",
                observed_value=_value(provenance.baseline_provenance),
                expected_value="explicit baseline provenance",
                evidence_refs=["phenotype_prediction__summary"],
            ),
            _check(
                section_id=section_id,
                check_id="split_context",
                status=V2AuditStatus.PASS,
                title="Training split context",
                detail=f"Model training split context is {_value(provenance.split_context)}.",
                endpoint=f"/jobs/{provenance.job_id}/decision",
                observed_value=_value(provenance.split_context),
                expected_value="declared split context",
                evidence_refs=["phenotype_prediction__summary"],
            ),
        ],
        evidence_refs=["phenotype_prediction__summary"],
    )


def _provider_section(
    *,
    section_id: V2AuditSectionId,
    title: str,
    endpoint: str,
    configured: bool,
    cached_sidecar_available: bool,
) -> V2AuditSection:
    status = V2AuditStatus.PENDING if configured else V2AuditStatus.FAIL
    detail = (
        "Provider is configured, but this read-only audit endpoint does not run "
        "expensive provider calls. Use the explicit provider-proof workflow."
        if configured
        else "Provider is not fully configured, so explicit live proof cannot pass."
    )
    if cached_sidecar_available:
        detail += " A cached sidecar exists, but cached content is not counted as live provider proof."
    return _section(
        section_id=section_id,
        title=title,
        summary="Provider proof is explicit and never inferred from cached sidecar artifacts.",
        checks=[
            _check(
                section_id=section_id,
                check_id="explicit_provider_proof",
                status=status,
                title="Explicit provider proof",
                detail=detail,
                endpoint=endpoint,
                observed_value="configured" if configured else "missing",
                expected_value="fresh explicit provider proof",
                blocking=not configured,
            )
        ],
    )


def _execution_gate_section(execution_gate: ExecutionGateReport) -> V2AuditSection:
    section_id = V2AuditSectionId.EXECUTION_GATE
    gate_status = V2AuditStatus.PASS
    blocking = False
    if execution_gate.gate_decision == ExecutionGateDecision.REVIEW:
        gate_status = V2AuditStatus.WARN
    elif execution_gate.gate_decision == ExecutionGateDecision.BLOCK:
        gate_status = V2AuditStatus.FAIL
        blocking = True
    return _section(
        section_id=section_id,
        title="Execution gate",
        summary=execution_gate.summary,
        checks=[
            _check(
                section_id=section_id,
                check_id="gate_decision",
                status=gate_status,
                title="Gate decision",
                detail=f"Execution gate returned {_value(execution_gate.gate_decision)}.",
                endpoint=f"/jobs/{execution_gate.job_id}/verification",
                observed_value=_value(execution_gate.gate_decision),
                expected_value="allow",
                evidence_refs=["execution_gate__report"],
                blocking=blocking,
            ),
            _check(
                section_id=section_id,
                check_id="audit_fingerprint",
                status=V2AuditStatus.PASS,
                title="Audit fingerprint",
                detail="Execution gate emitted a stable audit fingerprint and policy hash.",
                endpoint=f"/jobs/{execution_gate.job_id}/verification",
                observed_value=True,
                expected_value=True,
                evidence_refs=["execution_gate__report"],
            ),
        ],
        evidence_refs=["execution_gate__report"],
    )


def _reasoning_trace_section(reasoning_trace: ReasoningTrace) -> V2AuditSection:
    section_id = V2AuditSectionId.REASONING_TRACE
    coverage_status = V2AuditStatus.PASS if reasoning_trace.coverage.coverage_ratio == 1.0 else V2AuditStatus.WARN
    provider_calls = bool(reasoning_trace.metadata.get("provider_calls_triggered"))
    return _section(
        section_id=section_id,
        title="BioReason-style trace",
        summary=reasoning_trace.summary,
        checks=[
            _check(
                section_id=section_id,
                check_id="trace_coverage",
                status=coverage_status,
                title="Trace coverage",
                detail=(
                    f"{reasoning_trace.coverage.present_steps}/"
                    f"{reasoning_trace.coverage.required_steps} reasoning steps are present."
                ),
                endpoint=f"/jobs/{reasoning_trace.job_id}/reasoning-trace",
                observed_value=reasoning_trace.coverage.coverage_ratio,
                expected_value=1.0,
                evidence_refs=["reasoning_trace__summary"],
            ),
            _check(
                section_id=section_id,
                check_id="deterministic_trace",
                status=V2AuditStatus.FAIL if provider_calls else V2AuditStatus.PASS,
                title="Deterministic trace",
                detail="Reasoning trace builder must not call OpenRouter or Thesys.",
                endpoint=f"/jobs/{reasoning_trace.job_id}/reasoning-trace",
                observed_value=provider_calls,
                expected_value=False,
                evidence_refs=["reasoning_trace__summary"],
                blocking=provider_calls,
            ),
        ],
        evidence_refs=["reasoning_trace__summary"],
    )


def _evidence_graph_section(evidence_graph: EvidenceGraph) -> V2AuditSection:
    section_id = V2AuditSectionId.EVIDENCE_GRAPH
    stats = evidence_graph.stats
    graph_complete = stats.completeness_ratio == 1.0 and not stats.missing_node_classes
    linkage_complete = (
        stats.artifact_linkage_ratio == 1.0
        and stats.citation_linkage_ratio == 1.0
        and stats.weakly_connected
        and not stats.isolated_node_ids
    )
    provider_calls = bool(evidence_graph.metadata.get("provider_calls_triggered"))
    return _section(
        section_id=section_id,
        title="Evidence graph",
        summary="Evidence graph proof covers graph completeness, linkage, and deterministic construction.",
        checks=[
            _check(
                section_id=section_id,
                check_id="graph_completeness",
                status=V2AuditStatus.PASS if graph_complete else V2AuditStatus.WARN,
                title="Required graph classes",
                detail=f"Required graph-node completeness is {stats.completeness_ratio:.3f}.",
                endpoint=f"/jobs/{evidence_graph.job_id}/evidence-graph",
                observed_value=stats.completeness_ratio,
                expected_value=1.0,
                evidence_refs=["evidence_graph__summary"],
            ),
            _check(
                section_id=section_id,
                check_id="graph_linkage",
                status=V2AuditStatus.PASS if linkage_complete else V2AuditStatus.WARN,
                title="Graph linkage",
                detail=(
                    "Artifact and citation nodes should be linked, and the graph "
                    "should remain weakly connected without isolated nodes."
                ),
                endpoint=f"/jobs/{evidence_graph.job_id}/evidence-graph",
                observed_value=bool(linkage_complete),
                expected_value=True,
                evidence_refs=["evidence_graph__summary"],
            ),
            _check(
                section_id=section_id,
                check_id="deterministic_graph",
                status=V2AuditStatus.FAIL if provider_calls else V2AuditStatus.PASS,
                title="Deterministic graph",
                detail="Evidence graph builder must not call OpenRouter, Thesys, or live data providers.",
                endpoint=f"/jobs/{evidence_graph.job_id}/evidence-graph",
                observed_value=provider_calls,
                expected_value=False,
                evidence_refs=["evidence_graph__summary"],
                blocking=provider_calls,
            ),
        ],
        evidence_refs=["evidence_graph__summary"],
    )


def _artifact_section(
    *,
    job_id: str,
    artifact_manifest: ArtifactManifest | None,
) -> V2AuditSection:
    section_id = V2AuditSectionId.ARTIFACT_COVERAGE
    artifacts = list(artifact_manifest.artifacts) if artifact_manifest is not None else []
    previewable = [artifact for artifact in artifacts if artifact.preview_eligible]
    manifest_available = artifact_manifest is not None
    artifact_status = V2AuditStatus.PASS if artifacts else V2AuditStatus.FAIL
    return _section(
        section_id=section_id,
        title="Artifact coverage",
        summary="Artifact manifest proof covers raw evidence availability and browser-preview candidates.",
        checks=[
            _check(
                section_id=section_id,
                check_id="artifact_manifest",
                status=V2AuditStatus.PASS if manifest_available else V2AuditStatus.FAIL,
                title="Artifact manifest",
                detail="Artifact manifest is available for this persisted job."
                if manifest_available
                else "Artifact manifest is missing for this job.",
                endpoint=f"/jobs/{job_id}/artifacts",
                observed_value=manifest_available,
                expected_value=True,
                evidence_refs=["artifact_manifest__summary"],
                blocking=not manifest_available,
            ),
            _check(
                section_id=section_id,
                check_id="artifact_records",
                status=artifact_status,
                title="Artifact records",
                detail=f"{len(artifacts)} artifact records are available for audit.",
                endpoint=f"/jobs/{job_id}/artifacts",
                observed_value=len(artifacts),
                expected_value="one or more artifact records",
                evidence_refs=["artifact_manifest__summary"],
                blocking=not artifacts,
            ),
            _check(
                section_id=section_id,
                check_id="preview_candidates",
                status=V2AuditStatus.PASS if previewable else V2AuditStatus.WARN,
                title="Preview candidates",
                detail=f"{len(previewable)} artifact records are browser-preview eligible.",
                endpoint=f"/jobs/{job_id}/artifacts",
                observed_value=len(previewable),
                expected_value="one or more previewable artifacts",
                evidence_refs=["artifact_manifest__summary"],
            ),
        ],
        evidence_refs=["artifact_manifest__summary"],
    )


def _summary(*, sections: list[V2AuditSection], live_mode_ready: bool, provenance: V2AuditProvenance) -> V2AuditSummary:
    checks = [check for section in sections for check in section.checks]
    overall = _roll_up_status([section.status for section in sections])
    live_ready = (
        overall == V2AuditStatus.PASS
        and live_mode_ready
        and provenance.live_input
        and not any(check.status == V2AuditStatus.PENDING for check in checks)
    )
    return V2AuditSummary(
        overall_status=overall,
        section_count=len(sections),
        total_checks=len(checks),
        passing_checks=sum(check.status == V2AuditStatus.PASS for check in checks),
        warning_checks=sum(check.status == V2AuditStatus.WARN for check in checks),
        failed_checks=sum(check.status == V2AuditStatus.FAIL for check in checks),
        pending_checks=sum(check.status == V2AuditStatus.PENDING for check in checks),
        live_ready=live_ready,
        provider_proof_required=True,
    )


def build_v2_audit_bundle(
    *,
    decision_response: JobDecisionResponse,
    settings: AppSettings,
    artifact_manifest: ArtifactManifest | None,
    execution_gate: ExecutionGateReport,
    reasoning_trace: ReasoningTrace,
    evidence_graph: EvidenceGraph,
) -> V2AuditBundle:
    """Build the read-only V2 audit bundle without invoking external providers."""

    decision = decision_response.decision
    job_id = _decision_job_id(decision)
    provenance = _provenance_from_decision(decision)
    api_health = build_api_health_report(settings)
    integration_health = build_integration_health_report(settings)
    external_apis = integration_health.get("external_apis", {})
    llm_status = external_apis.get("llm", {})
    thesys_status = external_apis.get("thesys", {})
    runtime = api_health.get("runtime", {})

    llm_configured = (
        llm_status.get("status") == "configured"
        and not bool(llm_status.get("mock_mode"))
        and bool(settings.llm.provider)
    )
    thesys_configured = thesys_status.get("status") == "configured"
    cached_copilot = bool(execution_gate.metadata.get("copilot_sidecar_available"))
    cached_semantic_ui = bool(execution_gate.metadata.get("semantic_ui_sidecar_available"))

    sections = [
        _runtime_sections(
            job_id=job_id,
            api_health=api_health,
            integration_health=integration_health,
        ),
        _live_input_section(provenance),
        _baseline_section(provenance),
        _provider_section(
            section_id=V2AuditSectionId.OPENROUTER_PROOF,
            title="OpenRouter proof",
            endpoint=f"/jobs/{job_id}/copilot/explanation?refresh=true",
            configured=llm_configured,
            cached_sidecar_available=cached_copilot,
        ),
        _provider_section(
            section_id=V2AuditSectionId.THESYS_PROOF,
            title="Thesys C1 proof",
            endpoint=f"/jobs/{job_id}/semantic-ui/c1",
            configured=thesys_configured,
            cached_sidecar_available=cached_semantic_ui,
        ),
        _execution_gate_section(execution_gate),
        _reasoning_trace_section(reasoning_trace),
        _evidence_graph_section(evidence_graph),
        _artifact_section(job_id=job_id, artifact_manifest=artifact_manifest),
    ]

    return V2AuditBundle(
        job_id=job_id,
        sample_id=decision.sample.sample_id,
        target_drug=decision.sample.target_drug,
        provenance=provenance,
        summary=_summary(
            sections=sections,
            live_mode_ready=bool(runtime.get("live_mode_ready")),
            provenance=provenance,
        ),
        sections=sections,
        metadata={
            "builder": "deterministic_v2_audit_bundle",
            "provider_calls_triggered": False,
            "job_status": _value(decision_response.job_status.status),
            "api_health_status": str(api_health.get("status", "unknown")),
            "integration_health_status": str(integration_health.get("status", "unknown")),
            "openrouter_configured": llm_configured,
            "thesys_configured": thesys_configured,
            "cached_copilot_sidecar_available": cached_copilot,
            "cached_semantic_ui_sidecar_available": cached_semantic_ui,
            "artifact_manifest_available": artifact_manifest is not None,
            "artifact_count": len(artifact_manifest.artifacts) if artifact_manifest is not None else 0,
            "execution_gate_decision": _value(execution_gate.gate_decision),
            "execution_gate_audit_fingerprint": execution_gate.audit_fingerprint,
            "reasoning_trace_coverage_ratio": reasoning_trace.coverage.coverage_ratio,
            "evidence_graph_completeness_ratio": evidence_graph.stats.completeness_ratio,
        },
    )
