import {
  ArrowRight,
  ChartLineUp,
  Database,
  ListChecks,
  ShieldCheck,
  WarningCircle,
} from "@phosphor-icons/react";
import { Link } from "react-router-dom";

import { displayText, formatCount, formatPercent, humanizeToken, severityClass, triageClass, triageLabel } from "../api/format";
import type { CaseBundle, TriageOutcome } from "../api/types";
import { ExecutionGatePanel } from "./ExecutionGatePanel";

type CaseDecisionScreenProps = {
  bundle: CaseBundle;
  jobId: string;
};

type Tone = {
  label: string;
  stance: string;
  accent: string;
  border: string;
  band: string;
  panel: string;
  meter: string;
};

const demoCases: Array<{ jobId: string; label: string; triage: TriageOutcome }> = [
  { jobId: "job_demo_act_001", label: "ACT case", triage: "act" },
  { jobId: "job_demo_review_001", label: "REVIEW case", triage: "review" },
  { jobId: "job_demo_defer_001", label: "DEFER case", triage: "defer_to_lab" },
];

const toneByTriage: Record<TriageOutcome, Tone> = {
  act: {
    label: "Operational action",
    stance: "Move this case into immediate analyst handling.",
    accent: "text-act",
    border: "border-act",
    band: "from-red-50 via-white to-white",
    panel: "bg-red-50/70",
    meter: "bg-act",
  },
  review: {
    label: "Analyst review",
    stance: "Hold action until novelty and evidence context are checked.",
    accent: "text-review",
    border: "border-review",
    band: "from-amber-50 via-white to-white",
    panel: "bg-amber-50/70",
    meter: "bg-review",
  },
  defer_to_lab: {
    label: "Laboratory confirmation",
    stance: "Keep operational action deferred until confirmation is available.",
    accent: "text-defer",
    border: "border-defer",
    band: "from-slate-100 via-white to-white",
    panel: "bg-slate-100/80",
    meter: "bg-defer",
  },
};

function boundedRatio(value: number | null | undefined): number {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return 0;
  }
  return Math.max(0, Math.min(1, value));
}

function metricWidth(value: number | null | undefined): string {
  return `${Math.round(boundedRatio(value) * 100)}%`;
}

function booleanLabel(value: boolean | null | undefined): string {
  if (value === true) {
    return "Concordant";
  }
  if (value === false) {
    return "Not concordant";
  }
  return "Not reported";
}

function sourceContextLabel(source: string | null | undefined): string {
  return source ? humanizeToken(source) : "Source unavailable";
}

function hasProvenanceNote(notes: string[], note: string): boolean {
  return notes.includes(note);
}

function resolveInputSource(bundle: CaseBundle): string | null | undefined {
  const decision = bundle.decision.decision;
  return (
    decision.phenotype_prediction.input_source_context
    || decision.sample.metadata?.source_context
    || decision.sample.metadata?.source
  );
}

function inputProvenanceSummary(bundle: CaseBundle): {
  headline: string;
  detail: string;
  inputState: string;
  baselineState: string;
} {
  const decision = bundle.decision.decision;
  const phenotype = decision.phenotype_prediction;
  const sourceLabel = sourceContextLabel(resolveInputSource(bundle));
  const liveRetrieved = hasProvenanceNote(
    decision.provenance_notes,
    "analysis_input_phase6b_live_retrieval_fasta_path",
  );
  const fixtureBackedBaseline = phenotype.model_training_split_context === "fixture";

  if (liveRetrieved && fixtureBackedBaseline) {
    return {
      headline: "Live input, fixture-trained baseline",
      detail: `This case analyzed a real Phase 6B-retrieved FASTA through ${sourceLabel}. The current predictive baseline is still fixture-trained, so input provenance is live while the first-pass model remains a smoke baseline.`,
      inputState: `Live-retrieved FASTA via ${sourceLabel}`,
      baselineState: "Fixture-trained smoke baseline",
    };
  }

  if (liveRetrieved) {
    return {
      headline: "Live input provenance confirmed",
      detail: `This case analyzed a live-retrieved FASTA through ${sourceLabel}, and the predictive baseline does not report a fixture-only training context.`,
      inputState: `Live-retrieved FASTA via ${sourceLabel}`,
      baselineState: displayText(phenotype.model_training_split_context),
    };
  }

  return {
    headline: "Input and model provenance",
    detail: `Input provenance resolves through ${sourceLabel}. The predictive baseline reports ${displayText(phenotype.model_training_split_context)} training context for this decision.`,
    inputState: sourceLabel,
    baselineState: displayText(phenotype.model_training_split_context),
  };
}

