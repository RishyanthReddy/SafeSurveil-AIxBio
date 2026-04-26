from __future__ import annotations

from datetime import date
import json

import pytest

from app.contracts import (
    ActionabilityFeatures,
    ArtifactKind,
    ArtifactManifest,
    ArtifactRecord,
    AssemblyQC,
    CalibrationStatus,
    CopilotResponse,
    DecisionObject,
    JobState,
    MechanisticEvidence,
    NoveltyAssessment,
    NoveltyBucket,
    OrganismConsistency,
    OrganismHint,
    PhenotypePrediction,
    PredictedPhenotype,
    QCStatus,
    QueueItem,
    RationaleCode,
    SampleInput,
    SampleMetadata,
    SeverityLevel,
    SourceContext,
    TriageDecision,
    TriageOutcome,
)
from app.llm import (
    CopilotContextBuilder,
    LLMClientError,
    SemanticUIPromptBuilder,
    build_llm_client,
)
from app.settings import load_settings


def _build_decision() -> DecisionObject:
    sample = SampleInput(
        sample_id="sample_001",
        organism_hint=OrganismHint.E_COLI,
        target_drug="tetracycline",
        fasta_path="data/fixtures/sample.fa",
        metadata=SampleMetadata(
            accession="GCF_000005845.2",
            collection_date=date(2026, 4, 22),
            source_context=SourceContext.BOVINE_MILK,
            country="IN",
        ),
    )
    qc = AssemblyQC(
        job_id="job_001",
        sample_id="sample_001",
        target_drug="tetracycline",
        file_valid=True,
        sequence_count=10,
        total_bases=5012310,
        ambiguous_base_fraction=0.01,
        organism_consistency=OrganismConsistency.MATCH,
        missing_metadata_fields=[],
        qc_status=QCStatus.WARN,
        warnings=["coverage check pending"],
    )
    prediction = PhenotypePrediction(
        job_id="job_001",
        sample_id="sample_001",
        target_drug="tetracycline",
        predicted_phenotype=PredictedPhenotype.RESISTANT,
        probability=0.84,
        calibration_status=CalibrationStatus.NOT_AVAILABLE,
        uncertainty_score=0.16,
        feature_set_version="kmers_v1",
        model_version="baseline_v1",
        warnings=["calibration unavailable"],
    )
    novelty = NoveltyAssessment(
        job_id="job_001",
        sample_id="sample_001",
        target_drug="tetracycline",
        reference_snapshot_id="snapshot_2026_04_22",
        nearest_neighbor_id="ref_001",
        nearest_neighbor_distance=0.14,
        novelty_score=0.73,
        novelty_percentile=89.0,
        novelty_bucket=NoveltyBucket.HIGH,
        warnings=["reference panel limited"],
    )
    actionability = ActionabilityFeatures(
        job_id="job_001",
        sample_id="sample_001",
        target_drug="tetracycline",
        actionability_score=0.21,
        mechanism_concordance=False,
        prediction_entropy=0.33,
        qc_risk=0.25,
        novelty_risk=0.73,
        metadata_completeness=1.0,
        threshold_version="policy_v1",
        warnings=["novelty elevated"],
    )
    triage = TriageDecision(
        job_id="job_001",
        sample_id="sample_001",
        target_drug="tetracycline",
        triage=TriageOutcome.DEFER_TO_LAB,
        severity=SeverityLevel.HIGH,
        recommended_next_step="confirm phenotype in downstream review flow",
        threshold_version="policy_v1",
        rationale_codes=[
            RationaleCode.NO_SUPPORTED_MECHANISM,
            RationaleCode.HIGH_LINEAGE_NOVELTY,
            RationaleCode.MANUAL_CONFIRMATION_REQUIRED,
        ],
        warnings=["manual confirmation required"],
    )
    return DecisionObject(
        job_id="job_001",
        sample=sample,
        assembly_qc=qc,
        mechanistic_evidence=[
            MechanisticEvidence(
                job_id="job_001",
                sample_id="sample_001",
                target_drug="tetracycline",
                gene_symbol="tetA",
                mechanism_class="efflux",
                drug_association=["tetracycline"],
                support_level="supported",
                interpretation="supporting signal present in normalized output",
                raw_artifact_id="artifact_001",
            )
        ],
        phenotype_prediction=prediction,
        novelty_assessment=novelty,
        actionability_features=actionability,
        triage_decision=triage,
        rationale_codes=triage.rationale_codes,
        warnings=["live evidence path present"],
        artifact_manifest_id="manifest_001",
        provenance_notes=["live_mode"],
    )


