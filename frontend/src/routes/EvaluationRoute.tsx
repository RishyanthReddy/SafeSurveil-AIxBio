import {
  ArrowRight,
  BracketsCurly,
  CheckCircle,
  ClipboardText,
  Database,
  Gauge,
  ListChecks,
  Play,
  ShieldWarning,
  SquaresFour,
  WarningCircle,
} from "@phosphor-icons/react";
import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import {
  fetchArtifactManifest,
  fetchCopilotExplanation,
  fetchHealthStatus,
  fetchIntegrationHealth,
  fetchJobDecision,
  fetchJobStatus,
  fetchQueue,
  fetchThesysC1Render,
  fetchV2AuditBundle,
} from "../api/client";
import { formatCount, humanizeToken, triageLabel } from "../api/format";
import type {
  ArtifactManifest,
  CopilotOutputMode,
  HealthResponse,
  IntegrationHealthEntry,
  IntegrationHealthResponse,
  JobDecisionResponse,
  JobStatus,
  JobThesysC1Response,
  QueueItem,
  QueueSummaryResponse,
  V2AuditBundle,
  V2AuditSection,
  V2AuditStatus,
} from "../api/types";
import { ApiErrorState, EmptyState, TableSkeleton } from "../components/ApiState";
import { RouteHeader } from "../components/RouteHeader";
import { useApiResource } from "../hooks/useApiResource";

type ChecklistState = "pass" | "attention" | "fail" | "pending";

type ChecklistItem = {
  title: string;
  state: ChecklistState;
  detail: string;
};

type JobReadiness = {
  status: JobStatus;
  decision: JobDecisionResponse;
  artifacts: ArtifactManifest;
};

type ProviderProof =
  | { state: "idle" }
  | { state: "loading" }
  | {
      state: "complete";
      explanationMode?: CopilotOutputMode;
      c1?: JobThesysC1Response;
      errors: string[];
    };

function stateTone(state: ChecklistState): string {
  if (state === "pass") {
    return "border-emerald-300 bg-emerald-50 text-emerald-900";
  }
  if (state === "attention") {
    return "border-amber-300 bg-amber-50 text-amber-950";
  }
  if (state === "fail") {
    return "border-red-300 bg-red-50 text-act";
  }
  return "border-slate-300 bg-slate-100 text-ink-muted";
}

function stateLabel(state: ChecklistState): string {
  if (state === "pass") {
    return "Pass";
  }
  if (state === "attention") {
    return "Attention";
  }
  if (state === "fail") {
    return "Fail";
  }
  return "Pending";
}

function statusFromConfigured(entry: IntegrationHealthEntry | undefined): ChecklistState {
  if (!entry) {
    return "fail";
  }
  return entry.status === "configured" || entry.status === "available" ? "pass" : "attention";
}

function selectedQueueItem(queue: QueueSummaryResponse | null): QueueItem | null {
  return queue?.items[0] ?? null;
}

function resolveSelectedQueueItem(
  queue: QueueSummaryResponse | null,
  requestedJobId: string | null,
): QueueItem | null {
  if (!queue) {
    return null;
  }
  if (requestedJobId) {
    return queue.items.find((item) => item.job_id === requestedJobId) ?? null;
  }
  return selectedQueueItem(queue);
}

function jobReadyState(job: JobReadiness | null): ChecklistState {
  if (!job) {
    return "pending";
  }
  if (job.status.status === "failed") {
    return "fail";
  }
  if (job.status.status === "degraded") {
    return "attention";
  }
  return "pass";
}

function providerProofChecklist(proof: ProviderProof): ChecklistItem[] {
  if (proof.state === "idle") {
    return [
      {
        title: "Live provider proof",
        state: "pending",
        detail: "Select a queue job to run a fresh live explanation proof and a live Thesys C1 render.",
      },
    ];
  }
  if (proof.state === "loading") {
    return [
      {
        title: "Live provider proof",
        state: "pending",
        detail: "Provider proof is running against the selected job with a fresh live explanation request and a live Thesys C1 render.",
      },
    ];
  }

  const explanationPassed = proof.explanationMode === "live_llm";
  const c1Passed = proof.c1?.status === "rendered" && proof.c1.fallback_required === false;
  return [
    {
      title: "Copilot explanation output",
      state: explanationPassed ? "pass" : proof.explanationMode ? "attention" : "fail",
      detail: proof.explanationMode
        ? `Output origin: ${humanizeToken(proof.explanationMode)}`
        : "Copilot explanation did not return a usable output origin.",
    },
    {
      title: "Thesys C1 render",
      state: c1Passed ? "pass" : proof.c1 ? "attention" : "fail",
      detail: proof.c1
        ? `C1 status: ${humanizeToken(proof.c1.status)}; fallback required: ${proof.c1.fallback_required ? "yes" : "no"}`
        : "C1 render did not return a response.",
    },
    ...proof.errors.map((error) => ({
      title: "Provider proof error",
      state: "fail" as const,
      detail: error,
    })),
  ];
}

