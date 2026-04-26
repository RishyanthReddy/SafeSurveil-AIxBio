from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

from app.contracts import (
    ActionabilityFeatures,
    AllowedEvidenceSource,
    ArtifactManifest,
    ArtifactRecord,
    ContextSectionType,
    CopilotContext,
    CopilotContextSection,
    DecisionObject,
    MechanisticEvidence,
    NoveltyAssessment,
    PhenotypePrediction,
)

_DEFAULT_PROHIBITED_INFERENCE_ZONES = (
    "Do not claim evidence that is not present in the saved decision, novelty, mechanism, or artifact records.",
    "Do not upgrade degraded, missing, or fixture-backed inputs into live-grounded statements.",
    "Do not invent phenotype mechanisms, laboratory confirmation, or clinical directives beyond the recorded rationale and next step.",
)


def _humanize_token(value: str) -> str:
    return value.replace("_", " ")


def _sentence_list(items: Iterable[str]) -> str:
    normalized = [item.strip() for item in items if item and item.strip()]
    if not normalized:
        return "None recorded."
    return "; ".join(normalized)


def _artifact_line(record: ArtifactRecord) -> str:
    return f"{record.kind.value}: {record.artifact_id}"


def _mechanism_line(evidence: MechanisticEvidence, *, index: int) -> tuple[str, str]:
    label = evidence.gene_symbol or evidence.mutation or f"mechanism_{index}"
    evidence_id = f"mechanistic_evidence__{index}"
    line = (
        f"{label} ({evidence.mechanism_class}) supports {', '.join(evidence.drug_association) or evidence.target_drug} "
        f"with {evidence.support_level.value} support; interpretation: {evidence.interpretation}."
    )
    if evidence.raw_artifact_id:
        line = f"{line} Raw artifact: {evidence.raw_artifact_id}."
    return evidence_id, line


