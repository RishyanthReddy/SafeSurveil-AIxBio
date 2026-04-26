import {
  BracketsCurly,
  Database,
  GearSix,
  ShieldCheck,
  SquaresFour,
  Terminal,
} from "@phosphor-icons/react";
import type { ReactNode } from "react";

import { fetchHealthStatus, fetchIntegrationHealth } from "../api/client";
import { humanizeToken } from "../api/format";
import type {
  HealthResponse,
  IntegrationHealthEntry,
  IntegrationHealthResponse,
  IntegrationHealthValue,
} from "../api/types";
import { ApiErrorState } from "../components/ApiState";
import { useApiResource } from "../hooks/useApiResource";

type EntryDefinition = {
  key: string;
  label: string;
  description: string;
  icon: typeof Database;
  detailKeys: Array<{ key: string; label: string }>;
};

const externalApiDefinitions: EntryDefinition[] = [
  {
    key: "ncbi_datasets",
    label: "NCBI Datasets",
    description: "Assembly reports and genome package retrieval",
    icon: Database,
    detailKeys: [
      { key: "base_url", label: "Base URL" },
      { key: "api_key", label: "API key" },
    ],
  },
  {
    key: "bv_brc",
    label: "BV-BRC",
    description: "Live genome and AMR phenotype lookups",
    icon: Database,
    detailKeys: [
      { key: "auth_url", label: "Auth URL" },
      { key: "api_base_url", label: "API base" },
      { key: "token_file", label: "Token file" },
      { key: "username", label: "Username" },
      { key: "password", label: "Password" },
    ],
  },
  {
    key: "ncbi_pathogen_detection",
    label: "Pathogen Detection",
    description: "Metadata enrichment for live isolate context",
    icon: Database,
    detailKeys: [{ key: "base_url", label: "Base URL" }],
  },
  {
    key: "llm",
    label: "Grounded LLM",
    description: "OpenRouter-backed copilot generation path",
    icon: BracketsCurly,
    detailKeys: [
      { key: "provider", label: "Provider" },
      { key: "model", label: "Model" },
      { key: "fallback_model", label: "Fallback model" },
      { key: "base_url", label: "Base URL" },
      { key: "api_key", label: "API key" },
      { key: "mock_mode", label: "Mock mode" },
    ],
  },
  {
    key: "thesys",
    label: "Thesys C1",
    description: "Rendered semantic UI transport boundary",
    icon: SquaresFour,
    detailKeys: [
      { key: "model", label: "Model" },
      { key: "base_url", label: "Base URL" },
      { key: "api_key", label: "API key" },
    ],
  },
];

const toolDefinitions: EntryDefinition[] = [
  {
    key: "amrfinderplus",
    label: "AMRFinderPlus",
    description: "Mechanistic evidence execution on the local machine",
    icon: Terminal,
    detailKeys: [
      { key: "runtime_status", label: "Runtime" },
      { key: "version", label: "Version" },
      { key: "database", label: "Database" },
      { key: "database_version", label: "DB version" },
      { key: "source", label: "Resolution" },
      { key: "configured_value", label: "Configured" },
      { key: "executable_path", label: "Executable" },
      { key: "database_path", label: "DB path" },
      { key: "notes", label: "Notes" },
    ],
  },
  {
    key: "mash",
    label: "Mash",
    description: "Novelty runtime and distance-scoring availability",
    icon: GearSix,
    detailKeys: [
      { key: "runtime_status", label: "Runtime" },
      { key: "version", label: "Version" },
      { key: "source", label: "Resolution" },
      { key: "configured_value", label: "Configured" },
      { key: "executable_path", label: "Executable" },
      { key: "notes", label: "Notes" },
    ],
  },
];

function statusTone(status: string): string {
  if (status === "ready" || status === "available" || status === "configured" || status === "ok") {
    return "border-emerald-300 bg-emerald-50 text-emerald-900";
  }
  if (status === "degraded" || status === "pending" || status === "fixture") {
    return "border-amber-300 bg-amber-50 text-amber-950";
  }
  return "border-red-300 bg-red-50 text-act";
}