function ChecklistRow({ item }: { item: ChecklistItem }) {
  const Icon =
    item.state === "pass"
      ? CheckCircle
      : item.state === "fail"
        ? WarningCircle
        : item.state === "attention"
          ? ShieldWarning
          : ClipboardText;

  return (
    <div className="grid gap-4 p-5 md:grid-cols-[12rem_1fr] md:items-start">
      <div className="flex items-center gap-3">
        <Icon size={20} weight="duotone" className="text-ink" />
        <span
          className={`rounded border px-2.5 py-1 font-data text-[0.7rem] font-bold uppercase tracking-[0.14em] ${stateTone(item.state)}`}
        >
          {stateLabel(item.state)}
        </span>
      </div>
      <div>
        <h3 className="font-display text-base font-semibold tracking-tight text-ink">{item.title}</h3>
        <p className="mt-1 text-sm leading-6 text-ink-muted">{item.detail}</p>
      </div>
    </div>
  );
}

function MetricTile({
  label,
  value,
  detail,
  tone,
}: {
  label: string;
  value: string;
  detail: string;
  tone: ChecklistState;
}) {
  return (
    <article className="clinical-panel p-5">
      <p className="label-caps text-ink-muted">{label}</p>
      <div className="mt-4 flex items-end justify-between gap-4">
        <p className="font-display text-3xl font-bold leading-none tracking-[-0.04em] text-ink">{value}</p>
        <span
          className={`rounded border px-2.5 py-1 font-data text-[0.68rem] font-bold uppercase tracking-[0.14em] ${stateTone(tone)}`}
        >
          {stateLabel(tone)}
        </span>
      </div>
      <p className="mt-3 text-sm leading-6 text-ink-muted">{detail}</p>
    </article>
  );
}

function auditTone(status: V2AuditStatus): ChecklistState {
  if (status === "pass") {
    return "pass";
  }
  if (status === "fail") {
    return "fail";
  }
  if (status === "warn") {
    return "attention";
  }
  return "pending";
}

function auditSection(audit: V2AuditBundle, sectionId: V2AuditSection["section_id"]): V2AuditSection | null {
  return audit.sections.find((section) => section.section_id === sectionId) ?? null;
}

function auditMetadataNumber(audit: V2AuditBundle, key: string): number | null {
  const value = audit.metadata[key];
  return typeof value === "number" ? value : null;
}

function auditMetadataString(audit: V2AuditBundle, key: string): string | null {
  const value = audit.metadata[key];
  return typeof value === "string" ? value : null;
}

function formatRatio(value: number | null): string {
  if (value === null) {
    return "Unavailable";
  }
  return `${Math.round(value * 100)}%`;
}

function AuditStatusBadge({ status }: { status: V2AuditStatus }) {
  return (
    <span
      className={`rounded border px-2.5 py-1 font-data text-[0.68rem] font-bold uppercase tracking-[0.14em] ${stateTone(
        auditTone(status),
      )}`}
    >
      {status === "warn" ? "Attention" : humanizeToken(status)}
    </span>
  );
}

function AuditProofTile({
  label,
  value,
  detail,
  status,
}: {
  label: string;
  value: string;
  detail: string;
  status: V2AuditStatus;
}) {
  return (
    <article className="rounded border border-line bg-white p-5 shadow-[0_18px_34px_-28px_rgba(15,23,42,0.45)]">
      <div className="flex items-start justify-between gap-4">
        <p className="label-caps text-ink-muted">{label}</p>
        <AuditStatusBadge status={status} />
      </div>
      <p className="mt-4 font-display text-2xl font-semibold tracking-[-0.04em] text-ink">{value}</p>
      <p className="mt-3 text-sm leading-6 text-ink-muted">{detail}</p>
    </article>
  );
}