function MetricLine({
  label,
  value,
  detail,
  tone,
}: {
  label: string;
  value: number | null | undefined;
  detail: string;
  tone: Tone;
}) {
  return (
    <div className="border-t border-line pt-4 first:border-t-0 first:pt-0">
      <div className="flex items-end justify-between gap-4">
        <div>
          <p className="label-caps text-ink-muted">{label}</p>
          <p className="mt-2 text-sm leading-5 text-ink-muted">{detail}</p>
        </div>
        <p className="font-display text-2xl font-bold tracking-tight">{formatPercent(value, 0)}</p>
      </div>
      <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-surface-strong">
        <div className={`h-full rounded-full ${tone.meter}`} style={{ width: metricWidth(value) }} />
      </div>
    </div>
  );
}

function SmallFact({
  label,
  value,
  icon: Icon,
}: {
  label: string;
  value: string;
  icon: typeof ShieldCheck;
}) {
  return (
    <div className="border-t border-line py-4 first:border-t-0">
      <div className="flex items-start gap-3">
        <Icon className="mt-0.5 text-ink" size={20} weight="duotone" />
        <div>
          <p className="label-caps text-ink-muted">{label}</p>
          <p className="mt-2 text-sm font-semibold leading-5 text-ink">{value}</p>
        </div>
      </div>
    </div>
  );
}