@dataclass(frozen=True)
class CopilotContextBuilder:
    include_private_thresholds: bool = False
    local_policy_notes: tuple[str, ...] = field(default_factory=tuple)
    prohibited_inference_zones: tuple[str, ...] = field(
        default_factory=lambda: _DEFAULT_PROHIBITED_INFERENCE_ZONES
    )

    def build(
        self,
        decision: DecisionObject,
        *,
        artifact_manifest: ArtifactManifest | None = None,
        user_question: str | None = None,
        private_threshold_notes: Sequence[str] = (),
        extra_warnings: Sequence[str] = (),
    ) -> CopilotContext:
        allowed_sources = [
            AllowedEvidenceSource.DECISION_OBJECT,
            AllowedEvidenceSource.MECHANISTIC_EVIDENCE,
            AllowedEvidenceSource.NOVELTY_ASSESSMENT,
            AllowedEvidenceSource.PHENOTYPE_PREDICTION,
            AllowedEvidenceSource.ACTIONABILITY_FEATURES,
        ]
        if artifact_manifest is not None:
            allowed_sources.append(AllowedEvidenceSource.ARTIFACT_MANIFEST)

        sections = [
            self._decision_section(decision),
            self._evidence_section(decision.mechanistic_evidence, artifact_manifest=artifact_manifest),
            self._novelty_section(decision.novelty_assessment),
            self._actionability_section(
                decision.phenotype_prediction,
                decision.actionability_features,
                decision=decision,
                private_threshold_notes=private_threshold_notes,
            ),
            self._limitation_section(decision, artifact_manifest=artifact_manifest),
            self._prohibited_inference_section(),
        ]
        if user_question:
            sections.append(
                CopilotContextSection(
                    section_id="question",
                    section_type=ContextSectionType.QUESTION,
                    title="Current Analyst Question",
                    content=user_question,
                    evidence_ids=["decision_object__summary"],
                )
            )

        warnings = self._collect_warnings(decision, extra_warnings=extra_warnings)

        return CopilotContext(
            sample_id=decision.sample.sample_id,
            job_id=decision.job_id or decision.triage_decision.job_id,
            allowed_evidence_sources=allowed_sources,
            sections=sections,
            user_question=user_question,
            warnings=warnings,
        )

    def _decision_section(self, decision: DecisionObject) -> CopilotContextSection:
        metadata = decision.sample.metadata
        accession = metadata.accession or "not recorded"
        organism = decision.sample.organism_hint.value if decision.sample.organism_hint else "unknown"
        rationale = ", ".join(_humanize_token(code.value) for code in decision.rationale_codes)
        content = (
            f"Sample {decision.sample.sample_id} for {_humanize_token(organism)} and target drug "
            f"{_humanize_token(decision.sample.target_drug)} was triaged as "
            f"{_humanize_token(decision.triage_decision.triage.value)} with "
            f"{decision.triage_decision.severity.value} severity. "
            f"Recommended next step: {decision.triage_decision.recommended_next_step}. "
            f"Rationale codes: {rationale}. "
            f"Accession: {accession}. Source context: {metadata.source_context.value}."
        )
        return CopilotContextSection(
            section_id="decision_summary",
            section_type=ContextSectionType.DECISION,
            title="Decision Summary",
            content=content,
            evidence_ids=["decision_object__summary", "decision_object__triage"],
        )

    def _evidence_section(
        self,
        mechanistic_evidence: Sequence[MechanisticEvidence],
        *,
        artifact_manifest: ArtifactManifest | None,
    ) -> CopilotContextSection:
        mechanism_lines: list[str] = []
        evidence_ids: list[str] = []
        for index, evidence in enumerate(mechanistic_evidence, start=1):
            evidence_id, line = _mechanism_line(evidence, index=index)
            evidence_ids.append(evidence_id)
            mechanism_lines.append(line)

        if not mechanism_lines:
            mechanism_lines.append(
                "No mechanistic evidence entries were recorded for this job."
            )
            evidence_ids.append("mechanistic_evidence__none")

        if artifact_manifest is not None and artifact_manifest.artifacts:
            artifact_lines = ", ".join(_artifact_line(record) for record in artifact_manifest.artifacts[:5])
            mechanism_lines.append(f"Available artifacts: {artifact_lines}.")
            evidence_ids.extend(record.artifact_id for record in artifact_manifest.artifacts[:5])

        return CopilotContextSection(
            section_id="evidence_summary",
            section_type=ContextSectionType.EVIDENCE,
            title="Mechanistic Evidence and Artifacts",
            content=" ".join(mechanism_lines),
            evidence_ids=evidence_ids,
        )

    def _novelty_section(self, novelty: NoveltyAssessment) -> CopilotContextSection:
        nearest_neighbor = novelty.nearest_neighbor_id or "not recorded"
        content = (
            f"Novelty bucket: {_humanize_token(novelty.novelty_bucket.value)}. "
            f"Novelty score: {novelty.novelty_score if novelty.novelty_score is not None else 'not recorded'}. "
            f"Novelty percentile: {novelty.novelty_percentile if novelty.novelty_percentile is not None else 'not recorded'}. "
            f"Nearest neighbor: {nearest_neighbor}. "
            f"Nearest neighbor distance: {novelty.nearest_neighbor_distance if novelty.nearest_neighbor_distance is not None else 'not recorded'}. "
            f"Reference snapshot: {novelty.reference_snapshot_id}. "
            f"Missing reference: {novelty.missing_reference}. Uncertainty flag: {novelty.uncertainty_flag}."
        )
        return CopilotContextSection(
            section_id="novelty_summary",
            section_type=ContextSectionType.NOVELTY,
            title="Novelty Assessment",
            content=content,
            evidence_ids=["novelty_assessment__summary"],
        )

    def _actionability_section(
        self,
        prediction: PhenotypePrediction,
        actionability: ActionabilityFeatures,
        *,
        decision: DecisionObject,
        private_threshold_notes: Sequence[str],
    ) -> CopilotContextSection:
        threshold_bits = [
            f"Actionability threshold version: {actionability.threshold_version}.",
            f"Triage threshold version: {decision.triage_decision.threshold_version}.",
        ]
        if self.include_private_thresholds:
            normalized_private = [item.strip() for item in private_threshold_notes if item.strip()]
            if normalized_private:
                threshold_bits.append(
                    f"Private threshold notes: {'; '.join(normalized_private)}."
                )
        policy_bits = [item.strip() for item in self.local_policy_notes if item.strip()]
        content = (
            f"Predicted phenotype: {prediction.predicted_phenotype.value}. "
            f"Prediction probability: {prediction.probability}. "
            f"Calibration status: {prediction.calibration_status.value}. "
            f"Uncertainty score: {prediction.uncertainty_score if prediction.uncertainty_score is not None else 'not recorded'}. "
            f"Actionability score: {actionability.actionability_score}. "
            f"Mechanism concordance: {actionability.mechanism_concordance}. "
            f"QC risk: {actionability.qc_risk}. "
            f"Novelty risk: {actionability.novelty_risk}. "
            f"Metadata completeness: {actionability.metadata_completeness}. "
            f"{' '.join(threshold_bits)} "
            f"Allowed next step: {decision.triage_decision.recommended_next_step}."
        )
        if policy_bits:
            content = f"{content} Local policy notes: {'; '.join(policy_bits)}."
        return CopilotContextSection(
            section_id="actionability_summary",
            section_type=ContextSectionType.ACTIONABILITY,
            title="Prediction, Actionability, and Allowed Next Steps",
            content=content,
            evidence_ids=[
                "phenotype_prediction__summary",
                "actionability_features__summary",
                "decision_object__triage",
            ],
        )

    def _limitation_section(
        self,
        decision: DecisionObject,
        *,
        artifact_manifest: ArtifactManifest | None,
    ) -> CopilotContextSection:
        qc = decision.assembly_qc
        limitation_bits = [
            f"QC status: {qc.qc_status.value}.",
            f"File valid: {qc.file_valid}.",
            f"Sequence count: {qc.sequence_count}.",
            f"Total bases: {qc.total_bases}.",
            f"Ambiguous base fraction: {qc.ambiguous_base_fraction}.",
            f"Organism consistency: {qc.organism_consistency.value}.",
            f"Missing metadata fields: {_sentence_list(qc.missing_metadata_fields)}.",
            f"QC warnings: {_sentence_list(qc.warnings)}.",
            f"Decision warnings: {_sentence_list(decision.warnings)}.",
            f"Prediction warnings: {_sentence_list(decision.phenotype_prediction.warnings)}.",
            f"Novelty warnings: {_sentence_list(decision.novelty_assessment.warnings)}.",
            f"Actionability warnings: {_sentence_list(decision.actionability_features.warnings)}.",
            f"Triage warnings: {_sentence_list(decision.triage_decision.warnings)}.",
        ]
        if artifact_manifest is None:
            limitation_bits.append("Artifact manifest was not provided to the copilot context.")
        else:
            limitation_bits.append(
                f"Artifact manifest includes {len(artifact_manifest.artifacts)} tracked artifact(s)."
            )
        return CopilotContextSection(
            section_id="limitations",
            section_type=ContextSectionType.LIMITATION,
            title="Quality Checks and Limitations",
            content=" ".join(limitation_bits),
            evidence_ids=["decision_object__assembly_qc", "decision_object__warnings"],
        )

    def _prohibited_inference_section(self) -> CopilotContextSection:
        content = " ".join(self.prohibited_inference_zones)
        return CopilotContextSection(
            section_id="prohibited_inference_zones",
            section_type=ContextSectionType.LIMITATION,
            title="Prohibited Inference Zones",
            content=content,
            evidence_ids=["decision_object__summary"],
        )

    def _collect_warnings(
        self,
        decision: DecisionObject,
        *,
        extra_warnings: Sequence[str] = (),
    ) -> list[str]:
        warnings: list[str] = []
        for item in (
            *decision.warnings,
            *decision.assembly_qc.warnings,
            *decision.phenotype_prediction.warnings,
            *decision.novelty_assessment.warnings,
            *decision.actionability_features.warnings,
            *decision.triage_decision.warnings,
            *extra_warnings,
        ):
            normalized = item.strip()
            if normalized and normalized not in warnings:
                warnings.append(normalized)
        return warnings
