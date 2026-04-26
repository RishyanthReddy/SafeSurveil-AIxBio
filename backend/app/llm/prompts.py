from __future__ import annotations

from dataclasses import dataclass
import json

from app.contracts import CopilotContext, CopilotContextSection, CopilotResponse, DecisionObject, QueueItem

from .client import LLMRequest


def _format_bullet_list(items: list[str]) -> str:
    if not items:
        return "- none"
    return "\n".join(f"- {item}" for item in items)


def _render_context_section(section: CopilotContextSection) -> str:
    evidence_ids = ", ".join(section.evidence_ids) if section.evidence_ids else "none"
    return (
        f"Section ID: {section.section_id}\n"
        f"Section Type: {section.section_type.value}\n"
        f"Title: {section.title}\n"
        f"Evidence IDs: {evidence_ids}\n"
        f"Content:\n{section.content}"
    )


def _ensure_context_matches_decision(
    decision: DecisionObject,
    context: CopilotContext,
) -> str:
    job_id = decision.job_id or decision.triage_decision.job_id
    if context.job_id != job_id:
        raise ValueError("CopilotContext job_id must match the DecisionObject job_id.")
    if context.sample_id != decision.sample.sample_id:
        raise ValueError("CopilotContext sample_id must match the DecisionObject sample_id.")
    return job_id


def _ensure_queue_item_matches_decision(
    decision: DecisionObject,
    queue_item: QueueItem,
) -> str:
    job_id = decision.job_id or decision.triage_decision.job_id
    if queue_item.job_id != job_id:
        raise ValueError("QueueItem job_id must match the DecisionObject job_id.")
    if queue_item.sample_id != decision.sample.sample_id:
        raise ValueError("QueueItem sample_id must match the DecisionObject sample_id.")
    if queue_item.target_drug != decision.sample.target_drug:
        raise ValueError("QueueItem target_drug must match the DecisionObject target_drug.")
    if queue_item.triage != decision.triage_decision.triage:
        raise ValueError("QueueItem triage must match the DecisionObject triage.")
    if queue_item.severity != decision.triage_decision.severity:
        raise ValueError("QueueItem severity must match the DecisionObject severity.")
    return job_id


def _allowed_evidence_ids(context: CopilotContext) -> tuple[str, ...]:
    ordered_ids: list[str] = []
    for section in context.sections:
        for evidence_id in section.evidence_ids:
            if evidence_id not in ordered_ids:
                ordered_ids.append(evidence_id)
    return tuple(ordered_ids)


def _citation_aliases(context: CopilotContext) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for section in context.sections:
        if section.evidence_ids:
            aliases[section.section_id] = section.evidence_ids[0]
    return aliases


def _base_request_metadata(
    *,
    job_id: str,
    sample_id: str,
    target_drug: str,
    allowed_evidence_ids: tuple[str, ...],
    **extra_metadata: object,
) -> dict[str, str]:
    metadata: dict[str, str] = {
        "job_id": job_id,
        "sample_id": sample_id,
        "target_drug": target_drug,
        "allowed_evidence_ids_json": json.dumps(list(allowed_evidence_ids)),
    }
    metadata.update(
        {
            key: str(value)
            for key, value in extra_metadata.items()
            if value is not None
        }
    )
    return metadata


def _grounded_numeric_values(
    decision: DecisionObject,
    *,
    queue_item: QueueItem | None = None,
) -> dict[str, float | int]:
    numeric_values: dict[str, float | int] = {
        "probability": decision.phenotype_prediction.probability,
        "actionability_score": decision.actionability_features.actionability_score,
        "qc_risk": decision.actionability_features.qc_risk,
        "novelty_risk": decision.actionability_features.novelty_risk,
        "metadata_completeness": decision.actionability_features.metadata_completeness,
        "novelty_score": decision.novelty_assessment.novelty_score,
        "novelty_percentile": decision.novelty_assessment.novelty_percentile,
        "ambiguous_base_fraction": decision.assembly_qc.ambiguous_base_fraction,
        "sequence_count": decision.assembly_qc.sequence_count,
        "total_bases": decision.assembly_qc.total_bases,
    }
    if decision.phenotype_prediction.uncertainty_score is not None:
        numeric_values["uncertainty_score"] = decision.phenotype_prediction.uncertainty_score
    if decision.actionability_features.prediction_entropy is not None:
        numeric_values["prediction_entropy"] = decision.actionability_features.prediction_entropy
    if decision.novelty_assessment.nearest_neighbor_distance is not None:
        numeric_values["nearest_neighbor_distance"] = (
            decision.novelty_assessment.nearest_neighbor_distance
        )
    if queue_item is not None:
        numeric_values["queue_priority"] = queue_item.queue_priority
    return numeric_values


def _grounded_mechanistic_evidence(
    decision: DecisionObject,
) -> dict[str, list[str]]:
    mechanistic_evidence: dict[str, list[str]] = {}
    for index, evidence in enumerate(decision.mechanistic_evidence, start=1):
        signals = [
            value
            for value in (evidence.gene_symbol, evidence.mutation)
            if value
        ]
        if not signals and evidence.mechanism_class:
            signals.append(evidence.mechanism_class)
        if signals:
            mechanistic_evidence[f"mechanistic_evidence__{index}"] = signals
    return mechanistic_evidence