function renderHealthValue(value: IntegrationHealthValue | undefined): string {
  if (value === null || value === undefined || value === "") {
    return "Unavailable";
  }
  if (Array.isArray(value)) {
    return value.length > 0 ? value.join(" / ") : "Unavailable";
  }
  if (typeof value === "boolean") {
    return value ? "Yes" : "No";
  }
  if (typeof value === "number") {
    return new Intl.NumberFormat("en-US").format(value);
  }
  return value;
}

function runtimeLabel(health: HealthResponse, integrations: IntegrationHealthResponse): string {
  if (!health.runtime.live_mode_ready) {
    return "Mixed non-live";
  }
  return integrations.status === "ready" ? "Live-ready" : humanizeToken(integrations.status);
}

function EntryRow({
  definition,
  entry,
}: {
  definition: EntryDefinition;
  entry: IntegrationHealthEntry;
}) {
  const Icon = definition.icon;
  const status = String(entry.status ?? "missing");

  return (
    <div className="grid gap-4 p-5 2xl:grid-cols-[14rem_minmax(0,1fr)] 2xl:items-start">
      <div className="min-w-0">
        <div className="flex items-start gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded border border-line bg-surface-muted text-ink">
            <Icon size={18} weight="duotone" />
          </div>
          <div className="min-w-0">
            <h3 className="font-display text-base font-semibold tracking-tight text-ink">
              {definition.label}
            </h3>
            <p className="mt-1 text-sm leading-6 text-ink-muted">{definition.description}</p>
          </div>
        </div>
      </div>
      <div className="min-w-0">
        <span
          className={`inline-flex rounded border px-2.5 py-1 font-data text-[0.7rem] font-bold uppercase tracking-[0.14em] ${statusTone(status)}`}
        >
          {humanizeToken(status)}
        </span>
        <dl className="mt-4 grid gap-3">
          {definition.detailKeys.map((detail) => (
            <div className="min-w-0 space-y-1" key={`${definition.key}-${detail.key}`}>
              <dt className="label-caps whitespace-normal text-ink-muted">{detail.label}</dt>
              <dd className="max-w-full break-words text-sm leading-6 text-ink [overflow-wrap:anywhere]">
                {renderHealthValue(entry[detail.key])}
              </dd>
            </div>
          ))}
        </dl>
      </div>
    </div>
  );
}

