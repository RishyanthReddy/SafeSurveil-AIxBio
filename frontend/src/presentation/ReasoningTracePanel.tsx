import { GitBranch, LinkSimple, ListChecks, WarningCircle } from "@phosphor-icons/react";

import { formatCount, formatPercent, humanizeToken, severityClass, triageClass, triageLabel } from "../api/format";
import type {
  CaseBundle,
  ReasoningTrace,
  ReasoningTraceCaveat,
  ReasoningTraceCaveatSeverity,
  ReasoningTraceStep,
  ReasoningTraceStepStatus,
} from "../api/types";

function statusTone(status: ReasoningTraceStepStatus): string {
  if (status === "grounded") {
    return "border-emerald-300 bg-emerald-50 text-emerald-900";
  }
  return "border-amber-300 bg-amber-50 text-amber-950";
}

function caveatTone(severity: ReasoningTraceCaveatSeverity): string {
  if (severity === "warning") {
    return "border-amber-300 bg-amber-50 text-amber-950";
  }
  if (severity === "limitation") {
    return "border-slate-300 bg-slate-100 text-defer";
  }
  return "border-blue-200 bg-blue-50 text-blue-950";
}

function TraceUnavailable({ error }: { error?: string }) {
  return (
    <section className="clinical-panel border-l-[4px] border-l-review p-5 md:p-6">
      <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
        <div className="flex items-start gap-4">
          <WarningCircle className="mt-1 text-review" size={30} weight="duotone" />
          <div>
            <p className="label-caps text-review">Reasoning trace unavailable</p>
            <h3 className="mt-2 font-display text-2xl font-semibold tracking-tight text-ink">
              Decision and evidence remain available
            </h3>
            <p className="mt-3 max-w-[72ch] text-sm leading-6 text-ink-muted">
              The biological reasoning trace is a deterministic V2 overlay. The persisted decision, artifacts, and
              evidence tables stay visible if this read-only endpoint needs another pass.
            </p>
          </div>
        </div>
        {error ? (
          <code className="max-w-full overflow-x-auto rounded border border-line bg-surface-muted px-3 py-2 font-data text-xs text-ink-muted md:max-w-md">
            {error}
          </code>
        ) : null}
      </div>
    </section>
  );
}

function coverageWidth(trace: ReasoningTrace): string {
  const bounded = Math.max(0, Math.min(1, trace.coverage.coverage_ratio));
  return `${Math.round(bounded * 100)}%`;
}

function EvidenceChips({ step }: { step: ReasoningTraceStep }) {
  return (
    <div className="mt-4 flex flex-wrap gap-2">
      {step.evidence_refs.map((ref) => (
        <span
          className="inline-flex max-w-full items-center gap-2 rounded border border-line bg-white px-2.5 py-1 font-data text-[0.66rem] font-bold uppercase tracking-[0.1em] text-ink-muted"
          key={`${step.step_number}-${ref.evidence_id}`}
          title={ref.detail ?? ref.evidence_id}
        >
          <LinkSimple size={12} weight="bold" />
          <span className="truncate">{ref.evidence_id}</span>
          {ref.label ? <span className="hidden text-ink sm:inline">{ref.label}</span> : null}
        </span>
      ))}
    </div>
  );
}

function CaveatList({
  caveats,
  ids,
}: {
  caveats: Map<string, ReasoningTraceCaveat>;
  ids: string[];
}) {
  const matched = ids.map((id) => caveats.get(id)).filter((item): item is ReasoningTraceCaveat => Boolean(item));

  if (matched.length === 0) {
    return null;
  }

  return (
    <div className="mt-4 grid gap-2">
      {matched.map((caveat) => (
        <div className={`rounded border p-3 ${caveatTone(caveat.severity)}`} key={caveat.caveat_id}>
          <div className="flex flex-wrap items-center gap-2">
            <span className="label-caps">{humanizeToken(caveat.severity)}</span>
            <span className="font-display text-sm font-semibold tracking-tight">{caveat.title}</span>
          </div>
          <p className="mt-2 text-xs leading-5">{caveat.detail}</p>
        </div>
      ))}
    </div>
  );
}

function TraceStepCard({
  caveats,
  step,
}: {
  caveats: Map<string, ReasoningTraceCaveat>;
  step: ReasoningTraceStep;
}) {
  return (
    <article className="grid gap-4 border-b border-line py-5 last:border-b-0 md:grid-cols-[4.25rem_minmax(0,1fr)]">
      <div className="flex items-start gap-3 md:block">
        <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full border border-line bg-surface-muted font-display text-lg font-bold text-ink">
          {step.step_number}
        </div>
        <div className="mt-1 h-full min-h-8 w-px bg-line md:mx-auto md:mt-3" />
      </div>

      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <span
            className={`rounded border px-2.5 py-1 font-data text-[0.66rem] font-bold uppercase tracking-[0.14em] ${statusTone(
              step.status,
            )}`}
          >
            {humanizeToken(step.status)}
          </span>
          <span className="label-caps text-ink-muted">{humanizeToken(step.step_type)}</span>
        </div>
        <h4 className="mt-3 font-display text-xl font-semibold tracking-tight text-ink">{step.title}</h4>
        <p className="mt-3 max-w-[88ch] text-sm leading-6 text-ink-muted">{step.text}</p>
        <EvidenceChips step={step} />
        <CaveatList caveats={caveats} ids={step.caveat_ids} />
      </div>
    </article>
  );
}