def _json_example(payload: object) -> str:
    return json.dumps(payload, separators=(",", ":"))


def _enum_value(value: object) -> str:
    enum_value = getattr(value, "value", None)
    return str(enum_value if enum_value is not None else value)


def _evidence_id_for_key(
    key: str,
    *,
    allowed_evidence_ids: tuple[str, ...],
    fallback: str,
) -> str:
    preferred_by_key = {
        "probability": "phenotype_prediction__summary",
        "uncertainty_score": "phenotype_prediction__summary",
        "prediction_entropy": "phenotype_prediction__summary",
        "novelty_score": "novelty_assessment__summary",
        "novelty_percentile": "novelty_assessment__summary",
        "nearest_neighbor_distance": "novelty_assessment__summary",
        "actionability_score": "actionability_features__summary",
        "qc_risk": "actionability_features__summary",
        "novelty_risk": "actionability_features__summary",
        "metadata_completeness": "actionability_features__summary",
        "ambiguous_base_fraction": "decision_object__assembly_qc",
        "sequence_count": "decision_object__assembly_qc",
        "total_bases": "decision_object__assembly_qc",
        "queue_priority": "decision_object__summary",
    }
    preferred = preferred_by_key.get(key)
    if preferred in allowed_evidence_ids:
        return preferred
    return fallback


def _grounded_metric_examples(
    decision: DecisionObject,
    queue_item: QueueItem,
) -> list[dict[str, float | int | str]]:
    grounded_values = _grounded_numeric_values(decision, queue_item=queue_item)
    preferred_metrics = (
        ("probability", "Probability"),
        ("novelty_score", "Novelty Score"),
        ("qc_risk", "QC Risk"),
        ("actionability_score", "Actionability"),
        ("metadata_completeness", "Metadata Completeness"),
    )
    metrics: list[dict[str, float | int | str]] = []
    for key, label in preferred_metrics:
        value = grounded_values.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        metrics.append({"key": key, "label": label, "value": value})
        if len(metrics) == 2:
            break
    return metrics


def _grounded_chart_points(
    decision: DecisionObject,
    queue_item: QueueItem,
    *,
    allowed_evidence_ids: tuple[str, ...],
    fallback_evidence_id: str,
) -> list[dict[str, float | str]]:
    points: list[dict[str, float | str]] = []
    for metric in _grounded_metric_examples(decision, queue_item):
        key = str(metric["key"])
        points.append(
            {
                "label": str(metric["label"]),
                "value": float(metric["value"]),
                "evidence_id": _evidence_id_for_key(
                    key,
                    allowed_evidence_ids=allowed_evidence_ids,
                    fallback=fallback_evidence_id,
                ),
            }
        )
    return points


def _grounded_safety_axes(decision: DecisionObject) -> list[dict[str, float | str]]:
    axis_candidates = (
        ("Actionability", decision.actionability_features.actionability_score),
        ("Metadata Completeness", decision.actionability_features.metadata_completeness),
        ("QC Risk", decision.actionability_features.qc_risk),
    )
    return [
        {"label": label, "value": value}
        for label, value in axis_candidates
        if 0.0 <= value <= 1.0
    ][:2]


def _grounded_evidence_table_row(
    decision: DecisionObject,
    *,
    allowed_evidence_ids: tuple[str, ...],
    fallback_evidence_id: str,
) -> dict[str, object]:
    if decision.mechanistic_evidence:
        evidence = decision.mechanistic_evidence[0]
        signal = evidence.gene_symbol or evidence.mutation or evidence.mechanism_class
        evidence_id = (
            "mechanistic_evidence__1"
            if "mechanistic_evidence__1" in allowed_evidence_ids
            else fallback_evidence_id
        )
        return {
            "row_id": "mechanism_1",
            "label": "Mechanistic Evidence",
            "cells": {
                "signal": signal,
                "detail": evidence.interpretation,
                "support": _enum_value(evidence.support_level),
            },
            "evidence_id": evidence_id,
        }

    evidence_id = (
        "mechanistic_evidence__none"
        if "mechanistic_evidence__none" in allowed_evidence_ids
        else fallback_evidence_id
    )
    return {
        "row_id": "mechanism_none",
        "label": "Mechanistic Evidence",
        "cells": {
            "signal": "not recorded",
            "detail": "No mechanistic evidence entries were recorded for this job.",
            "support": "unavailable",
        },
        "evidence_id": evidence_id,
    }


def _queue_item_example(queue_item: QueueItem) -> dict[str, object]:
    return {
        "job_id": queue_item.job_id,
        "sample_id": queue_item.sample_id,
        "target_drug": queue_item.target_drug,
        "triage": _enum_value(queue_item.triage),
        "severity": _enum_value(queue_item.severity),
        "status": _enum_value(queue_item.status),
        "queue_priority": queue_item.queue_priority,
        "headline": queue_item.headline,
        "rationale_codes": [_enum_value(code) for code in queue_item.rationale_codes],
    }


