from __future__ import annotations

from dataclasses import dataclass

from app.contracts import (
    ActionabilityFeatures,
    ArtifactKind,
    ArtifactManifest,
    ArtifactPreviewResponse,
    ArtifactRecord,
    AssemblyQC,
    CalibrationStatus,
    CopilotAnswerBlock,
    CopilotOutputMode,
    CopilotOutputOrigin,
    CopilotResponse,
    DecisionCardBlock,
    DecisionObject,
    EvidenceTableBlock,
    EvidenceTableRow,
    JobCopilotResponse,
    JobDecisionResponse,
    JobSemanticUIResponse,
    JobState,
    JobStatus,
    MechanismSupportLevel,
    MetricDatum,
    NoveltyAssessment,
    NoveltyBucket,
    PhenotypePrediction,
    PredictedPhenotype,
    ProvenanceSource,
    QCStatus,
    QueueBlock,
    QueueItem,
    RationaleCode,
    RiskChartBlock,
    RiskChartPoint,
    SafetyProfileAxis,
    SafetyProfileBlock,
    SampleInput,
    SampleMetadata,
    SemanticUIObject,
    SeverityLevel,
    SourceContext,
    SplitContext,
    TriageDecision,
    TriageOutcome,
    MechanisticEvidence,
)


@dataclass(frozen=True)
class DemoJobSpec:
    job_id: str
    sample_id: str
    organism_hint: str
    organism_label: str
    target_drug: str
    triage: TriageOutcome
    severity: SeverityLevel
    status: JobState
    queue_priority: int
    headline: str
    rationale_codes: tuple[RationaleCode, ...]
    probability: float
    actionability_score: float
    qc_risk: float
    novelty_risk: float
    novelty_score: float
    novelty_bucket: NoveltyBucket
    mechanism_gene: str
    mechanism_status: str
    mechanism_support: MechanismSupportLevel
    recommended_next_step: str


_DEMO_JOB_SPECS: tuple[DemoJobSpec, ...] = (
    DemoJobSpec(
        job_id="job_demo_act_001",
        sample_id="sample_demo_act_001",
        organism_hint="e_coli",
        organism_label="Escherichia coli ST-131",
        target_drug="tetracycline",
        triage=TriageOutcome.ACT,
        severity=SeverityLevel.CRITICAL,
        status=JobState.COMPLETED,
        queue_priority=0,
        headline="Act on high-confidence resistant E. coli case",
        rationale_codes=(
            RationaleCode.ACTIONABILITY_THRESHOLD_MET,
            RationaleCode.SUPPORTED_MECHANISM_PRESENT,
            RationaleCode.CONCORDANT_SIGNAL_PRESENT,
        ),
        probability=0.94,
        actionability_score=0.91,
        qc_risk=0.08,
        novelty_risk=0.31,
        novelty_score=0.28,
        novelty_bucket=NoveltyBucket.KNOWN,
        mechanism_gene="tetA",
        mechanism_status="supported",
        mechanism_support=MechanismSupportLevel.SUPPORTED,
        recommended_next_step="prioritize analyst review and confirm resistance evidence in the report queue",
    ),
    DemoJobSpec(
        job_id="job_demo_review_001",
        sample_id="sample_demo_review_001",
        organism_hint="e_coli",
        organism_label="Escherichia coli cassette variant",
        target_drug="tetracycline",
        triage=TriageOutcome.REVIEW,
        severity=SeverityLevel.HIGH,
        status=JobState.DEGRADED,
        queue_priority=10,
        headline="Review elevated-novelty case before action",
        rationale_codes=(
            RationaleCode.HIGH_LINEAGE_NOVELTY,
            RationaleCode.MANUAL_CONFIRMATION_REQUIRED,
        ),
        probability=0.67,
        actionability_score=0.64,
        qc_risk=0.22,
        novelty_risk=0.77,
        novelty_score=0.74,
        novelty_bucket=NoveltyBucket.HIGH,
        mechanism_gene="tetM",
        mechanism_status="ambiguous",
        mechanism_support=MechanismSupportLevel.PARTIAL,
        recommended_next_step="review novelty and assembly context before escalating the case",
    ),
    DemoJobSpec(
        job_id="job_demo_defer_001",
        sample_id="sample_demo_defer_001",
        organism_hint="s_aureus",
        organism_label="Staphylococcus aureus surveillance isolate",
        target_drug="tetracycline",
        triage=TriageOutcome.DEFER_TO_LAB,
        severity=SeverityLevel.MEDIUM,
        status=JobState.COMPLETED,
        queue_priority=20,
        headline="Defer low-support case for lab confirmation",
        rationale_codes=(
            RationaleCode.ACTIONABILITY_THRESHOLD_NOT_MET,
            RationaleCode.NO_SUPPORTED_MECHANISM,
            RationaleCode.MANUAL_CONFIRMATION_REQUIRED,
        ),
        probability=0.39,
        actionability_score=0.36,
        qc_risk=0.18,
        novelty_risk=0.43,
        novelty_score=0.41,
        novelty_bucket=NoveltyBucket.ELEVATED,
        mechanism_gene="tetK",
        mechanism_status="screen_only",
        mechanism_support=MechanismSupportLevel.SCREEN_ONLY,
        recommended_next_step="defer operational action until laboratory confirmation is available",
    ),
)