def _build_manifest() -> ArtifactManifest:
    return ArtifactManifest(
        job_id="job_001",
        sample_id="sample_001",
        target_drug="tetracycline",
        artifact_root="artifacts/runs/jobs/job_001",
        artifacts=[
            ArtifactRecord(
                artifact_id="artifact_001",
                job_id="job_001",
                sample_id="sample_001",
                target_drug="tetracycline",
                kind=ArtifactKind.MECHANISTIC_EVIDENCE,
                path="artifacts/runs/jobs/job_001/mechanism.tsv",
                media_type="text/tab-separated-values",
                generated_by="amrfinderplus_runner",
            )
        ],
    )


def _build_queue_item() -> QueueItem:
    return QueueItem(
        job_id="job_001",
        sample_id="sample_001",
        target_drug="tetracycline",
        triage=TriageOutcome.DEFER_TO_LAB,
        severity=SeverityLevel.HIGH,
        status=JobState.COMPLETED,
        queue_priority=10,
        headline="Defer to lab for high-novelty tetracycline case",
        rationale_codes=[
            RationaleCode.NO_SUPPORTED_MECHANISM,
            RationaleCode.HIGH_LINEAGE_NOVELTY,
            RationaleCode.MANUAL_CONFIRMATION_REQUIRED,
        ],
    )


def _build_context(decision: DecisionObject | None = None):
    return CopilotContextBuilder().build(
        decision or _build_decision(),
        artifact_manifest=_build_manifest(),
    )


def test_semantic_ui_prompt_builder_includes_visualizer_rules() -> None:
    decision = _build_decision()
    context = _build_context()
    queue_item = _build_queue_item()
    builder = SemanticUIPromptBuilder()

    request = builder.build_request(decision, context, queue_item)

    assert request.operation == "semantic_ui_payload"
    assert request.output_format.value == "json"
    assert request.metadata["ui_contract"] == "semantic_ui"
    assert request.metadata["queue_priority"] == "10"
    assert json.loads(request.metadata["allowed_evidence_ids_json"]) == list(
        builder.allowed_evidence_ids(context)
    )
    grounded_numeric_values = json.loads(request.metadata["grounded_numeric_values_json"])
    assert grounded_numeric_values["probability"] == decision.phenotype_prediction.probability
    assert grounded_numeric_values["novelty_score"] == decision.novelty_assessment.novelty_score
    system_message = request.messages[0].content
    user_message = request.messages[1].content
    assert "downstream visualizer" in system_message
    assert "Use decision_card for the high-level case state and key metrics" in system_message
    assert "Use evidence_table for structured mechanism or artifact facts" in system_message
    assert "Use risk_charts to visualize quantitative signals" in system_message
    assert "include at most three evidence_table rows" in system_message
    assert "Never place decision_card, evidence_table, risk_charts, safety_profile, queue_block, or notes at the top level" in system_message
    assert "semantic_ui.queue_block is required when not refusing" in system_message
    assert "\"decision_card\"" in system_message
    assert "\"queue_block\"" in system_message
    assert "Downstream visualizer notes:" in user_message
    assert "queue_headline: Defer to lab for high-novelty tetracycline case" in user_message
    assert "all UI blocks must be nested under semantic_ui" in user_message
    assert "queue_block is required and must preserve the provided queue item exactly" in user_message
    assert "decision_summary" in user_message
    assert "prohibited_inference_zones" in user_message


def test_semantic_ui_prompt_builder_collects_allowed_evidence_ids() -> None:
    builder = SemanticUIPromptBuilder()
    allowed_ids = builder.allowed_evidence_ids(_build_context())

    assert "decision_object__summary" in allowed_ids
    assert "mechanistic_evidence__1" in allowed_ids
    assert "artifact_001" in allowed_ids
    assert len(allowed_ids) == len(set(allowed_ids))