def _semantic_ui_non_refusal_example(
    decision: DecisionObject,
    queue_item: QueueItem,
    *,
    allowed_evidence_ids: tuple[str, ...],
    fallback_evidence_id: str,
) -> dict[str, object]:
    chart_points = _grounded_chart_points(
        decision,
        queue_item,
        allowed_evidence_ids=allowed_evidence_ids,
        fallback_evidence_id=fallback_evidence_id,
    )
    return {
        "job_id": decision.job_id or decision.triage_decision.job_id,
        "sample_id": decision.sample.sample_id,
        "target_drug": decision.sample.target_drug,
        "summary": "Short grounded case overview for the analyst UI.",
        "next_steps": ["Recorded next step."],
        "refusal_required": False,
        "refusal_reason": None,
        "cited_evidence_ids": [fallback_evidence_id],
        "answer_blocks": [
            {
                "block_id": "ui_summary",
                "block_type": "summary",
                "title": "Case Summary",
                "content": "Short grounded summary.",
                "cited_evidence_ids": [fallback_evidence_id],
            },
            {
                "block_id": "ui_next_steps",
                "block_type": "next_steps",
                "title": "Recorded Next Step",
                "content": "Restate the recorded next step.",
                "cited_evidence_ids": [fallback_evidence_id],
            },
        ],
        "semantic_ui": {
            "decision_card": {
                "title": "Decision Overview",
                "triage_decision": decision.triage_decision.triage.value,
                "severity": decision.triage_decision.severity.value,
                "summary": "Short grounded overview for the case detail header.",
                "metrics": _grounded_metric_examples(decision, queue_item),
            },
            "evidence_table": {
                "title": "Evidence Summary",
                "columns": ["signal", "detail", "support"],
                "rows": [
                    _grounded_evidence_table_row(
                        decision,
                        allowed_evidence_ids=allowed_evidence_ids,
                        fallback_evidence_id=fallback_evidence_id,
                    )
                ],
            },
            "risk_charts": [
                {
                    "chart_id": "risk_overview",
                    "title": "Risk Overview",
                    "chart_type": "bar",
                    "points": chart_points,
                }
            ],
            "safety_profile": {
                "title": "Safety Profile",
                "axes": _grounded_safety_axes(decision),
            },
            "queue_block": {
                "title": "Analyst Queue",
                "items": [_queue_item_example(queue_item)],
            },
            "notes": ["Short renderer note if needed."],
        },
        "warnings": ["Optional warning if needed."],
    }


@dataclass(frozen=True)
class DecisionExplanationPromptBuilder:
    max_output_tokens: int = 1100
    reasoning_enabled: bool = False

    def allowed_evidence_ids(self, context: CopilotContext) -> tuple[str, ...]:
        return _allowed_evidence_ids(context)

    def build_request(
        self,
        decision: DecisionObject,
        context: CopilotContext,
    ) -> LLMRequest:
        job_id = _ensure_context_matches_decision(decision, context)
        allowed_evidence_ids = self.allowed_evidence_ids(context)
        example_evidence_id = allowed_evidence_ids[0] if allowed_evidence_ids else "decision_object__summary"
        system_message = (
            "You are the SafeSurveil grounded copilot. "
            "Your job is to explain an existing triage decision for an analyst without changing the science. "
            "Treat the saved decision object as authoritative. "
            "Never change the triage outcome, severity, rationale codes, or recommended next step. "
            "Use only the provided context sections, warnings, and evidence IDs. "
            "Do not invent mechanisms, phenotype claims, laboratory results, live-data status, or upstream retrieval facts. "
            "Do not use clinical directive language or imply confirmed diagnosis. "
            "If evidence is missing or limited, say so clearly in the explanation instead of guessing. "
            "Keep the tone concise, analyst-friendly, and uncertainty-aware. "
            "Keep the payload compact enough to finish in one response: use one summary block and one next_steps block, with a short warnings list. "
            "Return only JSON that matches the CopilotResponse contract for a decision explanation. "
            "Include job_id, sample_id, target_drug, summary, next_steps, refusal_required, refusal_reason, cited_evidence_ids, answer_blocks, and warnings. "
            "Do not include semantic_ui in this task. "
            "Use no extra keys. "
            "next_steps must always be a JSON array of strings, even if there is only one next step. "
            "Every answer_blocks item must include block_id, block_type, content, and cited_evidence_ids. "
            "Use block_type values only from: summary, bullets, next_steps, refusal. "
            f"Only cite evidence IDs from this allowed list: {', '.join(allowed_evidence_ids) if allowed_evidence_ids else 'none'}. "
            "Use this JSON shape exactly: "
            "{"
            f"\"job_id\":\"{job_id}\","
            f"\"sample_id\":\"{decision.sample.sample_id}\","
            f"\"target_drug\":\"{decision.sample.target_drug}\","
            "\"summary\":\"One short grounded explanation.\","
            "\"next_steps\":[\"One allowed next step.\"],"
            "\"refusal_required\":false,"
            "\"refusal_reason\":null,"
            f"\"cited_evidence_ids\":[\"{example_evidence_id}\"],"
            "\"answer_blocks\":["
            "{"
            "\"block_id\":\"decision_summary\","
            "\"block_type\":\"summary\","
            "\"title\":\"Decision Explanation\","
            "\"content\":\"Grounded explanation here.\","
            f"\"cited_evidence_ids\":[\"{example_evidence_id}\"]"
            "},"
            "{"
            "\"block_id\":\"allowed_next_steps\","
            "\"block_type\":\"next_steps\","
            "\"title\":\"Allowed Next Steps\","
            "\"content\":\"Short next-step explanation.\","
            f"\"cited_evidence_ids\":[\"{example_evidence_id}\"]"
            "}"
            "],"
            "\"warnings\":[\"Optional warning if needed.\"]"
            "}."
        )

        question_line = (
            f"Analyst question: {context.user_question}"
            if context.user_question
            else "Analyst question: Explain why this case received its current triage decision."
        )
        section_text = "\n\n".join(_render_context_section(section) for section in context.sections)
        user_message = (
            "Build a concise decision explanation from the provided grounded context.\n\n"
            "Authoritative decision metadata:\n"
            f"- job_id: {job_id}\n"
            f"- sample_id: {decision.sample.sample_id}\n"
            f"- target_drug: {decision.sample.target_drug}\n"
            f"- triage: {decision.triage_decision.triage.value}\n"
            f"- severity: {decision.triage_decision.severity.value}\n"
            f"- recommended_next_step: {decision.triage_decision.recommended_next_step}\n"
            f"- rationale_codes: {', '.join(code.value for code in decision.rationale_codes)}\n\n"
            "Allowed evidence sources:\n"
            f"{_format_bullet_list([source.value for source in context.allowed_evidence_sources])}\n\n"
            "Context warnings:\n"
            f"{_format_bullet_list(context.warnings)}\n\n"
            f"{question_line}\n\n"
            "Context sections:\n"
            f"{section_text}\n\n"
            "Return JSON only. Required output behavior:\n"
            "- summary must explain the existing decision rather than restating the prompt.\n"
            "- next_steps must stay consistent with the recorded recommended next step.\n"
            "- cited_evidence_ids must use only the allowed IDs listed above.\n"
            "- answer_blocks must include at least one summary block and should include one next_steps block when a next step is available.\n"
            "- every answer_blocks item must include a stable block_id.\n"
            "- if the context is too weak to answer safely, set refusal_required=true and explain why.\n"
        )

        return LLMRequest(
            operation="decision_explanation",
            messages=(
                {
                    "role": "system",
                    "content": system_message,
                },
                {
                    "role": "user",
                    "content": user_message,
                },
            ),
            metadata=_base_request_metadata(
                job_id=job_id,
                sample_id=decision.sample.sample_id,
                target_drug=decision.sample.target_drug,
                allowed_evidence_ids=allowed_evidence_ids,
                citation_aliases_json=json.dumps(_citation_aliases(context)),
                triage=decision.triage_decision.triage.value,
                severity=decision.triage_decision.severity.value,
                response_contract=CopilotResponse.__name__.lower(),
            ),
            output_format="json",
            max_output_tokens=self.max_output_tokens,
            reasoning_enabled=self.reasoning_enabled,
        )