def demo_queue_items() -> list[QueueItem]:
    return [_build_queue_item(spec) for spec in _DEMO_JOB_SPECS]


def get_demo_job_status(job_id: str) -> JobStatus | None:
    spec = _demo_spec(job_id)
    if spec is None:
        return None
    return _build_job_status(spec)


def get_demo_job_decision_response(job_id: str) -> JobDecisionResponse | None:
    spec = _demo_spec(job_id)
    if spec is None:
        return None
    return JobDecisionResponse(
        job_status=_build_job_status(spec),
        decision=_build_decision(spec),
    )


def get_demo_artifact_manifest(job_id: str) -> ArtifactManifest | None:
    spec = _demo_spec(job_id)
    if spec is None:
        return None
    return _build_artifact_manifest(spec)


def get_demo_artifact_preview(
    job_id: str,
    artifact_id: str,
    *,
    max_bytes: int,
) -> ArtifactPreviewResponse | None:
    spec = _demo_spec(job_id)
    if spec is None:
        return None
    normalized_artifact_id = artifact_id.strip().lower()
    if normalized_artifact_id == f"{spec.job_id}_decision_json":
        media_type = "application/json"
        content = _build_decision(spec).model_dump_json(indent=2)
    elif normalized_artifact_id == f"{spec.job_id}_mechanism_table":
        media_type = "text/tab-separated-values"
        content = (
            "feature\tsupport\tinterpretation\n"
            f"{spec.mechanism_gene}\t{spec.mechanism_support.value}\t{spec.mechanism_status}\n"
        )
    elif normalized_artifact_id == f"{spec.job_id}_semantic_ui_json":
        media_type = "application/json"
        content = _build_semantic_ui(spec).model_dump_json(indent=2)
    else:
        return None
    encoded = content.encode("utf-8")
    truncated = len(encoded) > max_bytes
    if truncated:
        content = encoded[:max_bytes].decode("utf-8", errors="replace")
    return ArtifactPreviewResponse(
        job_id=spec.job_id,
        artifact_id=normalized_artifact_id,
        media_type=media_type,
        encoding="utf-8",
        content=content,
        truncated=truncated,
        size_bytes=len(encoded),
    )


def get_demo_copilot_response(job_id: str, *, mode: str, question: str | None = None) -> JobCopilotResponse | None:
    spec = _demo_spec(job_id)
    if spec is None:
        return None
    return JobCopilotResponse(
        job_status=_build_job_status(spec),
        output_origin=_demo_output_origin(mode),
        copilot=_build_copilot(spec, mode=mode, question=question),
    )