def test_semantic_ui_prompt_builder_carries_grounded_mechanism_map() -> None:
    decision = _build_decision()

    request = SemanticUIPromptBuilder().build_request(
        decision,
        _build_context(decision),
        _build_queue_item(),
    )

    mechanism_map = json.loads(request.metadata["grounded_mechanistic_evidence_json"])
    assert mechanism_map == {"mechanistic_evidence__1": ["tetA"]}
    assert "Grounded mechanistic evidence map:" in request.messages[1].content
    assert '"mechanistic_evidence__1": ["tetA"]' in request.messages[1].content


def test_semantic_ui_prompt_builder_examples_use_current_numeric_values() -> None:
    decision = _build_decision()
    decision = decision.model_copy(
        update={
            "novelty_assessment": decision.novelty_assessment.model_copy(
                update={"novelty_score": 0.34, "novelty_percentile": 47.0}
            ),
            "actionability_features": decision.actionability_features.model_copy(
                update={
                    "actionability_score": 0.52,
                    "qc_risk": 0.41,
                    "metadata_completeness": 0.87,
                }
            ),
        }
    )

    request = SemanticUIPromptBuilder().build_request(
        decision,
        _build_context(decision),
        _build_queue_item(),
    )

    system_message = request.messages[0].content
    assert "\"key\":\"novelty_score\",\"label\":\"Novelty Score\",\"value\":0.34" in system_message
    assert "\"label\":\"Actionability\",\"value\":0.52" in system_message
    assert "\"label\":\"Metadata Completeness\",\"value\":0.87" in system_message
    assert "\"value\":0.73" not in system_message
    assert "\"value\":0.25" not in system_message


def test_semantic_ui_prompt_builder_examples_do_not_invent_mechanisms() -> None:
    decision = _build_decision().model_copy(update={"mechanistic_evidence": []})

    request = SemanticUIPromptBuilder().build_request(
        decision,
        _build_context(decision),
        _build_queue_item(),
    )

    system_message = request.messages[0].content
    assert "\"signal\":\"not recorded\"" in system_message
    assert "No mechanistic evidence entries were recorded for this job." in system_message
    assert "tetA" not in system_message


def test_semantic_ui_prompt_builder_rejects_queue_drift() -> None:
    decision = _build_decision()
    context = _build_context()
    queue_item = _build_queue_item().model_copy(update={"sample_id": "sample_999"})

    with pytest.raises(ValueError, match="QueueItem sample_id"):
        SemanticUIPromptBuilder().build_request(decision, context, queue_item)


@pytest.mark.live
def test_live_semantic_ui_prompt_returns_grounded_semantic_payload() -> None:
    settings = load_settings()
    assert settings.llm.provider == "openrouter", (
        "LLM_PROVIDER must be set to openrouter for live semantic UI prompt tests"
    )
    assert settings.llm.api_key, "LLM_API_KEY must be configured in the local environment"
    assert settings.llm.base_url, "LLM_BASE_URL must be configured in the local environment"
    assert settings.llm.model, "LLM_MODEL must be configured in the local environment"

    decision = _build_decision()
    context = CopilotContextBuilder().build(decision)
    queue_item = _build_queue_item()
    builder = SemanticUIPromptBuilder(
        max_output_tokens=2400,
        reasoning_enabled=False,
    )
    request = builder.build_request(decision, context, queue_item)
    client = build_llm_client(settings)

    try:
        validated = client.generate_validated(request, CopilotResponse)
    except LLMClientError as exc:
        if "HTTP 429" in str(exc):
            pytest.skip("OpenRouter rate-limited the live semantic UI smoke test")
        raise
    allowed_ids = set(builder.allowed_evidence_ids(context))

    assert validated.parsed.job_id == "job_001"
    assert validated.parsed.sample_id == "sample_001"
    assert validated.parsed.target_drug == "tetracycline"
    assert validated.parsed.refusal_required is False
    assert validated.parsed.summary is not None
    assert validated.parsed.semantic_ui is not None
    assert validated.parsed.semantic_ui.decision_card is not None
    assert validated.parsed.semantic_ui.queue_block is not None
    assert set(validated.parsed.cited_evidence_ids).issubset(allowed_ids)
