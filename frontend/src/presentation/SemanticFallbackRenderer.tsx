import { ChartLineUp, Database, ListChecks, WarningCircle } from "@phosphor-icons/react";

import { formatCount, formatPercent, humanizeToken, triageClass, triageLabel } from "../api/format";
import type { SemanticUIObject } from "../api/types";
import {
  adaptSemanticUi,
  type PresentationMetric,
  type PresentationRiskChart,
  type SemanticPresentationModel,
} from "./semanticAdapter";

function metricValue(metric: PresentationMetric): string {
  if (typeof metric.value === "number") {
    const token = `${metric.key} ${metric.label}`.toLowerCase();
    if (
      metric.unit === "%" ||
      token.includes("score") ||
      token.includes("risk") ||
      token.includes("probability") ||
      token.includes("percentile") ||
      token.includes("completeness")
    ) {
      return formatPercent(metric.value, 0);
    }
    return Number.isInteger(metric.value) ? formatCount(metric.value) : String(metric.value);
  }
  if (typeof metric.value === "boolean") {
    return metric.value ? "True" : "False";
  }
  return metric.value;
}

function chartBarWidth(value: number): string {
  const bounded = Math.max(0, Math.min(1, value));
  return `${bounded * 100}%`;
}

function chartPointValue(value: number): string {
  return value >= 0 && value <= 1 ? formatPercent(value, 0) : String(value);
}

function DecisionBlock({ model }: { model: SemanticPresentationModel }) {
  const card = model.decisionCard;
  const ready = card.status === "ready";
  return (
    <article className={`clinical-panel p-5 ${ready ? "border-l-[3px] border-l-review" : ""}`}>
      <div className="flex flex-wrap items-center gap-2">
        {ready ? (
          <span
            className={`inline-flex rounded border px-2 py-1 text-[0.68rem] font-bold uppercase tracking-[0.12em] ${triageClass(
              card.triage,
            )}`}
          >
            {triageLabel(card.triage)}
          </span>
        ) : (
          <span className="inline-flex rounded border border-line bg-surface-muted px-2 py-1 text-[0.68rem] font-bold uppercase tracking-[0.12em] text-ink-muted">
            Unavailable
          </span>
        )}
        {ready ? <span className="label-caps text-ink-muted">Severity: {humanizeToken(card.severity)}</span> : null}
      </div>
      <h2 className="mt-5 font-display text-2xl font-semibold tracking-tight">{card.title}</h2>
      <p className="mt-3 max-w-[64ch] text-sm leading-6 text-ink-muted">{card.summary}</p>
      {card.metrics.length > 0 ? (
        <div className="mt-5 grid gap-2 md:grid-cols-2">
          {card.metrics.map((metric) => (
            <div className="rounded border border-line bg-surface-muted p-3" key={metric.key}>
              <p className="label-caps text-ink-muted">{metric.label}</p>
              <p className="mt-2 font-display text-xl font-bold">{metricValue(metric)}</p>
            </div>
          ))}
        </div>
      ) : (
        <div className="mt-5 rounded border border-dashed border-line bg-surface-muted p-4">
          <p className="label-caps text-ink-muted">Metrics unavailable</p>
          <p className="mt-2 text-sm leading-6 text-ink-muted">No decision-card metrics were supplied.</p>
        </div>
      )}
    </article>
  );
}

function RiskChart({ chart }: { chart: PresentationRiskChart }) {
  return (
    <article className="clinical-panel p-5">
      <div className="flex items-center justify-between gap-3 border-b border-line pb-3">
        <div className="flex items-center gap-2">
          <ChartLineUp size={20} weight="duotone" />
          <h3 className="label-caps text-ink">{chart.title}</h3>
        </div>
        <span className="font-data text-xs uppercase tracking-[0.12em] text-ink-muted">{chart.chartType}</span>
      </div>
      {chart.status === "ready" ? (
        <div className="mt-4 space-y-3">
          {chart.points.map((point) => (
            <div key={`${chart.chartId}-${point.label}`}>
              <div className="mb-1 flex items-center justify-between gap-3">
                <span className="text-sm text-ink-muted">{point.label}</span>
                <span className="font-data text-sm font-semibold text-ink">{chartPointValue(point.value)}</span>
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-surface-strong">
                <div className="h-full bg-ink transition-[width] duration-300" style={{ width: chartBarWidth(point.value) }} />
              </div>
              {point.evidence_id ? (
                <p className="mt-1 font-data text-[0.68rem] uppercase tracking-[0.12em] text-ink-muted">
                  Evidence: {point.evidence_id}
                </p>
              ) : null}
            </div>
          ))}
        </div>
      ) : (
        <div className="mt-4 rounded border border-dashed border-line bg-surface-muted p-4">
          <p className="label-caps text-ink-muted">Chart unavailable</p>
          <p className="mt-2 text-sm leading-6 text-ink-muted">No risk-chart points were supplied.</p>
        </div>
      )}
    </article>
  );
}