@dataclass(frozen=True)
class GroundedAnalystQAPromptBuilder:
    max_output_tokens: int = 1200
    reasoning_enabled: bool = False

    def allowed_evidence_ids(self, context: CopilotContext) -> tuple[str, ...]:
        return _allowed_evidence_ids(context)

    def build_request(
        self,
        decision: DecisionObject,
        context: CopilotContext,
        *,
        question: str | None = None,
    ) -> LLMRequest:
        job_id = _ensure_context_matches_decision(decision, context)
        analyst_question = (question or context.user_question or "").strip()
        if not analyst_question:
            raise ValueError("Grounded analyst Q&A requires a user question.")

        allowed_evidence_ids = self.allowed_evidence_ids(context)
        example_evidence_id = allowed_evidence_ids[0] if allowed_evidence_ids else "decision_object__summary"
        section_text = "\n\n".join(_render_context_section(section) for section in context.sections)

        system_message = (
            "You are the SafeSurveil grounded copilot answering analyst questions about an existing triage decision. "
            "Treat the saved decision object and grounded context as authoritative. "
            "Answer only from the provided context sections, warnings, rationale codes, and evidence IDs. "
            "Questions about why a case was deferred, what evidence supports the recorded call, which signals suggest novelty, and what the recorded next step is are in scope and should be answered when the evidence supports them. "
            "If the answer is not available in evidence, say that clearly instead of guessing. "
            "If the question asks for unsafe operational details, pathogen design, evasion, offensive use, unsupported laboratory protocol, or a change to the decision itself, refuse. "
            "Never invent mechanisms, phenotype claims, laboratory confirmation, live-data claims, or clinical directives. "
            "Never override triage outcome, severity, rationale codes, or the recorded recommended next step. "
            "Keep the tone concise, analyst-friendly, and uncertainty-aware. "
            "Keep the payload compact enough to finish in one response: use one summary answer block plus one optional next_steps or bullets block, and keep warnings to the shortest necessary list. "
            "Return only JSON that matches the CopilotResponse contract for analyst Q and A. "
            "Include job_id, sample_id, target_drug, summary, next_steps, refusal_required, refusal_reason, cited_evidence_ids, answer_blocks, and warnings. "
            "Do not include semantic_ui in this task. "
            "Use no extra keys. "
            "next_steps must always be a JSON array of strings. Use an empty array if no grounded follow-up is appropriate. "
            "Every answer_blocks item must include block_id, block_type, content, and cited_evidence_ids. "
            "Use block_type values only from: summary, bullets, next_steps, refusal. "
            f"Only cite evidence IDs from this allowed list: {', '.join(allowed_evidence_ids) if allowed_evidence_ids else 'none'}. "
            "Use this non-refusal JSON shape exactly: "
            "{"
            f"\"job_id\":\"{job_id}\","
            f"\"sample_id\":\"{decision.sample.sample_id}\","
            f"\"target_drug\":\"{decision.sample.target_drug}\","
            "\"summary\":\"Direct grounded answer to the analyst question.\","
            "\"next_steps\":[\"Optional grounded follow-up question or next step.\"],"
            "\"refusal_required\":false,"
            "\"refusal_reason\":null,"
            f"\"cited_evidence_ids\":[\"{example_evidence_id}\"],"
            "\"answer_blocks\":["
            "{"
            "\"block_id\":\"qa_answer\","
            "\"block_type\":\"summary\","
            "\"title\":\"Analyst Answer\","
            "\"content\":\"Direct grounded answer here.\","
            f"\"cited_evidence_ids\":[\"{example_evidence_id}\"]"
            "},"
            "{"
            "\"block_id\":\"grounded_follow_up\","
            "\"block_type\":\"bullets\","
            "\"title\":\"Grounded Follow-Up\","
            "\"content\":\"One short grounded follow-up suggestion or state that no further grounded answer is available.\","
            f"\"cited_evidence_ids\":[\"{example_evidence_id}\"]"
            "}"
            "],"
            "\"warnings\":[\"Optional warning if needed.\"]"
            "}. "
            "Use this refusal JSON shape exactly when the question is unsafe or out of scope: "
            "{"
            f"\"job_id\":\"{job_id}\","
            f"\"sample_id\":\"{decision.sample.sample_id}\","
            f"\"target_drug\":\"{decision.sample.target_drug}\","
            "\"summary\":null,"
            "\"next_steps\":[],"
            "\"refusal_required\":true,"
            "\"refusal_reason\":\"Brief reason the question cannot be answered safely from evidence.\","
            "\"cited_evidence_ids\":[],"
            "\"answer_blocks\":["
            "{"
            "\"block_id\":\"qa_refusal\","
            "\"block_type\":\"refusal\","
            "\"title\":\"Refusal\","
            "\"content\":\"Brief refusal explanation.\","
            "\"cited_evidence_ids\":[]"
            "}"
            "],"
            "\"warnings\":[\"Optional warning if needed.\"]"
            "}."
        )

        user_message = (
            "Answer the analyst question from grounded context only.\n\n"
            "Authoritative decision metadata:\n"
            f"- job_id: {job_id}\n"
            f"- sample_id: {decision.sample.sample_id}\n"
            f"- target_drug: {decision.sample.target_drug}\n"
            f"- triage: {decision.triage_decision.triage.value}\n"
            f"- severity: {decision.triage_decision.severity.value}\n"
            f"- recommended_next_step: {decision.triage_decision.recommended_next_step}\n"
            f"- rationale_codes: {', '.join(code.value for code in decision.rationale_codes)}\n\n"
            "Allowed evidence sources:\n"
            f"{_format_bullet_list([source.value for source in context.allowed_evidence_sources])}\n\n"
            "Context warnings:\n"
            f"{_format_bullet_list(context.warnings)}\n\n"
            f"Analyst question: {analyst_question}\n\n"
            "Question handling note:\n"
            "- if the question asks why the case was deferred, reviewed, or acted on, answer directly from the saved rationale and evidence.\n"
            "- if the question asks what evidence supports the recorded call, answer directly from the saved mechanism, novelty, prediction, and QC context.\n"
            "- if the question asks what the lab should do next, restate only the recorded recommended next step and any grounded caveats.\n"
            "- do not refuse an in-scope analyst question merely because the evidence is incomplete; instead answer with the limits clearly stated.\n\n"
            "Context sections:\n"
            f"{section_text}\n\n"
            "Required answer behavior:\n"
            "- answer the specific analyst question directly when the evidence supports it.\n"
            "- if evidence is partial or missing, say 'not available in evidence' or equivalent instead of guessing.\n"
            "- if the question is unsafe, out of scope, or asks you to override the saved decision, refuse.\n"
            "- cited_evidence_ids must use only the allowed IDs listed above.\n"
            "- keep next_steps consistent with the recorded recommended next step or with a grounded follow-up question.\n"
            "- do not invent new mechanism, phenotype, or lab-confirmation claims.\n"
        )

        return LLMRequest(
            operation="grounded_analyst_qa",
            messages=(
                {
                    "role": "system",
                    "content": system_message,
                },
                {
                    "role": "user",
                    "content": user_message,
                },
            ),
            metadata=_base_request_metadata(
                job_id=job_id,
                sample_id=decision.sample.sample_id,
                target_drug=decision.sample.target_drug,
                allowed_evidence_ids=allowed_evidence_ids,
                citation_aliases_json=json.dumps(_citation_aliases(context)),
                question_type="analyst_qa",
                response_contract=CopilotResponse.__name__.lower(),
            ),
            output_format="json",
            max_output_tokens=self.max_output_tokens,
            reasoning_enabled=self.reasoning_enabled,
        )