export function CaseDecisionScreen({ bundle, jobId }: CaseDecisionScreenProps) {
  const decision = bundle.decision.decision;
  const triageDecision = decision.triage_decision;
  const sample = decision.sample;
  const phenotype = decision.phenotype_prediction;
  const actionability = decision.actionability_features;
  const novelty = decision.novelty_assessment;
  const qc = decision.assembly_qc;
  const triage = triageDecision.triage;
  const tone = toneByTriage[triage];
  const metadata = sample.metadata;
  const queueItem = bundle.semanticUi.semantic_ui.queue_block?.items.find((item) => item.job_id === jobId);
  const mechanismCount = decision.mechanistic_evidence.length;
  const rationaleCodes = triageDecision.rationale_codes.length > 0 ? triageDecision.rationale_codes : decision.rationale_codes;
  const sourceLabel = sourceContextLabel(resolveInputSource(bundle));
  const provenanceSummary = inputProvenanceSummary(bundle);
  const showDemoCaseSwitcher = jobId.startsWith("job_demo_");

  return (
    <section className="grid gap-gutter">
      <div className={`clinical-panel min-w-0 overflow-hidden border-l-[4px] ${tone.border}`}>
        <div className={`bg-gradient-to-br ${tone.band} p-5 md:p-7`}>
          <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
            <div className="max-w-[68ch]">
              <div className="flex flex-wrap items-center gap-2">
                <span
                  className={`inline-flex rounded border px-2.5 py-1 text-[0.68rem] font-bold uppercase tracking-[0.14em] ${triageClass(
                    triage,
                  )}`}
                >
                  {triageLabel(triage)}
                </span>
                <span className={`label-caps rounded border px-2.5 py-1 ${tone.border} ${tone.accent}`}>
                  {humanizeToken(triageDecision.severity)}
                </span>
                <span className="label-caps rounded border border-line bg-white px-2.5 py-1 text-ink-muted">
                  {humanizeToken(bundle.status.status)}
                </span>
              </div>

              <p className="mt-7 label-caps text-ink-muted">Case decision</p>
              <h2 className="mt-3 font-display text-4xl font-bold leading-[0.95] tracking-[-0.045em] text-ink md:text-5xl">
                {humanizeToken(sample.organism_hint)} against {humanizeToken(sample.target_drug)}
              </h2>
              <p className="mt-5 max-w-[60ch] text-base leading-7 text-ink-muted">
                {triageDecision.recommended_next_step}
              </p>
            </div>

            <aside className={`rounded-lg border ${tone.border} ${tone.panel} p-4 lg:w-72`}>
              <p className={`label-caps ${tone.accent}`}>{tone.label}</p>
              <p className="mt-3 font-display text-xl font-semibold leading-tight">{tone.stance}</p>
              <div className="mt-5 flex items-center justify-between border-t border-line pt-4">
                <span className="label-caps text-ink-muted">Actionability</span>
                <span className="font-display text-3xl font-bold tracking-tight">
                  {formatPercent(actionability.actionability_score, 0)}
                </span>
              </div>
            </aside>
          </div>

          <div className="mt-8 grid gap-3 md:grid-cols-2">
            <div className="rounded-lg border border-line bg-white/85 p-4">
              <p className="label-caps text-ink-muted">Sample ID</p>
              <p className="mt-3 break-all font-data text-sm font-bold text-ink">{sample.sample_id}</p>
            </div>
            <div className="rounded-lg border border-line bg-white/85 p-4">
              <p className="label-caps text-ink-muted">Accession</p>
              <p className="mt-3 break-all font-data text-sm font-bold text-ink">{metadata?.accession ?? "Unavailable"}</p>
            </div>
            <div className="rounded-lg border border-line bg-white/85 p-4">
              <p className="label-caps text-ink-muted">Input source</p>
              <p className="mt-3 text-sm font-semibold text-ink">{sourceLabel}</p>
            </div>
            <div className="rounded-lg border border-line bg-white/85 p-4">
              <p className="label-caps text-ink-muted">Queue priority</p>
              <p className="mt-3 font-display text-2xl font-bold tracking-tight">
                {queueItem ? formatCount(queueItem.queue_priority) : "Unavailable"}
              </p>
            </div>
          </div>

          <div className="mt-4 rounded-lg border border-line bg-white/90 p-4 md:p-5">
            <div className="grid gap-4">
              <div>
                <p className="label-caps text-ink-muted">Provenance split</p>
                <h3 className="mt-2 font-display text-xl font-semibold tracking-tight text-ink">
                  {provenanceSummary.headline}
                </h3>
                <p className="mt-3 max-w-[64ch] text-sm leading-6 text-ink-muted">
                  {provenanceSummary.detail}
                </p>
              </div>
              <div className="grid gap-3 md:grid-cols-2">
                <div className="rounded-lg border border-line bg-surface-panel px-4 py-3">
                  <p className="label-caps text-ink-muted">Analysis input</p>
                  <p className="mt-2 text-sm font-semibold leading-6 text-ink">{provenanceSummary.inputState}</p>
                </div>
                <div className="rounded-lg border border-line bg-surface-panel px-4 py-3">
                  <p className="label-caps text-ink-muted">Predictive baseline</p>
                  <p className="mt-2 text-sm font-semibold leading-6 text-ink">{provenanceSummary.baselineState}</p>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div className="grid gap-0 divide-y divide-line">
          <div className="p-5 md:p-6">
            <p className="label-caps text-ink-muted">Rationale codes</p>
            {rationaleCodes.length > 0 ? (
              <div className="mt-4 flex flex-wrap gap-2">
                {rationaleCodes.map((code) => (
                  <span className="rounded border border-line bg-surface-muted px-2.5 py-1 font-data text-xs" key={code}>
                    {humanizeToken(code)}
                  </span>
                ))}
              </div>
            ) : (
              <p className="mt-4 text-sm leading-6 text-ink-muted">No rationale codes were included with this decision.</p>
            )}

            <div className="mt-6 rounded-lg border border-line bg-ink p-4 text-white">
              <p className="label-caps text-slate-300">Recommended next step</p>
              <p className="mt-3 text-sm leading-6 text-slate-100">{triageDecision.recommended_next_step}</p>
            </div>
          </div>

          <div className="grid gap-0 divide-y divide-line md:grid-cols-3 md:divide-x md:divide-y-0">
            <div className="p-5">
              <SmallFact
                icon={ShieldCheck}
                label="QC status"
                value={`${humanizeToken(qc.qc_status)} / ${formatPercent(actionability.qc_risk, 0)} risk`}
              />
            </div>
            <div className="p-5">
              <SmallFact
                icon={Database}
                label="Mechanisms"
                value={`${formatCount(mechanismCount)} row${mechanismCount === 1 ? "" : "s"} linked`}
              />
            </div>
            <div className="p-5">
              <SmallFact
                icon={ListChecks}
                label="Evidence"
                value={`${formatCount(bundle.artifacts.artifacts.length)} artifacts available`}
              />
            </div>
          </div>
        </div>
      </div>

      <ExecutionGatePanel bundle={bundle} />

      <div className="grid gap-gutter">
        <div className="clinical-panel p-5 md:p-6">
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="label-caps text-ink-muted">Decision signals</p>
              <h3 className="mt-2 font-display text-xl font-semibold">Grounded scorecard</h3>
            </div>
            <ChartLineUp size={26} weight="duotone" className={tone.accent} />
          </div>
          <div className="mt-5 grid gap-4">
            <MetricLine
              detail={humanizeToken(phenotype.predicted_phenotype)}
              label="Resistance probability"
              tone={tone}
              value={phenotype.probability}
            />
            <MetricLine
              detail={booleanLabel(actionability.mechanism_concordance)}
              label="Actionability score"
              tone={tone}
              value={actionability.actionability_score}
            />
            <MetricLine
              detail={humanizeToken(novelty.novelty_bucket)}
              label="Novelty risk"
              tone={tone}
              value={actionability.novelty_risk}
            />
          </div>
        </div>

        <div className="clinical-panel overflow-hidden">
          {showDemoCaseSwitcher ? (
            <>
              <div className="border-b border-line bg-surface-muted p-5">
                <p className="label-caps text-ink-muted">Demo case switcher</p>
              </div>
              <div className="divide-y divide-line">
                {demoCases.map((demoCase) => {
                  const isActive = demoCase.jobId === jobId;
                  return (
                    <Link
                      className={`group flex items-center justify-between gap-4 p-4 transition hover:bg-white active:scale-[0.99] ${
                        isActive ? "bg-white" : "bg-surface-panel"
                      }`}
                      key={demoCase.jobId}
                      to={`/cases/${demoCase.jobId}`}
                    >
                      <div>
                        <span
                          className={`inline-flex rounded border px-2 py-1 text-[0.65rem] font-bold uppercase tracking-[0.12em] ${triageClass(
                            demoCase.triage,
                          )}`}
                        >
                          {triageLabel(demoCase.triage)}
                        </span>
                        <p className="mt-2 text-sm font-semibold text-ink">{demoCase.label}</p>
                        <p className="mt-1 font-data text-[0.7rem] uppercase tracking-[0.12em] text-ink-muted">
                          {demoCase.jobId}
                        </p>
                      </div>
                      <ArrowRight
                        className={`transition group-hover:translate-x-1 ${isActive ? "text-ink" : "text-ink-muted"}`}
                        size={18}
                        weight="bold"
                      />
                    </Link>
                  );
                })}
              </div>
            </>
          ) : null}
          <div className={showDemoCaseSwitcher ? "border-t border-line p-5" : "p-5"}>
            <div className="flex items-start gap-3">
              <WarningCircle className={severityClass(triageDecision.severity)} size={22} weight="duotone" />
              <div>
                <p className="label-caps text-ink-muted">Guardrail</p>
                <p className="mt-2 text-sm leading-6 text-ink-muted">
                  This header reflects the persisted decision object only. Mechanism inspection is shown below, while
                  chart decomposition and grounded copilot language remain separate Phase 8 surfaces.
                </p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
