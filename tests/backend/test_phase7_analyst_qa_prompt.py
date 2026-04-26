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
    MechanisticEvidence,
    NoveltyAssessment,
    NoveltyBucket,
    OrganismConsistency,
    OrganismHint,
    PhenotypePrediction,
    PredictedPhenotype,
    QCStatus,
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
    GroundedAnalystQAPromptBuilder,
    LLMClientError,
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


def _build_context(question: str | None = None):
    return CopilotContextBuilder().build(
        _build_decision(),
        artifact_manifest=_build_manifest(),
        user_question=question,
    )


def test_grounded_analyst_qa_prompt_builder_includes_refusal_and_non_guessing_rules() -> None:
    decision = _build_decision()
    context = _build_context("Why was this isolate deferred?")
    builder = GroundedAnalystQAPromptBuilder()

    request = builder.build_request(decision, context)

    assert request.operation == "grounded_analyst_qa"
    assert request.output_format.value == "json"
    assert json.loads(request.metadata["allowed_evidence_ids_json"]) == list(
        builder.allowed_evidence_ids(context)
    )
    system_message = request.messages[0].content
    user_message = request.messages[1].content
    assert "If the answer is not available in evidence, say that clearly instead of guessing" in system_message
    assert "If the question asks for unsafe operational details" in system_message
    assert "Keep the payload compact enough to finish in one response" in system_message
    assert "\"block_id\":\"qa_answer\"" in system_message
    assert "\"block_id\":\"qa_refusal\"" in system_message
    assert "Analyst question: Why was this isolate deferred?" in user_message
    assert "decision_summary" in user_message
    assert "prohibited_inference_zones" in user_message


def test_grounded_analyst_qa_prompt_builder_requires_question() -> None:
    decision = _build_decision()
    context = _build_context()

    with pytest.raises(ValueError, match="requires a user question"):
        GroundedAnalystQAPromptBuilder().build_request(decision, context)


def test_grounded_analyst_qa_prompt_builder_accepts_explicit_question_override() -> None:
    decision = _build_decision()
    context = _build_context()
    request = GroundedAnalystQAPromptBuilder().build_request(
        decision,
        context,
        question="Which signals suggest novelty?",
    )

    assert "Analyst question: Which signals suggest novelty?" in request.messages[1].content


@pytest.mark.live
def test_live_grounded_analyst_qa_prompt_returns_grounded_copilot_response() -> None:
    settings = load_settings()
    assert settings.llm.provider == "openrouter", (
        "LLM_PROVIDER must be set to openrouter for live analyst Q&A tests"
    )
    assert settings.llm.api_key, "LLM_API_KEY must be configured in the local environment"
    assert settings.llm.base_url, "LLM_BASE_URL must be configured in the local environment"
    assert settings.llm.model, "LLM_MODEL must be configured in the local environment"

    decision = _build_decision()
    context = _build_context("Why was this isolate deferred?")
    builder = GroundedAnalystQAPromptBuilder(
        max_output_tokens=1200,
        reasoning_enabled=False,
    )
    request = builder.build_request(decision, context)
    client = build_llm_client(settings)

    try:
        validated = client.generate_validated(request, CopilotResponse)
    except LLMClientError as exc:
        if "HTTP 429" in str(exc):
            pytest.skip("OpenRouter rate-limited the live analyst Q&A smoke test")
        raise
    allowed_ids = set(builder.allowed_evidence_ids(context))

    assert validated.parsed.job_id == "job_001"
    assert validated.parsed.sample_id == "sample_001"
    assert validated.parsed.target_drug == "tetracycline"
    assert validated.parsed.refusal_required is False
    assert validated.parsed.summary is not None
    assert "defer" in validated.parsed.summary.lower()
    assert set(validated.parsed.cited_evidence_ids).issubset(allowed_ids)