def get_demo_semantic_ui_response(job_id: str) -> JobSemanticUIResponse | None:
    spec = _demo_spec(job_id)
    if spec is None:
        return None
    return JobSemanticUIResponse(
        job_id=spec.job_id,
        output_origin=_demo_output_origin("semantic_ui"),
        semantic_ui=_build_semantic_ui(spec),
    )


def _demo_spec(job_id: str) -> DemoJobSpec | None:
    normalized = job_id.strip().lower()
    return next((spec for spec in _DEMO_JOB_SPECS if spec.job_id == normalized), None)


def _build_sample(spec: DemoJobSpec) -> SampleInput:
    return SampleInput(
        sample_id=spec.sample_id,
        organism_hint=spec.organism_hint,
        target_drug=spec.target_drug,
        fasta_uri=f"demo://phase8/{spec.sample_id}.fasta",
        metadata=SampleMetadata(
            accession=f"DEMO-{spec.sample_id.upper()}",
            source_context=SourceContext.FIXTURE,
            country="USA",
            provenance_source=ProvenanceSource.FIXTURE,
        ),
    )


def _build_job_status(spec: DemoJobSpec) -> JobStatus:
    return JobStatus(
        job_id=spec.job_id,
        sample_id=spec.sample_id,
        target_drug=spec.target_drug,
        status=spec.status,
        current_step="demo_seeded_case_ready",
        warnings=["fixture_backed_demo_case"],
    )


def _build_queue_item(spec: DemoJobSpec) -> QueueItem:
    return QueueItem(
        job_id=spec.job_id,
        sample_id=spec.sample_id,
        target_drug=spec.target_drug,
        triage=spec.triage,
        severity=spec.severity,
        status=spec.status,
        queue_priority=spec.queue_priority,
        headline=spec.headline,
        rationale_codes=list(spec.rationale_codes),
    )


def _build_decision(spec: DemoJobSpec) -> DecisionObject:
    sample = _build_sample(spec)
    return DecisionObject(
        job_id=spec.job_id,
        sample=sample,
        assembly_qc=AssemblyQC(
            job_id=spec.job_id,
            sample_id=spec.sample_id,
            target_drug=spec.target_drug,
            file_valid=True,
            sequence_count=48,
            total_bases=4_928_412,
            ambiguous_base_fraction=0.002,
            organism_consistency="match",
            qc_status=QCStatus.PASS if spec.qc_risk < 0.2 else QCStatus.WARN,
            warnings=[] if spec.qc_risk < 0.2 else ["assembly review recommended"],
        ),
        mechanistic_evidence=[
            MechanisticEvidence(
                job_id=spec.job_id,
                sample_id=spec.sample_id,
                target_drug=spec.target_drug,
                gene_symbol=spec.mechanism_gene,
                mechanism_class="tetracycline efflux or ribosomal protection",
                drug_association=[spec.target_drug],
                support_level=spec.mechanism_support,
                interpretation=f"{spec.mechanism_gene} is {spec.mechanism_status} for {spec.target_drug}.",
                raw_artifact_id=f"{spec.job_id}_mechanism_table",
            )
        ],
        phenotype_prediction=PhenotypePrediction(
            job_id=spec.job_id,
            sample_id=spec.sample_id,
            target_drug=spec.target_drug,
            predicted_phenotype=(
                PredictedPhenotype.RESISTANT
                if spec.probability >= 0.5
                else PredictedPhenotype.SUSCEPTIBLE
            ),
            probability=spec.probability,
            calibration_status=CalibrationStatus.UNCALIBRATED,
            uncertainty_score=round(1.0 - abs(spec.probability - 0.5) * 2.0, 3),
            feature_set_version="phase8_demo_features",
            model_version="phase8_demo_baseline",
            model_training_split_context=SplitContext.FIXTURE,
            input_source_context=SourceContext.FIXTURE,
            input_provenance_source=ProvenanceSource.FIXTURE,
        ),
        novelty_assessment=NoveltyAssessment(
            job_id=spec.job_id,
            sample_id=spec.sample_id,
            target_drug=spec.target_drug,
            reference_snapshot_id="phase8_demo_reference_snapshot",
            nearest_neighbor_id=f"{spec.organism_hint}_reference_neighbor",
            nearest_neighbor_distance=round(spec.novelty_score * 0.035, 4),
            novelty_score=spec.novelty_score,
            novelty_percentile=round(spec.novelty_score * 100, 1),
            novelty_bucket=spec.novelty_bucket,
        ),
        actionability_features=ActionabilityFeatures(
            job_id=spec.job_id,
            sample_id=spec.sample_id,
            target_drug=spec.target_drug,
            actionability_score=spec.actionability_score,
            mechanism_concordance=spec.mechanism_support == MechanismSupportLevel.SUPPORTED,
            prediction_entropy=round(1.0 - abs(spec.probability - 0.5) * 2.0, 3),
            qc_risk=spec.qc_risk,
            novelty_risk=spec.novelty_risk,
            metadata_completeness=0.92,
            threshold_version="phase8_demo_thresholds",
            warnings=[] if spec.triage == TriageOutcome.ACT else ["analyst confirmation recommended"],
        ),
        triage_decision=TriageDecision(
            job_id=spec.job_id,
            sample_id=spec.sample_id,
            target_drug=spec.target_drug,
            triage=spec.triage,
            severity=spec.severity,
            recommended_next_step=spec.recommended_next_step,
            threshold_version="phase8_demo_thresholds",
            rationale_codes=list(spec.rationale_codes),
        ),
        rationale_codes=list(spec.rationale_codes),
        artifact_manifest_id=f"{spec.job_id}_artifact_manifest",
        provenance_notes=["fixture_backed_phase8_demo_case"],
    )