function AuditSectionStrip({ section }: { section: V2AuditSection }) {
  return (
    <div className="grid gap-4 border-t border-line p-5 lg:grid-cols-[12rem_1fr] lg:items-start">
      <div className="flex items-center gap-3">
        <AuditStatusBadge status={section.status} />
        <span className="font-data text-[0.7rem] uppercase tracking-[0.16em] text-ink-muted">
          {section.checks.length} checks
        </span>
      </div>
      <div>
        <h3 className="font-display text-base font-semibold tracking-tight text-ink">{section.title}</h3>
        <p className="mt-1 text-sm leading-6 text-ink-muted">{section.summary}</p>
        <div className="mt-3 flex flex-wrap gap-2">
          {section.checks.slice(0, 4).map((check) => (
            <span
              className={`rounded border px-2.5 py-1 font-data text-[0.65rem] font-bold uppercase tracking-[0.13em] ${stateTone(
                auditTone(check.status),
              )}`}
              key={check.check_id}
              title={check.detail}
            >
              {humanizeToken(check.title)}
            </span>
          ))}
          {section.checks.length > 4 ? (
            <span className="rounded border border-line bg-surface-muted px-2.5 py-1 font-data text-[0.65rem] font-bold uppercase tracking-[0.13em] text-ink-muted">
              +{section.checks.length - 4} more
            </span>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function providerProofState(proof: ProviderProof): ChecklistState {
  if (proof.state === "loading" || proof.state === "idle") {
    return "pending";
  }
  if (proof.errors.length > 0) {
    return "fail";
  }
  if (proof.explanationMode === "live_llm" && proof.c1?.status === "rendered" && proof.c1.fallback_required === false) {
    return "pass";
  }
  return "attention";
}

function V2AuditBoard({ audit, proof }: { audit: V2AuditBundle; proof: ProviderProof }) {
  const executionGate = auditSection(audit, "execution_gate");
  const reasoningTrace = auditSection(audit, "reasoning_trace");
  const evidenceGraph = auditSection(audit, "evidence_graph");
  const artifactCoverage = auditSection(audit, "artifact_coverage");
  const openrouterProof = auditSection(audit, "openrouter_proof");
  const thesysProof = auditSection(audit, "thesys_proof");
  const fingerprint = auditMetadataString(audit, "execution_gate_audit_fingerprint");
  const traceCoverage = auditMetadataNumber(audit, "reasoning_trace_coverage_ratio");
  const graphCompleteness = auditMetadataNumber(audit, "evidence_graph_completeness_ratio");
  const artifactCount = auditMetadataNumber(audit, "artifact_count");
  const proofTone = providerProofState(proof);

  return (
    <section className="clinical-panel overflow-hidden">
      <div className="grid gap-5 border-b border-line bg-surface-muted p-6 xl:grid-cols-[minmax(0,1.2fr)_minmax(18rem,0.8fr)] xl:items-end">
        <div>
          <p className="label-caps text-ink-muted">V2 audit bundle</p>
          <h2 className="mt-2 font-display text-3xl font-semibold tracking-[-0.05em] text-ink">
            Runtime gate, reasoning, graph, and provenance proof
          </h2>
          <p className="mt-3 max-w-[74ch] text-sm leading-6 text-ink-muted">
            This board is fed by <code className="font-data">/jobs/{audit.job_id}/v2-audit</code>. It is intentionally
            read-only: verifier, trace, graph, artifacts, and provenance are checked here, while expensive OpenRouter
            and Thesys proof stays explicit below.
          </p>
        </div>
        <div className="rounded border border-line bg-white p-4">
          <div className="flex items-center justify-between gap-4">
            <span className="label-caps text-ink-muted">Overall audit state</span>
            <AuditStatusBadge status={audit.summary.overall_status} />
          </div>
          <div className="mt-4 grid grid-cols-4 gap-2 text-center">
            <div>
              <p className="font-display text-2xl font-bold tracking-[-0.04em] text-ink">
                {audit.summary.passing_checks}
              </p>
              <p className="font-data text-[0.62rem] uppercase tracking-[0.13em] text-ink-muted">Pass</p>
            </div>
            <div>
              <p className="font-display text-2xl font-bold tracking-[-0.04em] text-amber-700">
                {audit.summary.warning_checks}
              </p>
              <p className="font-data text-[0.62rem] uppercase tracking-[0.13em] text-ink-muted">Warn</p>
            </div>
            <div>
              <p className="font-display text-2xl font-bold tracking-[-0.04em] text-act">
                {audit.summary.failed_checks}
              </p>
              <p className="font-data text-[0.62rem] uppercase tracking-[0.13em] text-ink-muted">Fail</p>
            </div>
            <div>
              <p className="font-display text-2xl font-bold tracking-[-0.04em] text-ink-muted">
                {audit.summary.pending_checks}
              </p>
              <p className="font-data text-[0.62rem] uppercase tracking-[0.13em] text-ink-muted">Pending</p>
            </div>
          </div>
        </div>
      </div>

      <div className="grid gap-5 p-5 xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
        <div className="rounded border border-line bg-white p-5">
          <p className="label-caps text-ink-muted">Live provenance split</p>
          <h3 className="mt-2 font-display text-2xl font-semibold tracking-[-0.04em] text-ink">
            {audit.provenance.provenance_split_label}
          </h3>
          <p className="mt-3 text-sm leading-6 text-ink-muted">{audit.provenance.detail}</p>
          <div className="mt-5 grid gap-3 sm:grid-cols-2">
            <div className="rounded border border-line bg-surface-muted p-3">
              <p className="label-caps text-ink-muted">Input</p>
              <p className="mt-1 font-data text-sm font-bold text-ink">
                {humanizeToken(audit.provenance.input_provenance)}
              </p>
            </div>
            <div className="rounded border border-line bg-surface-muted p-3">
              <p className="label-caps text-ink-muted">Baseline</p>
              <p className="mt-1 font-data text-sm font-bold text-ink">
                {humanizeToken(audit.provenance.baseline_provenance)}
              </p>
            </div>
            <div className="rounded border border-line bg-surface-muted p-3">
              <p className="label-caps text-ink-muted">Source context</p>
              <p className="mt-1 font-data text-sm font-bold text-ink">
                {humanizeToken(audit.provenance.source_context)}
              </p>
            </div>
            <div className="rounded border border-line bg-surface-muted p-3">
              <p className="label-caps text-ink-muted">Split context</p>
              <p className="mt-1 font-data text-sm font-bold text-ink">
                {humanizeToken(audit.provenance.split_context)}
              </p>
            </div>
          </div>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <AuditProofTile
            label="Execution gate"
            value={executionGate ? humanizeToken(executionGate.status) : "Unavailable"}
            detail={executionGate?.summary ?? "Execution gate section is missing from the audit bundle."}
            status={executionGate?.status ?? "fail"}
          />
          <AuditProofTile
            label="Reasoning trace"
            value={formatRatio(traceCoverage)}
            detail={reasoningTrace?.summary ?? "Reasoning trace section is missing from the audit bundle."}
            status={reasoningTrace?.status ?? "fail"}
          />
          <AuditProofTile
            label="Evidence graph"
            value={formatRatio(graphCompleteness)}
            detail={evidenceGraph?.summary ?? "Evidence graph section is missing from the audit bundle."}
            status={evidenceGraph?.status ?? "fail"}
          />
          <AuditProofTile
            label="Artifact coverage"
            value={artifactCount === null ? "Unavailable" : `${artifactCount} artifacts`}
            detail={artifactCoverage?.summary ?? "Artifact coverage section is missing from the audit bundle."}
            status={artifactCoverage?.status ?? "fail"}
          />
        </div>
      </div>

      <div className="grid gap-5 border-t border-line p-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div className="rounded border border-line bg-surface-muted p-5">
          <div className="flex items-center justify-between gap-4">
            <p className="label-caps text-ink-muted">Audit fingerprint</p>
            <AuditStatusBadge status={executionGate?.status ?? "fail"} />
          </div>
          <code className="mt-4 block break-all rounded border border-line bg-white px-3 py-3 font-data text-xs leading-6 text-ink">
            {fingerprint ?? "Fingerprint unavailable"}
          </code>
        </div>
        <div className="rounded border border-line bg-surface-muted p-5">
          <div className="flex items-center justify-between gap-4">
            <p className="label-caps text-ink-muted">Provider proof boundary</p>
            <span
              className={`rounded border px-2.5 py-1 font-data text-[0.68rem] font-bold uppercase tracking-[0.14em] ${stateTone(
                proofTone,
              )}`}
            >
              Explicit proof {stateLabel(proofTone)}
            </span>
          </div>
          <p className="mt-4 text-sm leading-6 text-ink-muted">
            The audit bundle reports OpenRouter as {openrouterProof?.status ?? "missing"} and Thesys as{" "}
            {thesysProof?.status ?? "missing"} because the endpoint does not trigger hidden provider calls. The button
            below runs those live checks intentionally.
          </p>
        </div>
      </div>

      <div className="divide-y divide-line">
        {audit.sections.map((section) => (
          <AuditSectionStrip key={section.section_id} section={section} />
        ))}
      </div>
    </section>
  );
}

function ShellCommand({ label, command }: { label: string; command: string }) {
  return (
    <div className="grid gap-3 border-t border-line p-4 md:grid-cols-[10rem_1fr] md:items-center">
      <span className="label-caps text-ink-muted">{label}</span>
      <code className="overflow-x-auto rounded border border-line bg-surface-muted px-3 py-2 font-data text-xs text-ink">
        {command}
      </code>
    </div>
  );
}

async function loadJobReadiness(jobId: string, signal: AbortSignal): Promise<JobReadiness> {
  const [status, decision, artifacts] = await Promise.all([
    fetchJobStatus(jobId, signal),
    fetchJobDecision(jobId, signal),
    fetchArtifactManifest(jobId, signal),
  ]);
  return { status, decision, artifacts };
}

async function loadProviderProof(jobId: string, signal?: AbortSignal): Promise<ProviderProof> {
  const [explanation, c1] = await Promise.allSettled([
    fetchCopilotExplanation(jobId, { refresh: true, signal }),
    fetchThesysC1Render(jobId, signal),
  ]);
  const errors: string[] = [];
  let explanationMode: CopilotOutputMode | undefined;
  let c1Response: JobThesysC1Response | undefined;

  if (explanation.status === "fulfilled") {
    explanationMode = explanation.value.output_origin.mode;
  } else {
    errors.push(explanation.reason instanceof Error ? explanation.reason.message : "Copilot proof failed.");
  }

  if (c1.status === "fulfilled") {
    c1Response = c1.value;
  } else {
    errors.push(c1.reason instanceof Error ? c1.reason.message : "C1 proof failed.");
  }

  return {
    state: "complete",
    explanationMode,
    c1: c1Response,
    errors,
  };
}

function buildChecklist({
  health,
  integrations,
  queue,
  job,
  proof,
  selectedJobId,
  selectedQueueJob,
}: {
  health: HealthResponse;
  integrations: IntegrationHealthResponse;
  queue: QueueSummaryResponse;
  job: JobReadiness | null;
  proof: ProviderProof;
  selectedJobId: string | null;
  selectedQueueJob: QueueItem | null;
}): ChecklistItem[] {
  const llmState = statusFromConfigured(integrations.external_apis.llm);
  const c1State = statusFromConfigured(integrations.external_apis.thesys);
  const previewableArtifacts = job?.artifacts.artifacts.filter((artifact) => artifact.preview_eligible).length ?? 0;
  const checklist: ChecklistItem[] = [
    {
      title: "API health endpoint",
      state: health.status === "ok" ? "pass" : "fail",
      detail: `/health reports ${humanizeToken(health.status)} for environment ${health.runtime.app_env}.`,
    },
    {
      title: "Live operating mode",
      state: health.runtime.live_mode_ready ? "pass" : "attention",
      detail:
        health.runtime.live_mode_blockers.length === 0
          ? "No demo, fixture, or mock LLM blockers are reported by the backend runtime."
          : `Runtime blockers: ${health.runtime.live_mode_blockers.map(humanizeToken).join(", ")}.`,
    },
    {
      title: "Integration readiness",
      state: integrations.status === "ready" ? "pass" : "attention",
      detail: `/health/integrations reports ${humanizeToken(integrations.status)} in ${humanizeToken(integrations.mode)} mode.`,
    },
    {
      title: "Queue population",
      state: queue.items.length > 0 ? "pass" : "attention",
      detail:
        queue.items.length > 0
          ? `${formatCount(queue.items.length)} queue records are available for operator review.`
          : "No queue records are available yet. Submit a live analysis before the final acceptance run.",
    },
    {
      title: "Selected job persistence",
      state: selectedJobId ? jobReadyState(job) : "pending",
      detail: selectedJobId
        ? selectedQueueJob
          ? `Selected ${selectedJobId} with ${humanizeToken(selectedQueueJob.status)} status and ${triageLabel(selectedQueueJob.triage)} triage.`
          : `Selected ${selectedJobId} by explicit route parameter for status, decision, and artifact checks.`
        : "No selected queue job is available for status, decision, and artifact checks.",
    },
    {
      title: "Decision object",
      state: job?.decision.decision ? "pass" : selectedJobId ? "fail" : "pending",
      detail: job?.decision.decision
        ? `Decision object loaded for ${job.decision.decision.sample.sample_id} and ${job.decision.decision.sample.target_drug}.`
        : "Decision object has not been verified for a queue job.",
    },
    {
      title: "Artifact manifest and preview candidates",
      state: job ? (previewableArtifacts > 0 ? "pass" : "attention") : "pending",
      detail: job
        ? `${formatCount(job.artifacts.artifacts.length)} artifacts loaded; ${formatCount(previewableArtifacts)} are preview eligible.`
        : "Artifact manifest has not been checked yet.",
    },
    {
      title: "Copilot live mode configured",
      state: health.runtime.llm_mode === "live" && llmState === "pass" ? "pass" : "attention",
      detail: `Runtime LLM mode is ${humanizeToken(health.runtime.llm_mode)}; provider configuration is ${humanizeToken(String(integrations.external_apis.llm?.status ?? "missing"))}.`,
    },
    {
      title: "Thesys C1 configured",
      state: c1State,
      detail: `Thesys readiness is ${humanizeToken(String(integrations.external_apis.thesys?.status ?? "missing"))}.`,
    },
  ];

  return [...checklist, ...providerProofChecklist(proof)];
}

export function EvaluationRoute() {
  const [searchParams] = useSearchParams();
  const requestedJobId = searchParams.get("jobId");
  const health = useApiResource((signal) => fetchHealthStatus(signal), []);
  const integrations = useApiResource((signal) => fetchIntegrationHealth(signal), []);
  const queue = useApiResource((signal) => fetchQueue({ limit: 25, signal }), []);
  const selectedJob = queue.status === "success" ? resolveSelectedQueueItem(queue.data, requestedJobId) : null;
  const selectedJobId = requestedJobId ?? selectedJob?.job_id ?? null;
  const jobReadiness = useApiResource(
    (signal) => {
      if (!selectedJobId) {
        return Promise.resolve(null);
      }
      return loadJobReadiness(selectedJobId, signal);
    },
    [selectedJobId ?? "no-job"],
  );
  const auditBundle = useApiResource(
    (signal) => {
      if (!selectedJobId) {
        return Promise.resolve(null);
      }
      return fetchV2AuditBundle(selectedJobId, signal);
    },
    [selectedJobId ?? "no-job"],
  );
  const [proof, setProof] = useState<ProviderProof>({ state: "idle" });

  useEffect(() => {
    if (!selectedJobId) {
      setProof({ state: "idle" });
      return;
    }

    const controller = new AbortController();
    setProof({ state: "loading" });
    const kickoff = window.setTimeout(() => {
      void loadProviderProof(selectedJobId, controller.signal)
        .then((nextProof) => {
          if (!controller.signal.aborted) {
            setProof(nextProof);
          }
        })
        .catch((error: unknown) => {
          if (controller.signal.aborted) {
            return;
          }
          const message = error instanceof Error ? error.message : "Provider proof failed.";
          setProof({
            state: "complete",
            errors: [message],
          });
        });
    }, 0);

    return () => {
      window.clearTimeout(kickoff);
      controller.abort();
    };
  }, [selectedJobId]);

  const checklist = useMemo(() => {
    if (
      health.status !== "success"
      || integrations.status !== "success"
      || queue.status !== "success"
      || jobReadiness.status !== "success"
    ) {
      return [];
    }
    return buildChecklist({
      health: health.data,
      integrations: integrations.data,
      queue: queue.data,
      job: jobReadiness.data,
      proof,
      selectedJobId,
      selectedQueueJob: selectedJob,
    });
  }, [health, integrations, queue, jobReadiness, proof, selectedJob, selectedJobId]);

  const passCount = checklist.filter((item) => item.state === "pass").length;
  const attentionCount = checklist.filter((item) => item.state === "attention").length;
  const failCount = checklist.filter((item) => item.state === "fail").length;
  const pendingCount = checklist.filter((item) => item.state === "pending").length;
  async function runProviderProof() {
    if (!selectedJobId) {
      return;
    }
    setProof({ state: "loading" });
    setProof(await loadProviderProof(selectedJobId));
  }

  const isLoading =
    health.status === "loading"
    || integrations.status === "loading"
    || queue.status === "loading"
    || jobReadiness.status === "loading";
  const auditIsLoading = auditBundle.status === "loading";

  const firstError =
    health.status === "error"
      ? { title: "API health did not load", message: health.error }
      : integrations.status === "error"
        ? { title: "Integration readiness did not load", message: integrations.error }
        : queue.status === "error"
          ? { title: "Queue did not load", message: queue.error }
          : jobReadiness.status === "error"
            ? { title: "Selected job checks did not load", message: jobReadiness.error }
            : null;

  return (
    <>
      <RouteHeader
        eyebrow="Phase 8 + V2 / acceptance"
        title="Live operator checklist and V2 audit board"
        description="This route combines health, integration, queue, provider proof, and the V2 audit bundle into one screenshot-ready sign-off surface."
      />

      {isLoading ? <TableSkeleton rows={6} /> : null}

      {firstError ? <ApiErrorState title={firstError.title} message={firstError.message} /> : null}

      {!isLoading && !firstError && queue.status === "success" && queue.data.items.length === 0 ? (
        <EmptyState
          title="No queue job is available for acceptance"
          message="Submit a live analysis first, then return here to validate job, artifact, copilot, and C1 readiness."
        />
      ) : null}

      {checklist.length > 0 ? (
        <section className="grid gap-gutter">
          <div className="grid gap-gutter md:grid-cols-2 xl:grid-cols-4">
            <MetricTile
              label="Passing checks"
              value={formatCount(passCount)}
              detail="Checks backed by live backend responses and currently acceptable."
              tone={passCount > 0 ? "pass" : "pending"}
            />
            <MetricTile
              label="Attention checks"
              value={formatCount(attentionCount)}
              detail="Non-blocking or degraded states that must be understood before sign-off."
              tone={attentionCount > 0 ? "attention" : "pass"}
            />
            <MetricTile
              label="Failed checks"
              value={formatCount(failCount)}
              detail="Blocking failures returned by the current backend or provider proof."
              tone={failCount > 0 ? "fail" : "pass"}
            />
            <MetricTile
              label="Pending checks"
              value={formatCount(pendingCount)}
              detail="Checks waiting for a live job or explicit provider-proof run."
              tone={pendingCount > 0 ? "pending" : "pass"}
            />
          </div>

          {auditIsLoading ? <TableSkeleton rows={3} /> : null}

          {auditBundle.status === "error" ? (
            <ApiErrorState
              title="V2 audit bundle did not load"
              message={`${auditBundle.error} Core Phase 8 checks remain visible below, but V2 closure needs /v2-audit.`}
            />
          ) : null}

          {auditBundle.status === "success" && auditBundle.data ? (
            <V2AuditBoard audit={auditBundle.data} proof={proof} />
          ) : null}

          <section className="clinical-panel overflow-hidden">
            <div className="grid gap-4 border-b border-line bg-surface-muted p-5 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
              <div>
                <p className="label-caps text-ink-muted">Selected acceptance job</p>
                <h2 className="mt-2 font-display text-2xl font-semibold tracking-tight text-ink">
                  {selectedJobId ?? "No queue job selected"}
                </h2>
                <p className="mt-2 text-sm leading-6 text-ink-muted">
                  The route automatically checks health, queue, job status, decision, and artifact manifest, then runs
                  a fresh provider proof for the selected job. Use rerun when you want another live OpenRouter and
                  Thesys pass.
                </p>
              </div>
              <div className="flex flex-wrap gap-3">
                <Link
                  className="inline-flex items-center gap-2 rounded border border-line bg-white px-4 py-2.5 text-xs font-bold uppercase tracking-[0.14em] text-ink transition hover:border-slate-700 active:scale-[0.98]"
                  to={selectedJobId ? `/cases/${selectedJobId}` : "/analysis/new"}
                >
                  {selectedJobId ? "Open Case" : "New Analysis"}
                  <ArrowRight size={16} weight="bold" />
                </Link>
                <button
                  className="inline-flex items-center gap-2 rounded bg-ink px-4 py-2.5 text-xs font-bold uppercase tracking-[0.14em] text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50 active:scale-[0.98]"
                  disabled={!selectedJobId || proof.state === "loading"}
                  onClick={runProviderProof}
                  type="button"
                >
                  <Play size={16} weight="bold" />
                  {proof.state === "loading" ? "Running Proof" : "Rerun Provider Proof"}
                </button>
              </div>
            </div>

            <div className="divide-y divide-line">
              {checklist.map((item) => (
                <ChecklistRow item={item} key={`${item.title}-${item.detail}`} />
              ))}
            </div>
          </section>

          <section className="grid gap-gutter xl:grid-cols-[0.9fr_1.1fr]">
            <div className="clinical-panel overflow-hidden">
              <div className="border-b border-line bg-surface-muted p-5">
                <div className="flex items-center gap-3">
                  <ListChecks size={20} weight="duotone" className="text-ink" />
                  <div>
                    <p className="label-caps text-ink-muted">Human checklist</p>
                    <h2 className="mt-1 font-display text-xl font-semibold tracking-tight text-ink">
                      Screenshot and route sequence
                    </h2>
                  </div>
                </div>
              </div>
              <div className="divide-y divide-line">
                {[
                  { label: "Dashboard", to: "/", text: "Confirm readiness and queue counts load from the backend." },
                  {
                    label: "Queue",
                    to: selectedJobId ? `/queue?q=${encodeURIComponent(selectedJobId)}` : "/queue",
                    text: "Confirm the selected job appears and opens case detail.",
                  },
                  {
                    label: "Case detail",
                    to: selectedJobId ? `/cases/${selectedJobId}` : "/analysis/new",
                    text: "Confirm decision, evidence, risk, copilot, and fallback semantic UI render.",
                  },
                  {
                    label: "Fallback renderer",
                    to: selectedJobId ? `/fallback-renderer?jobId=${encodeURIComponent(selectedJobId)}` : "/fallback-renderer",
                    text: "Confirm React fallback preserves grounded semantic UI content.",
                  },
                  {
                    label: "Evaluation",
                    to: selectedJobId ? `/evaluation?jobId=${encodeURIComponent(selectedJobId)}` : "/evaluation",
                    text: "Capture this checklist after provider proof is complete.",
                  },
                ].map((item) => (
                  <Link
                    className="grid gap-3 p-5 transition hover:bg-surface-muted/70 active:scale-[0.99] md:grid-cols-[10rem_1fr_auto] md:items-center"
                    key={item.label}
                    to={item.to}
                  >
                    <span className="label-caps text-ink">{item.label}</span>
                    <span className="text-sm leading-6 text-ink-muted">{item.text}</span>
                    <ArrowRight size={16} weight="bold" className="text-ink-muted" />
                  </Link>
                ))}
              </div>
            </div>

            <div className="clinical-panel overflow-hidden">
              <div className="border-b border-line bg-surface-muted p-5">
                <div className="flex items-center gap-3">
                  <Gauge size={20} weight="duotone" className="text-ink" />
                  <div>
                    <p className="label-caps text-ink-muted">Curl matrix</p>
                    <h2 className="mt-1 font-display text-xl font-semibold tracking-tight text-ink">
                      Backend checks to mirror in terminal
                    </h2>
                  </div>
                </div>
              </div>
              <ShellCommand label="API" command="curl.exe http://127.0.0.1:8001/health" />
              <ShellCommand label="Integrations" command="curl.exe http://127.0.0.1:8001/health/integrations" />
              <ShellCommand label="Queue" command="curl.exe http://127.0.0.1:8001/queue" />
              <ShellCommand
                label="Job status"
                command={
                  selectedJobId
                    ? `curl.exe http://127.0.0.1:8001/jobs/${selectedJobId}/status`
                    : "curl.exe http://127.0.0.1:8001/jobs/{job_id}/status"
                }
              />
              <ShellCommand
                label="Decision"
                command={
                  selectedJobId
                    ? `curl.exe http://127.0.0.1:8001/jobs/${selectedJobId}/decision`
                    : "curl.exe http://127.0.0.1:8001/jobs/{job_id}/decision"
                }
              />
              <ShellCommand
                label="Artifacts"
                command={
                  selectedJobId
                    ? `curl.exe http://127.0.0.1:8001/jobs/${selectedJobId}/artifacts`
                    : "curl.exe http://127.0.0.1:8001/jobs/{job_id}/artifacts"
                }
              />
              <ShellCommand
                label="V2 audit"
                command={
                  selectedJobId
                    ? `curl.exe http://127.0.0.1:8001/jobs/${selectedJobId}/v2-audit`
                    : "curl.exe http://127.0.0.1:8001/jobs/{job_id}/v2-audit"
                }
              />
              <ShellCommand
                label="C1"
                command={
                  selectedJobId
                    ? `curl.exe http://127.0.0.1:8001/jobs/${selectedJobId}/semantic-ui/c1`
                    : "curl.exe http://127.0.0.1:8001/jobs/{job_id}/semantic-ui/c1"
                }
              />
            </div>
          </section>

          <section className="grid gap-gutter md:grid-cols-3">
            <div className="clinical-panel p-5">
              <Database size={20} weight="duotone" className="text-ink" />
              <p className="mt-4 label-caps text-ink-muted">Data source rule</p>
              <p className="mt-2 text-sm leading-6 text-ink-muted">
                Every status on this route comes from the backend or an explicit provider-proof result.
              </p>
            </div>
            <div className="clinical-panel p-5">
              <BracketsCurly size={20} weight="duotone" className="text-ink" />
              <p className="mt-4 label-caps text-ink-muted">Copilot rule</p>
              <p className="mt-2 text-sm leading-6 text-ink-muted">
                Phase 8 closure still requires a provider proof where copilot output origin is live LLM.
              </p>
            </div>
            <div className="clinical-panel p-5">
              <SquaresFour size={20} weight="duotone" className="text-ink" />
              <p className="mt-4 label-caps text-ink-muted">C1 rule</p>
              <p className="mt-2 text-sm leading-6 text-ink-muted">
                Final sign-off requires C1 status rendered with fallback required set to no.
              </p>
            </div>
          </section>
        </section>
      ) : null}
    </>
  );
}