export function ReasoningTracePanel({ bundle }: { bundle: CaseBundle }) {
  const trace = bundle.reasoningTrace;

  if (!trace) {
    return <TraceUnavailable error={bundle.reasoningTraceError} />;
  }

  const caveatsById = new Map(trace.caveats.map((caveat) => [caveat.caveat_id, caveat]));
  const providerCallsTriggered = trace.metadata.provider_calls_triggered === true;
  const builder =
    typeof trace.metadata.builder === "string" ? trace.metadata.builder : "deterministic_reasoning_trace_builder";

  return (
    <section className="clinical-panel overflow-hidden">
      <div className="border-b border-line bg-gradient-to-br from-surface-muted via-white to-white p-5 md:p-6">
        <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_22rem] xl:items-start">
          <div className="flex min-w-0 gap-4">
            <div className="mt-1 text-defer">
              <GitBranch size={36} weight="duotone" />
            </div>
            <div className="min-w-0">
              <p className="label-caps text-ink-muted">BioReason-style trace</p>
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <span
                  className={`rounded border px-3 py-1.5 font-data text-xs font-bold uppercase tracking-[0.18em] ${triageClass(
                    trace.decision,
                  )}`}
                >
                  {triageLabel(trace.decision)}
                </span>
                <span className={`font-data text-xs font-bold uppercase tracking-[0.16em] ${severityClass(trace.severity)}`}>
                  Severity: {humanizeToken(trace.severity)}
                </span>
                <span className="rounded border border-line bg-white px-3 py-1.5 font-data text-xs font-bold uppercase tracking-[0.14em] text-ink-muted">
                  {formatCount(trace.coverage.present_steps)}/{formatCount(trace.coverage.required_steps)} steps
                </span>
              </div>
              <h3 className="mt-5 max-w-[18ch] font-display text-4xl font-bold leading-none tracking-[-0.045em] text-ink md:text-5xl">
                Why this case landed here
              </h3>
              <p className="mt-4 max-w-[82ch] text-sm leading-6 text-ink-muted">{trace.summary}</p>
            </div>
          </div>

          <aside className="rounded-xl border border-line bg-white/90 p-4">
            <div className="flex items-center gap-3">
              <ListChecks size={24} weight="duotone" className="text-defer" />
              <div>
                <p className="label-caps text-ink-muted">Trace coverage</p>
                <p className="mt-2 font-display text-2xl font-bold tracking-tight text-ink">
                  {formatPercent(trace.coverage.coverage_ratio, 0)}
                </p>
              </div>
            </div>
            <div className="mt-4 h-2 overflow-hidden rounded-full bg-surface-strong">
              <div className="h-full rounded-full bg-defer" style={{ width: coverageWidth(trace) }} />
            </div>
            <dl className="mt-5 grid grid-cols-2 gap-3 text-xs">
              <div className="rounded border border-line bg-surface-muted p-3">
                <dt className="label-caps text-ink-muted">Builder</dt>
                <dd className="mt-2 break-words font-data font-bold text-ink">{builder}</dd>
              </div>
              <div className="rounded border border-line bg-surface-muted p-3">
                <dt className="label-caps text-ink-muted">Provider calls</dt>
                <dd className="mt-2 font-data font-bold text-ink">
                  {providerCallsTriggered ? "Triggered" : "None"}
                </dd>
              </div>
              <div className="rounded border border-line bg-surface-muted p-3">
                <dt className="label-caps text-ink-muted">Caveats</dt>
                <dd className="mt-2 font-data font-bold text-ink">{formatCount(trace.caveats.length)}</dd>
              </div>
              <div className="rounded border border-line bg-surface-muted p-3">
                <dt className="label-caps text-ink-muted">Missing steps</dt>
                <dd className="mt-2 font-data font-bold text-ink">
                  {formatCount(trace.coverage.missing_step_types.length)}
                </dd>
              </div>
            </dl>
          </aside>
        </div>
      </div>

      <div className="grid gap-0 xl:grid-cols-[minmax(0,1fr)_20rem]">
        <div className="p-5 md:p-6">
          {trace.steps.map((step) => (
            <TraceStepCard caveats={caveatsById} key={step.step_type} step={step} />
          ))}
        </div>

        <aside className="border-t border-line bg-surface-muted/55 p-5 md:p-6 xl:border-l xl:border-t-0">
          <p className="label-caps text-ink-muted">Trace reading guide</p>
          <h4 className="mt-2 font-display text-xl font-semibold tracking-tight text-ink">
            Backend reasoning, not generated prose
          </h4>
          <p className="mt-3 text-sm leading-6 text-ink-muted">
            This panel is built directly from the persisted decision object. Copilot language appears later as a
            sidecar, after the deterministic evidence chain is already visible.
          </p>

          <div className="mt-5 grid gap-3">
            <div className="rounded border border-line bg-white p-3">
              <p className="label-caps text-ink-muted">Sample</p>
              <p className="mt-2 break-words font-data text-xs font-bold text-ink">{trace.sample_id}</p>
            </div>
            <div className="rounded border border-line bg-white p-3">
              <p className="label-caps text-ink-muted">Target drug</p>
              <p className="mt-2 font-data text-xs font-bold text-ink">{humanizeToken(trace.target_drug)}</p>
            </div>
            {trace.coverage.missing_step_types.length > 0 ? (
              <div className="rounded border border-amber-300 bg-amber-50 p-3 text-amber-950">
                <p className="label-caps">Missing trace steps</p>
                <p className="mt-2 text-xs leading-5">
                  {trace.coverage.missing_step_types.map((stepType) => humanizeToken(stepType)).join(", ")}
                </p>
              </div>
            ) : (
              <div className="rounded border border-emerald-200 bg-emerald-50 p-3 text-emerald-900">
                <p className="label-caps">Complete biological sequence</p>
                <p className="mt-2 text-xs leading-5">
                  All required sample, mechanism, novelty, QC, actionability, and triage steps are present.
                </p>
              </div>
            )}
          </div>
        </aside>
      </div>
    </section>
  );
}