def _build_artifact_manifest(spec: DemoJobSpec) -> ArtifactManifest:
    artifacts = [
        ArtifactRecord(
            artifact_id=f"{spec.job_id}_decision_json",
            job_id=spec.job_id,
            sample_id=spec.sample_id,
            target_drug=spec.target_drug,
            kind=ArtifactKind.DECISION_OBJECT,
            path=f"artifacts/demo/{spec.job_id}/decision.json",
            media_type="application/json",
            generated_by="phase8_demo_seed",
        ),
        ArtifactRecord(
            artifact_id=f"{spec.job_id}_mechanism_table",
            job_id=spec.job_id,
            sample_id=spec.sample_id,
            target_drug=spec.target_drug,
            kind=ArtifactKind.MECHANISTIC_EVIDENCE,
            path=f"artifacts/demo/{spec.job_id}/mechanisms.tsv",
            media_type="text/tab-separated-values",
            generated_by="phase8_demo_seed",
        ),
        ArtifactRecord(
            artifact_id=f"{spec.job_id}_semantic_ui_json",
            job_id=spec.job_id,
            sample_id=spec.sample_id,
            target_drug=spec.target_drug,
            kind=ArtifactKind.SEMANTIC_UI,
            path=f"artifacts/demo/{spec.job_id}/semantic_ui.json",
            media_type="application/json",
            generated_by="phase8_demo_seed",
        ),
    ]
    return ArtifactManifest(
        job_id=spec.job_id,
        sample_id=spec.sample_id,
        target_drug=spec.target_drug,
        artifact_root="artifacts/demo",
        artifacts=artifacts,
    )