function EvidenceBlock({ model }: { model: SemanticPresentationModel }) {
  const table = model.evidenceTable;
  return (
    <article className="clinical-panel overflow-hidden">
      <div className="flex items-center justify-between gap-3 border-b border-line bg-surface-muted p-5">
        <div className="flex items-center gap-2">
          <Database size={20} weight="duotone" />
          <h3 className="label-caps text-ink">{table.title}</h3>
        </div>
        <span className="font-data text-xs uppercase tracking-[0.12em] text-ink-muted">
          {formatCount(table.rows.length)} rows
        </span>
      </div>
      {table.status === "ready" ? (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] border-collapse text-left">
            <thead>
              <tr className="border-b border-line">
                <th className="label-caps px-5 py-3 text-ink-muted">Label</th>
                {table.columns.map((column) => (
                  <th className="label-caps px-5 py-3 text-ink-muted" key={column}>
                    {humanizeToken(column)}
                  </th>
                ))}
                <th className="label-caps px-5 py-3 text-ink-muted">Evidence ID</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line font-data text-sm">
              {table.rows.map((row) => (
                <tr className="transition hover:bg-surface-muted" key={row.row_id}>
                  <td className="px-5 py-4 font-semibold text-ink">{row.label}</td>
                  {table.columns.map((column) => (
                    <td className="px-5 py-4 text-ink-muted" key={`${row.row_id}-${column}`}>
                      {row.cells[column] === null || row.cells[column] === undefined ? "Unavailable" : String(row.cells[column])}
                    </td>
                  ))}
                  <td className="px-5 py-4 text-ink-muted">{row.evidence_id ?? "Unavailable"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="p-5">
          <p className="text-sm leading-6 text-ink-muted">
            The semantic UI payload did not include renderable mechanism rows. The adapter keeps this section empty
            instead of synthesizing mechanism labels.
          </p>
        </div>
      )}
    </article>
  );
}

function ContextBlock({ model }: { model: SemanticPresentationModel }) {
  return (
    <aside className="clinical-panel divide-y divide-line">
      <section className="p-5">
        <div className="flex items-center gap-2">
          <ListChecks size={20} weight="duotone" />
          <h3 className="label-caps text-ink">{model.queueBlock.title}</h3>
        </div>
        {model.queueBlock.status === "ready" ? (
          <div className="mt-4 space-y-3">
            {model.queueBlock.items.map((item) => (
              <div className="rounded border border-line bg-surface-muted p-3" key={item.job_id}>
                <p className="font-data text-sm font-semibold text-ink">{item.sample_id}</p>
                <p className="mt-1 text-xs leading-5 text-ink-muted">{item.headline}</p>
              </div>
            ))}
          </div>
        ) : (
          <p className="mt-3 text-sm leading-6 text-ink-muted">No queue block was supplied.</p>
        )}
      </section>

      <section className="p-5">
        <h3 className="label-caps text-ink">Grounding notes</h3>
        {model.notes.length > 0 ? (
          <ul className="mt-3 space-y-2 text-sm leading-6 text-ink-muted">
            {model.notes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        ) : (
          <p className="mt-3 text-sm leading-6 text-ink-muted">No semantic UI notes were supplied.</p>
        )}
      </section>

      {model.issues.length > 0 ? (
        <section className="p-5">
          <div className="flex items-center gap-2">
            <WarningCircle size={20} weight="duotone" />
            <h3 className="label-caps text-ink">Adapter issues</h3>
          </div>
          <div className="mt-3 space-y-2">
            {model.issues.map((issue) => (
              <p className="rounded border border-line bg-surface-muted p-2 font-data text-xs text-ink-muted" key={`${issue.path}-${issue.message}`}>
                {issue.path}: {issue.message}
              </p>
            ))}
          </div>
        </section>
      ) : null}
    </aside>
  );
}

export function SemanticFallbackRenderer({
  semanticUi,
  title = "React fallback dashboard",
}: {
  semanticUi: SemanticUIObject | Partial<SemanticUIObject> | null | undefined;
  title?: string;
}) {
  const model = adaptSemanticUi(semanticUi);
  return (
    <div>
      <div className="mb-4 flex flex-col gap-2 md:flex-row md:items-end md:justify-between">
        <div>
          <p className="label-caps text-ink-muted">Presentation adapter</p>
          <h2 className="font-display text-2xl font-semibold tracking-tight">{title}</h2>
        </div>
        <p className="font-data text-xs uppercase tracking-[0.12em] text-ink-muted">
          {model.issues.length === 0 ? "Contract clean" : `${model.issues.length} adapter notices`}
        </p>
      </div>
      <section className="grid gap-gutter">
        <DecisionBlock model={model} />
        <div className="grid gap-gutter">
          {model.riskCharts.map((chart) => (
            <RiskChart chart={chart} key={chart.chartId} />
          ))}
        </div>
        <EvidenceBlock model={model} />
        <ContextBlock model={model} />
      </section>
    </div>
  );
}