function ReadinessSkeleton() {
  return (
    <div className="grid gap-gutter">
      <div className="clinical-panel overflow-hidden">
        <div className="border-b border-line bg-surface-muted p-5">
          <div className="h-3 w-40 animate-pulse rounded bg-surface-strong" />
        </div>
        <div className="grid gap-0 lg:grid-cols-[minmax(0,1.25fr)_minmax(18rem,0.75fr)]">
          <div className="space-y-4 p-5">
            <div className="h-7 w-52 animate-pulse rounded bg-surface-strong" />
            <div className="h-3 w-full animate-pulse rounded bg-surface-strong" />
            <div className="h-3 w-5/6 animate-pulse rounded bg-surface-strong" />
            <div className="grid gap-4 sm:grid-cols-2">
              {Array.from({ length: 4 }).map((_, index) => (
                <div className="space-y-2" key={index}>
                  <div className="h-3 w-24 animate-pulse rounded bg-surface-strong" />
                  <div className="h-4 w-32 animate-pulse rounded bg-surface-strong" />
                </div>
              ))}
            </div>
          </div>
          <div className="border-t border-line p-5 lg:border-l lg:border-t-0">
            <div className="h-3 w-24 animate-pulse rounded bg-surface-strong" />
            <div className="mt-4 h-20 animate-pulse rounded bg-surface-strong" />
          </div>
        </div>
      </div>
      <div className="grid gap-gutter xl:grid-cols-2">
        {Array.from({ length: 2 }).map((_, index) => (
          <div className="clinical-panel overflow-hidden" key={index}>
            <div className="border-b border-line bg-surface-muted p-5">
              <div className="h-3 w-36 animate-pulse rounded bg-surface-strong" />
            </div>
            <div className="divide-y divide-line">
              {Array.from({ length: index === 0 ? 3 : 2 }).map((_, rowIndex) => (
                <div className="grid gap-4 p-5 2xl:grid-cols-[14rem_minmax(0,1fr)]" key={rowIndex}>
                  <div className="h-10 animate-pulse rounded bg-surface-strong" />
                  <div className="space-y-3">
                    <div className="h-5 w-24 animate-pulse rounded bg-surface-strong" />
                    <div className="grid gap-3">
                      <div className="h-12 animate-pulse rounded bg-surface-strong" />
                      <div className="h-12 animate-pulse rounded bg-surface-strong" />
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export function SystemReadinessPanel({ actionPanel }: { actionPanel?: ReactNode }) {
  const health = useApiResource((signal) => fetchHealthStatus(signal), []);
  const integrations = useApiResource((signal) => fetchIntegrationHealth(signal), []);

  if (health.status === "loading" || integrations.status === "loading") {
    return <ReadinessSkeleton />;
  }

  if (health.status === "error") {
    return (
      <ApiErrorState
        title="Runtime status did not load"
        message={health.error}
      />
    );
  }

  if (integrations.status === "error") {
    return (
      <ApiErrorState
        title="Integration readiness did not load"
        message={integrations.error}
      />
    );
  }

  const runtime = health.data.runtime;
  const blockers = runtime.live_mode_blockers;

  return (
    <div className="grid gap-gutter">
      <div className={actionPanel ? "grid gap-gutter xl:grid-cols-[minmax(0,1.65fr)_minmax(20rem,0.7fr)] xl:items-start" : ""}>
        <section className="clinical-panel overflow-hidden">
          <div className="border-b border-line bg-surface-muted px-5 py-4">
            <p className="label-caps text-ink-muted">System readiness</p>
          </div>
          <div className="grid gap-0 lg:grid-cols-[minmax(0,1.25fr)_minmax(18rem,0.75fr)]">
            <div className="p-5">
              <div className="flex flex-wrap items-center gap-2">
                <span
                  className={`rounded border px-2.5 py-1 font-data text-[0.7rem] font-bold uppercase tracking-[0.14em] ${statusTone(health.data.status)}`}
                >
                  API {humanizeToken(health.data.status)}
                </span>
                <span
                  className={`rounded border px-2.5 py-1 font-data text-[0.7rem] font-bold uppercase tracking-[0.14em] ${statusTone(integrations.data.status)}`}
                >
                  Integrations {humanizeToken(integrations.data.status)}
                </span>
                <span
                  className={`rounded border px-2.5 py-1 font-data text-[0.7rem] font-bold uppercase tracking-[0.14em] ${statusTone(runtime.live_mode_ready ? "ready" : "degraded")}`}
                >
                  {runtimeLabel(health.data, integrations.data)}
                </span>
              </div>

              <h2 className="mt-4 font-display text-2xl font-semibold tracking-tight text-ink">
                Backend truth surface for live-ready demos
              </h2>
              <p className="mt-3 max-w-[66ch] text-sm leading-6 text-ink-muted">
                These statuses come directly from <span className="font-data text-ink">/health</span>{" "}
                and <span className="font-data text-ink">/health/integrations</span>. The frontend does
                not synthesize scientific or operational values here; it only reflects the backend&apos;s
                current runtime and integration state.
              </p>

              <dl className="mt-6 grid gap-5 sm:grid-cols-2 xl:grid-cols-3">
                <div>
                  <dt className="label-caps text-ink-muted">Backend mode</dt>
                  <dd className="mt-1 text-sm leading-6 text-ink">{humanizeToken(runtime.backend_mode)}</dd>
                </div>
                <div>
                  <dt className="label-caps text-ink-muted">Queue data</dt>
                  <dd className="mt-1 text-sm leading-6 text-ink">{humanizeToken(runtime.job_data_mode)}</dd>
                </div>
                <div>
                  <dt className="label-caps text-ink-muted">Evidence mode</dt>
                  <dd className="mt-1 text-sm leading-6 text-ink">{humanizeToken(runtime.evidence_mode)}</dd>
                </div>
                <div>
                  <dt className="label-caps text-ink-muted">LLM mode</dt>
                  <dd className="mt-1 text-sm leading-6 text-ink">{humanizeToken(runtime.llm_mode)}</dd>
                </div>
                <div>
                  <dt className="label-caps text-ink-muted">HTTP timeout</dt>
                  <dd className="mt-1 text-sm leading-6 text-ink">
                    {integrations.data.settings.http_timeout_seconds}s
                  </dd>
                </div>
                <div>
                  <dt className="label-caps text-ink-muted">Retry count</dt>
                  <dd className="mt-1 text-sm leading-6 text-ink">
                    {integrations.data.settings.http_retry_count}
                  </dd>
                </div>
              </dl>
            </div>

            <aside className="border-t border-line bg-white/70 p-5 lg:border-l lg:border-t-0">
              <div className="flex items-start gap-3">
                <div className="flex h-10 w-10 items-center justify-center rounded border border-line bg-surface-muted text-ink">
                  <ShieldCheck size={18} weight="duotone" />
                </div>
                <div>
                  <p className="label-caps text-ink-muted">Readiness blockers</p>
                  <p className="mt-2 text-sm leading-6 text-ink-muted">
                    Live acceptance is blocked whenever runtime mode drifts into demo, fixtures, or mock LLM
                    behavior.
                  </p>
                </div>
              </div>

              <div className="mt-5 flex flex-wrap gap-2">
                {blockers.length === 0 ? (
                  <span className="rounded border border-emerald-300 bg-emerald-50 px-2.5 py-1 font-data text-[0.7rem] font-bold uppercase tracking-[0.14em] text-emerald-900">
                    No live blockers
                  </span>
                ) : (
                  blockers.map((blocker) => (
                    <span
                      className="rounded border border-amber-300 bg-amber-50 px-2.5 py-1 font-data text-[0.7rem] font-bold uppercase tracking-[0.14em] text-amber-950"
                      key={blocker}
                    >
                      {humanizeToken(blocker)}
                    </span>
                  ))
                )}
              </div>

              <dl className="mt-6 space-y-4">
                <div>
                  <dt className="label-caps text-ink-muted">App environment</dt>
                  <dd className="mt-1 text-sm leading-6 text-ink">{runtime.app_env}</dd>
                </div>
                <div>
                  <dt className="label-caps text-ink-muted">Dataset root</dt>
                  <dd className="mt-1 break-all text-sm leading-6 text-ink">
                    {integrations.data.settings.dataset_root}
                  </dd>
                </div>
                <div>
                  <dt className="label-caps text-ink-muted">Log level</dt>
                  <dd className="mt-1 text-sm leading-6 text-ink">{integrations.data.settings.log_level}</dd>
                </div>
                <div>
                  <dt className="label-caps text-ink-muted">Secrets exposure</dt>
                  <dd className="mt-1 text-sm leading-6 text-ink">
                    {integrations.data.secrets.redacted ? "Redacted" : "Exposed"}
                  </dd>
                </div>
              </dl>
            </aside>
          </div>
        </section>

        {actionPanel ? <div className="xl:min-w-0">{actionPanel}</div> : null}
      </div>

      <div className="grid gap-gutter xl:grid-cols-2">
        <section className="clinical-panel overflow-hidden">
          <div className="border-b border-line bg-surface-muted px-5 py-4">
            <p className="label-caps text-ink-muted">External APIs</p>
          </div>
          <div className="divide-y divide-line">
            {externalApiDefinitions.map((definition) => {
              const entry = integrations.data.external_apis[definition.key];
              if (!entry) {
                return null;
              }
              return <EntryRow definition={definition} entry={entry} key={definition.key} />;
            })}
          </div>
        </section>

        <section className="clinical-panel overflow-hidden">
          <div className="border-b border-line bg-surface-muted px-5 py-4">
            <p className="label-caps text-ink-muted">Local tooling</p>
          </div>
          <div className="divide-y divide-line">
            {toolDefinitions.map((definition) => {
              const entry = integrations.data.tools[definition.key];
              if (!entry) {
                return null;
              }
              return <EntryRow definition={definition} entry={entry} key={definition.key} />;
            })}
          </div>
        </section>
      </div>
    </div>
  );
}