def _build_semantic_ui(spec: DemoJobSpec) -> SemanticUIObject:
    return SemanticUIObject(
        decision_card=DecisionCardBlock(
            title=f"{spec.organism_label} - {spec.target_drug}",
            triage_decision=spec.triage,
            severity=spec.severity,
            summary=spec.headline,
            metrics=[
                MetricDatum(
                    key="probability",
                    label="Resistance Probability",
                    value=spec.probability,
                    unit="ratio",
                ),
                MetricDatum(
                    key="actionability_score",
                    label="Actionability",
                    value=spec.actionability_score,
                    unit="ratio",
                ),
                MetricDatum(
                    key="novelty_score",
                    label="Novelty",
                    value=spec.novelty_score,
                    unit="ratio",
                ),
                MetricDatum(
                    key="qc_risk",
                    label="QC Risk",
                    value=spec.qc_risk,
                    unit="ratio",
                ),
            ],
        ),
        evidence_table=EvidenceTableBlock(
            title="Mechanistic Evidence",
            columns=["Feature", "Support", "Interpretation"],
            rows=[
                EvidenceTableRow(
                    row_id=f"{spec.job_id}_mechanism_row",
                    label=spec.mechanism_gene,
                    cells={
                        "Feature": spec.mechanism_gene,
                        "Support": spec.mechanism_support.value,
                        "Interpretation": spec.mechanism_status,
                    },
                    evidence_id=f"{spec.job_id}_mechanism_table",
                )
            ],
        ),
        risk_charts=[
            RiskChartBlock(
                chart_id=f"{spec.job_id}_risk_decomposition",
                title="Risk Decomposition",
                chart_type="bar",
                points=[
                    RiskChartPoint(
                        label="Novelty",
                        value=spec.novelty_risk,
                        evidence_id=f"{spec.job_id}_decision_json",
                    ),
                    RiskChartPoint(
                        label="QC",
                        value=spec.qc_risk,
                        evidence_id=f"{spec.job_id}_decision_json",
                    ),
                    RiskChartPoint(
                        label="Actionability",
                        value=spec.actionability_score,
                        evidence_id=f"{spec.job_id}_decision_json",
                    ),
                ],
            )
        ],
        safety_profile=SafetyProfileBlock(
            title="Demo Safety Profile",
            axes=[
                SafetyProfileAxis(label="Evidence Support", value=spec.actionability_score),
                SafetyProfileAxis(label="QC Confidence", value=round(1.0 - spec.qc_risk, 3)),
                SafetyProfileAxis(label="Novelty Burden", value=spec.novelty_risk),
            ],
        ),
        queue_block=QueueBlock(
            title="Analyst Queue Context",
            items=[_build_queue_item(spec)],
        ),
        notes=[
            "Fixture-backed Phase 8 demo surface.",
            "Values mirror the demo decision object and are safe for offline UI development.",
        ],
    )


def _build_copilot(spec: DemoJobSpec, *, mode: str, question: str | None) -> CopilotResponse:
    evidence_ids = [f"{spec.job_id}_decision_json", f"{spec.job_id}_mechanism_table"]
    if mode == "answer":
        content = (
            f"Grounded demo answer for '{question}': {spec.headline}. "
            f"The cited mechanism row is {spec.mechanism_gene} with {spec.mechanism_support.value} support."
        )
        title = "Grounded Demo Answer"
    elif mode == "queue_summary":
        content = (
            f"{spec.sample_id} is queued as {spec.triage.value} with {spec.severity.value} severity "
            f"because {', '.join(code.value for code in spec.rationale_codes)}."
        )
        title = "Queue Summary"
    else:
        content = (
            f"{spec.organism_label} has an actionability score of {spec.actionability_score:.2f} "
            f"for {spec.target_drug}; recommendation: {spec.recommended_next_step}."
        )
        title = "Decision Explanation"
    return CopilotResponse(
        job_id=spec.job_id,
        sample_id=spec.sample_id,
        target_drug=spec.target_drug,
        summary=content,
        next_steps=[spec.recommended_next_step],
        cited_evidence_ids=evidence_ids,
        answer_blocks=[
            CopilotAnswerBlock(
                block_id=f"{mode}_block",
                block_type="summary" if mode != "answer" else "bullets",
                title=title,
                content=content,
                cited_evidence_ids=evidence_ids,
            )
        ],
        semantic_ui=_build_semantic_ui(spec) if mode == "semantic_ui" else None,
        warnings=["fixture_backed_demo_response"],
    )


def _demo_output_origin(mode: str) -> CopilotOutputOrigin:
    return CopilotOutputOrigin(
        mode=CopilotOutputMode.FALLBACK,
        provider="demo_fixture",
        detail=f"demo_seeded_{mode}",
    )