@dataclass(frozen=True)
class QueueSummaryPromptBuilder:
    max_output_tokens: int = 1100
    reasoning_enabled: bool = False

    def allowed_evidence_ids(self, context: CopilotContext) -> tuple[str, ...]:
        return _allowed_evidence_ids(context)

    def build_request(
        self,
        decision: DecisionObject,
        context: CopilotContext,
        queue_item: QueueItem,
    ) -> LLMRequest:
        job_id = _ensure_context_matches_decision(decision, context)
        _ensure_queue_item_matches_decision(decision, queue_item)
        allowed_evidence_ids = self.allowed_evidence_ids(context)
        example_evidence_id = allowed_evidence_ids[0] if allowed_evidence_ids else "decision_object__summary"
        section_text = "\n\n".join(_render_context_section(section) for section in context.sections)

        system_message = (
            "You are the SafeSurveil grounded copilot preparing a compact analyst handoff summary for the queue. "
            "Treat the queue item, saved decision object, and grounded context as authoritative. "
            "Write for an analyst scanning the queue, not for a patient or public audience. "
            "Never change the queue status, triage outcome, severity, queue priority, rationale codes, or recorded recommended next step. "
            "Use only the provided context sections, warnings, queue metadata, and allowed evidence IDs. "
            "Do not invent mechanisms, phenotype claims, live-data status, laboratory confirmation, or clinical directives. "
            "If evidence is limited, state that briefly instead of guessing. "
            "Keep the payload compact enough to finish in one response: use one summary block, one notes block, one next_steps block, and a short warnings list. "
            "Return only JSON that matches the CopilotResponse contract for a queue handoff summary. "
            "Return minified JSON only, with no markdown fences and no extra commentary. "
            "Include job_id, sample_id, target_drug, summary, next_steps, refusal_required, refusal_reason, cited_evidence_ids, answer_blocks, and warnings. "
            "Do not include semantic_ui in this task. "
            "Use no extra keys. "
            "next_steps must always be a JSON array of strings, even if there is only one recorded next step. "
            "Every answer_blocks item must include block_id, block_type, content, and cited_evidence_ids. "
            "Use block_type values only from: summary, bullets, next_steps, refusal. "
            f"Only cite evidence IDs from this allowed list: {', '.join(allowed_evidence_ids) if allowed_evidence_ids else 'none'}. "
            "Use this non-refusal JSON shape exactly: "
            "{"
            f"\"job_id\":\"{job_id}\","
            f"\"sample_id\":\"{decision.sample.sample_id}\","
            f"\"target_drug\":\"{decision.sample.target_drug}\","
            "\"summary\":\"One short queue-ready analyst handoff summary.\","
            "\"next_steps\":[\"Recorded next step or one grounded follow-up reminder.\"],"
            "\"refusal_required\":false,"
            "\"refusal_reason\":null,"
            f"\"cited_evidence_ids\":[\"{example_evidence_id}\"],"
            "\"answer_blocks\":["
            "{"
            "\"block_id\":\"queue_handoff_summary\","
            "\"block_type\":\"summary\","
            "\"title\":\"Queue Handoff Summary\","
            "\"content\":\"Compact grounded handoff here.\","
            f"\"cited_evidence_ids\":[\"{example_evidence_id}\"]"
            "},"
            "{"
            "\"block_id\":\"queue_handoff_notes\","
            "\"block_type\":\"bullets\","
            "\"title\":\"Queue Handoff Notes\","
            "\"content\":\"Two or three short grounded notes for the analyst queue.\","
            f"\"cited_evidence_ids\":[\"{example_evidence_id}\"]"
            "},"
            "{"
            "\"block_id\":\"queue_next_steps\","
            "\"block_type\":\"next_steps\","
            "\"title\":\"Recorded Next Step\","
            "\"content\":\"Restate the recorded next step without changing it.\","
            f"\"cited_evidence_ids\":[\"{example_evidence_id}\"]"
            "}"
            "],"
            "\"warnings\":[\"Optional warning if needed.\"]"
            "}. "
            "Use this refusal JSON shape exactly only when the provided context is too weak to produce a safe grounded handoff: "
            "{"
            f"\"job_id\":\"{job_id}\","
            f"\"sample_id\":\"{decision.sample.sample_id}\","
            f"\"target_drug\":\"{decision.sample.target_drug}\","
            "\"summary\":null,"
            "\"next_steps\":[],"
            "\"refusal_required\":true,"
            "\"refusal_reason\":\"Brief reason the handoff cannot be grounded safely.\","
            "\"cited_evidence_ids\":[],"
            "\"answer_blocks\":["
            "{"
            "\"block_id\":\"queue_handoff_refusal\","
            "\"block_type\":\"refusal\","
            "\"title\":\"Refusal\","
            "\"content\":\"Brief refusal explanation.\","
            "\"cited_evidence_ids\":[]"
            "}"
            "],"
            "\"warnings\":[\"Optional warning if needed.\"]"
            "}."
        )

        user_message = (
            "Prepare a compact analyst handoff summary for the queue from grounded context only.\n\n"
            "Authoritative queue metadata:\n"
            f"- job_id: {queue_item.job_id}\n"
            f"- sample_id: {queue_item.sample_id}\n"
            f"- target_drug: {queue_item.target_drug}\n"
            f"- queue_priority: {queue_item.queue_priority}\n"
            f"- queue_status: {queue_item.status.value}\n"
            f"- queue_headline: {queue_item.headline}\n"
            f"- triage: {queue_item.triage.value}\n"
            f"- severity: {queue_item.severity.value}\n"
            f"- rationale_codes: {', '.join(code.value for code in queue_item.rationale_codes)}\n\n"
            "Authoritative decision metadata:\n"
            f"- recommended_next_step: {decision.triage_decision.recommended_next_step}\n"
            f"- decision_rationale_codes: {', '.join(code.value for code in decision.rationale_codes)}\n\n"
            "Allowed evidence sources:\n"
            f"{_format_bullet_list([source.value for source in context.allowed_evidence_sources])}\n\n"
            "Context warnings:\n"
            f"{_format_bullet_list(context.warnings)}\n\n"
            "Required handoff behavior:\n"
            "- summary must be short, queue-ready, and grounded in the saved decision and evidence.\n"
            "- preserve the queue headline intent, but do not simply copy the headline if the grounded context is more precise.\n"
            "- mention the core reason this case is in the queue, using only recorded triage, severity, rationale, novelty, QC, and mechanism context.\n"
            "- next_steps must remain consistent with the recorded recommended next step.\n"
            "- answer_blocks must include one summary block, one notes block, and one next_steps block when not refusing.\n"
            "- cited_evidence_ids must use only the allowed IDs listed above.\n"
            "- if evidence is partial, state the limitation briefly instead of guessing.\n\n"
            "Context sections:\n"
            f"{section_text}\n\n"
            "Return JSON only.\n"
        )

        return LLMRequest(
            operation="queue_summary_handoff",
            messages=(
                {
                    "role": "system",
                    "content": system_message,
                },
                {
                    "role": "user",
                    "content": user_message,
                },
            ),
            metadata=_base_request_metadata(
                job_id=job_id,
                sample_id=decision.sample.sample_id,
                target_drug=decision.sample.target_drug,
                allowed_evidence_ids=allowed_evidence_ids,
                citation_aliases_json=json.dumps(_citation_aliases(context)),
                queue_priority=queue_item.queue_priority,
                queue_status=queue_item.status.value,
                response_contract=CopilotResponse.__name__.lower(),
            ),
            output_format="json",
            max_output_tokens=self.max_output_tokens,
            reasoning_enabled=self.reasoning_enabled,
        )


@dataclass(frozen=True)
class SemanticUIPromptBuilder:
    max_output_tokens: int = 2400
    reasoning_enabled: bool = False

    def allowed_evidence_ids(self, context: CopilotContext) -> tuple[str, ...]:
        return _allowed_evidence_ids(context)

    def build_request(
        self,
        decision: DecisionObject,
        context: CopilotContext,
        queue_item: QueueItem,
    ) -> LLMRequest:
        job_id = _ensure_context_matches_decision(decision, context)
        _ensure_queue_item_matches_decision(decision, queue_item)
        allowed_evidence_ids = self.allowed_evidence_ids(context)
        example_evidence_id = allowed_evidence_ids[0] if allowed_evidence_ids else "decision_object__summary"
        section_text = "\n\n".join(_render_context_section(section) for section in context.sections)
        grounded_numeric_values = _grounded_numeric_values(decision, queue_item=queue_item)
        grounded_mechanistic_evidence = _grounded_mechanistic_evidence(decision)
        non_refusal_example_json = _json_example(
            _semantic_ui_non_refusal_example(
                decision,
                queue_item,
                allowed_evidence_ids=allowed_evidence_ids,
                fallback_evidence_id=example_evidence_id,
            )
        )

        system_message = (
            "You are the SafeSurveil grounded copilot preparing a structured semantic UI payload for a downstream visualizer. "
            "The downstream visualizer will turn your structured output into UI components such as cards, tables, charts, and queue views, but it does not have access to tools, databases, or hidden state. "
            "Therefore every UI block must be fully grounded in the provided decision object, queue item, context sections, warnings, and allowed evidence IDs. "
            "Use short, clear rules and avoid filler. "
            "Follow these UI rules exactly: "
            "Use decision_card for the high-level case state and key metrics. "
            "Use evidence_table for structured mechanism or artifact facts. "
            "Use risk_charts to visualize quantitative signals such as novelty, QC risk, actionability, or uncertainty. "
            "Use queue_block to preserve the current analyst queue context. "
            "Use safety_profile only for normalized 0 to 1 axes. "
            "Express actions only through next_steps, queue_block, and short answer blocks; do not invent buttons, forms, links, or unsupported interactions in the payload. "
            "Never change the queue status, triage outcome, severity, queue priority, rationale codes, or recorded recommended next step. "
            "Do not invent mechanisms, phenotype claims, laboratory confirmation, live-data status, or clinical directives. "
            "If evidence is limited, state that briefly in summary or notes instead of guessing. "
            "Return minified JSON only, with no markdown fences and no extra commentary. "
            "Keep the payload compact enough to finish in one response: include at most three evidence_table rows, at most one risk_charts entry with four points, and at most two notes. "
            "Return a full CopilotResponse object with a populated semantic_ui field. "
            "Include only these top-level keys: job_id, sample_id, target_drug, summary, next_steps, refusal_required, refusal_reason, cited_evidence_ids, answer_blocks, semantic_ui, warnings. "
            "Never place decision_card, evidence_table, risk_charts, safety_profile, queue_block, or notes at the top level; those keys belong only inside semantic_ui. "
            "Every answer_blocks item must include block_id, block_type, content, and cited_evidence_ids. "
            "Use block_type values only from: summary, bullets, next_steps, refusal. "
            f"Only cite evidence IDs from this allowed list: {', '.join(allowed_evidence_ids) if allowed_evidence_ids else 'none'}. "
            "When not refusing, include at least one summary answer block and one next_steps answer block. "
            "semantic_ui.decision_card must include title, triage_decision, severity, summary, and metrics. "
            "semantic_ui.evidence_table rows should use concise analyst-facing labels and may cite evidence IDs from the allowed list. "
            "If an evidence_table row cites mechanistic_evidence__N, its label or cells must name the exact mechanism signal attached to that ID. "
            "semantic_ui.risk_charts points must use grounded numeric values already present in the saved context. "
            "semantic_ui.queue_block is required when not refusing and must include the provided queue item without changing its fields. "
            "semantic_ui.notes should be short domain-specific renderer notes, not generic styling advice. "
            "Use this non-refusal JSON shape exactly: "
            f"{non_refusal_example_json}. "
            "Use this refusal JSON shape exactly only when the provided context is too weak to build a safe grounded semantic UI payload: "
            "{"
            f"\"job_id\":\"{job_id}\","
            f"\"sample_id\":\"{decision.sample.sample_id}\","
            f"\"target_drug\":\"{decision.sample.target_drug}\","
            "\"summary\":null,"
            "\"next_steps\":[],"
            "\"refusal_required\":true,"
            "\"refusal_reason\":\"Brief reason the UI payload cannot be grounded safely.\","
            "\"cited_evidence_ids\":[],"
            "\"answer_blocks\":["
            "{"
            "\"block_id\":\"ui_refusal\","
            "\"block_type\":\"refusal\","
            "\"title\":\"Refusal\","
            "\"content\":\"Brief refusal explanation.\","
            "\"cited_evidence_ids\":[]"
            "}"
            "],"
            "\"semantic_ui\":null,"
            "\"warnings\":[\"Optional warning if needed.\"]"
            "}."
        )

        user_message = (
            "Build a grounded semantic UI response for the analyst case detail surface.\n\n"
            "Authoritative queue metadata:\n"
            f"- job_id: {queue_item.job_id}\n"
            f"- sample_id: {queue_item.sample_id}\n"
            f"- target_drug: {queue_item.target_drug}\n"
            f"- queue_priority: {queue_item.queue_priority}\n"
            f"- queue_status: {queue_item.status.value}\n"
            f"- queue_headline: {queue_item.headline}\n"
            f"- triage: {queue_item.triage.value}\n"
            f"- severity: {queue_item.severity.value}\n"
            f"- rationale_codes: {', '.join(code.value for code in queue_item.rationale_codes)}\n\n"
            "Authoritative decision metadata:\n"
            f"- recommended_next_step: {decision.triage_decision.recommended_next_step}\n"
            f"- decision_rationale_codes: {', '.join(code.value for code in decision.rationale_codes)}\n\n"
            "Downstream visualizer notes:\n"
            "- it will render cards, tables, charts, and queue surfaces from your semantic_ui payload.\n"
            "- it does not have tool or database access, so every displayed fact must already be present in the grounded context.\n"
            "- prefer structured quantitative signals for charts and structured facts for tables.\n\n"
            "Allowed evidence sources:\n"
            f"{_format_bullet_list([source.value for source in context.allowed_evidence_sources])}\n\n"
            "Grounded mechanistic evidence map:\n"
            f"{json.dumps(grounded_mechanistic_evidence, sort_keys=True)}\n\n"
            "Context warnings:\n"
            f"{_format_bullet_list(context.warnings)}\n\n"
            "Required semantic UI behavior:\n"
            "- decision_card should highlight the triage, severity, and a concise grounded summary.\n"
            "- evidence_table should contain structured evidence rows, not long prose paragraphs.\n"
            "- risk_charts should visualize grounded numeric signals already present in the saved context.\n"
            "- keep evidence_table to at most three rows and risk_charts to one chart with at most four points.\n"
            "- queue_block is required and must preserve the provided queue item exactly.\n"
            "- safety_profile axes must stay within 0 and 1.\n"
            "- all UI blocks must be nested under semantic_ui; do not place risk_charts or other UI fields at the top level.\n"
            "- next_steps must remain consistent with the recorded recommended next step.\n"
            "- cited_evidence_ids must use only the allowed IDs listed above.\n"
            "- if evidence is partial, keep the UI useful but note the limitation briefly instead of inventing content.\n\n"
            "Context sections:\n"
            f"{section_text}\n\n"
            "Return JSON only.\n"
        )

        return LLMRequest(
            operation="semantic_ui_payload",
            messages=(
                {
                    "role": "system",
                    "content": system_message,
                },
                {
                    "role": "user",
                    "content": user_message,
                },
            ),
            metadata=_base_request_metadata(
                job_id=job_id,
                sample_id=decision.sample.sample_id,
                target_drug=decision.sample.target_drug,
                allowed_evidence_ids=allowed_evidence_ids,
                citation_aliases_json=json.dumps(_citation_aliases(context)),
                queue_priority=queue_item.queue_priority,
                queue_status=queue_item.status.value,
                queue_item_json=queue_item.model_dump_json(),
                response_contract=CopilotResponse.__name__.lower(),
                ui_contract="semantic_ui",
                grounded_numeric_values_json=json.dumps(grounded_numeric_values),
                grounded_mechanistic_evidence_json=json.dumps(grounded_mechanistic_evidence),
            ),
            output_format="json",
            max_output_tokens=self.max_output_tokens,
            reasoning_enabled=self.reasoning_enabled,
        )
