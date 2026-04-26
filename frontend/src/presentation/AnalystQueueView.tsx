import {
  ArrowSquareOut,
  ChartDonut,
  Columns,
  Funnel,
  Kanban,
  Queue,
  Scales,
  SortAscending,
  Target,
} from "@phosphor-icons/react";
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { formatCount, formatPercent, humanizeToken, triageClass, triageLabel } from "../api/format";
import type { QueueItem, SeverityLevel, TriageOutcome } from "../api/types";

type AnalystQueueViewProps = {
  items: QueueItem[];
  snapshots: Record<string, QueueDecisionSnapshot>;
};

type TriageFilter = TriageOutcome | "all";
type SortKey = "actionability" | "priority" | "severity" | "triage" | "sample";

export type QueueDecisionSnapshot = {
  accession: string | null;
  actionabilityScore: number | null;
  collectionDate: string | null;
  evidenceCount: number | null;
  error?: string;
  organism: string | null;
};

const triageOrder: TriageOutcome[] = ["act", "review", "defer_to_lab"];
const severityOrder: Record<SeverityLevel, number> = {
  critical: 4,
  high: 3,
  medium: 2,
  low: 1,
};

function normalizeTriageFilter(value: string | null): TriageFilter {
  if (value === "act" || value === "review" || value === "defer_to_lab") {
    return value;
  }
  return "all";
}

function scoreFromPriority(item: QueueItem): number {
  if (!Number.isFinite(item.queue_priority)) {
    return 0;
  }
  return Math.max(0.12, Math.min(1, 1 - item.queue_priority / 30));
}

function compareByPriority(left: QueueItem, right: QueueItem): number {
  return (
    left.queue_priority - right.queue_priority ||
    severityOrder[right.severity] - severityOrder[left.severity] ||
    left.sample_id.localeCompare(right.sample_id)
  );
}

function snapshotFor(
  snapshots: Record<string, QueueDecisionSnapshot>,
  item: QueueItem,
): QueueDecisionSnapshot | null {
  return snapshots[item.job_id] ?? null;
}

function compareBySortKey(sortKey: SortKey, snapshots: Record<string, QueueDecisionSnapshot>) {
  return (left: QueueItem, right: QueueItem): number => {
    if (sortKey === "actionability") {
      const leftScore = snapshotFor(snapshots, left)?.actionabilityScore ?? -1;
      const rightScore = snapshotFor(snapshots, right)?.actionabilityScore ?? -1;
      return rightScore - leftScore || compareByPriority(left, right);
    }
    if (sortKey === "severity") {
      return severityOrder[right.severity] - severityOrder[left.severity] || compareByPriority(left, right);
    }
    if (sortKey === "triage") {
      return triageOrder.indexOf(left.triage) - triageOrder.indexOf(right.triage) || compareByPriority(left, right);
    }
    if (sortKey === "sample") {
      return left.sample_id.localeCompare(right.sample_id) || compareByPriority(left, right);
    }
    return compareByPriority(left, right);
  };
}

function matchesQuery(
  item: QueueItem,
  query: string,
  snapshots: Record<string, QueueDecisionSnapshot>,
): boolean {
  const normalizedQuery = query.trim().toLowerCase();
  if (!normalizedQuery) {
    return true;
  }
  const snapshot = snapshotFor(snapshots, item);
  const haystack = [
    item.job_id,
    item.sample_id,
    item.target_drug,
    item.headline,
    item.status,
    item.severity,
    item.triage,
    snapshot?.accession,
    snapshot?.collectionDate,
    snapshot?.organism,
    ...item.rationale_codes,
  ]
    .filter((piece): piece is string => Boolean(piece))
    .join(" ")
    .toLowerCase();
  return haystack.includes(normalizedQuery);
}

function severityTone(severity: SeverityLevel): string {
  if (severity === "critical") {
    return "text-act";
  }
  if (severity === "high") {
    return "text-review";
  }
  return "text-defer";
}

function statusLabel(item: QueueItem): string {
  return humanizeToken(item.status);
}

function countByTriage(items: QueueItem[], triage: TriageOutcome): number {
  return items.filter((item) => item.triage === triage).length;
}

function representativeForTriage(items: QueueItem[], triage: TriageOutcome): QueueItem | null {
  return [...items].filter((item) => item.triage === triage).sort(compareByPriority)[0] ?? null;
}

function updatedAtLabel(item: QueueItem): string {
  return item.updated_at ?? "Not timestamped";
}

function dateLabel(value: string | null | undefined): string {
  if (!value) {
    return "No collection date";
  }
  return value;
}

function organismLabel(snapshot: QueueDecisionSnapshot | null): string {
  return snapshot?.organism ? humanizeToken(snapshot.organism) : "Organism not reported";
}

function RationalePills({ codes }: { codes: string[] }) {
  if (codes.length === 0) {
    return <span className="text-xs text-ink-muted">No rationale codes reported</span>;
  }

  return (
    <div className="flex flex-wrap gap-1.5">
      {codes.map((code) => (
        <span
          className="rounded border border-line bg-surface-muted px-2 py-1 text-[0.65rem] font-semibold uppercase tracking-[0.1em] text-ink-muted"
          key={code}
        >
          {humanizeToken(code)}
        </span>
      ))}
    </div>
  );
}

function ActionabilityMeter({ item, snapshot }: { item: QueueItem; snapshot: QueueDecisionSnapshot | null }) {
  const score = snapshot?.actionabilityScore ?? null;
  const width = score === null ? "0%" : formatPercent(score, 0);
  return (
    <div className="flex items-center justify-end gap-3">
      <span className="font-semibold text-ink">{score === null ? "n/a" : formatPercent(score, 0)}</span>
      <div className="h-1.5 w-20 overflow-hidden rounded-full bg-surface-strong">
        <div className="h-full rounded-full bg-ink transition-[width] duration-300" style={{ width }} />
      </div>
      <span className="sr-only">Queue priority {formatCount(item.queue_priority)}</span>
    </div>
  );
}

function TriageSummaryCards({ items, onFilter }: { items: QueueItem[]; onFilter: (triage: TriageFilter) => void }) {
  return (
    <section className="grid gap-gutter md:grid-cols-3">
      {triageOrder.map((triage) => {
        const count = countByTriage(items, triage);
        const representative = representativeForTriage(items, triage);
        return (
          <button
            className={`clinical-panel border-l-[4px] p-5 text-left transition hover:-translate-y-0.5 hover:border-slate-700 active:scale-[0.99] ${
              triage === "act" ? "border-l-act" : triage === "review" ? "border-l-review" : "border-l-defer"
            }`}
            key={triage}
            onClick={() => onFilter(triage)}
            type="button"
          >
            <div className="flex items-start justify-between gap-3">
              <span className={`inline-flex rounded border px-2.5 py-1 text-[0.68rem] font-bold uppercase tracking-[0.12em] ${triageClass(triage)}`}>
                {triageLabel(triage)}
              </span>
              <ChartDonut className="text-ink-muted" size={22} weight="duotone" />
            </div>
            <p className="mt-5 font-display text-5xl font-bold leading-none tracking-[-0.05em]">{formatCount(count)}</p>
            <p className="mt-3 text-sm leading-6 text-ink-muted">
              {representative ? representative.headline : "No API-returned case for this triage lane."}
            </p>
          </button>
        );
      })}
    </section>
  );
}

function FocusPanel({
  item,
  snapshot,
}: {
  item: QueueItem | null;
  snapshot: QueueDecisionSnapshot | null;
}) {
  if (!item) {
    return (
      <article className="clinical-panel border-l-[4px] border-l-defer p-6">
        <p className="label-caps text-defer">No focused sample</p>
        <p className="mt-3 text-sm leading-6 text-ink-muted">
          Select a queue row to compare the sample against the ACT, REVIEW, and DEFER lanes.
        </p>
      </article>
    );
  }

  return (
    <article className="clinical-panel overflow-hidden">
      <div className="grid min-w-0 xl:grid-cols-[minmax(20rem,0.8fr)_minmax(0,1.2fr)]">
        <div className="min-w-0 border-b border-line bg-ink p-6 text-white xl:border-b-0 xl:border-r">
          <p className="label-caps text-slate-300">Focused sample</p>
          <h2 className="mt-3 break-words font-display text-2xl font-semibold tracking-tight [overflow-wrap:anywhere]">
            {item.sample_id}
          </h2>
          <p className="mt-2 max-w-[58ch] break-words text-sm leading-6 text-slate-300 [overflow-wrap:anywhere]">
            {item.headline}
          </p>
        </div>

        <div className="min-w-0 bg-white p-5 xl:p-6">
          <dl className="grid gap-x-6 gap-y-5 md:grid-cols-2 xl:grid-cols-4">
            <div className="grid gap-1">
              <dt className="label-caps text-ink-muted">Job</dt>
              <dd className="break-words font-data text-xs text-ink [overflow-wrap:anywhere]">{item.job_id}</dd>
            </div>
            <div className="grid gap-1">
              <dt className="label-caps text-ink-muted">Target</dt>
              <dd className="font-data text-sm text-ink">{humanizeToken(item.target_drug)}</dd>
            </div>
            <div className="grid gap-1">
              <dt className="label-caps text-ink-muted">Organism</dt>
              <dd className="font-data text-sm text-ink">{organismLabel(snapshot)}</dd>
            </div>
            <div className="grid gap-1">
              <dt className="label-caps text-ink-muted">Accession</dt>
              <dd className="break-words font-data text-xs text-ink [overflow-wrap:anywhere]">
                {snapshot?.accession ?? "Unavailable"}
              </dd>
            </div>
            <div className="grid gap-1">
              <dt className="label-caps text-ink-muted">Status</dt>
              <dd className="font-data text-sm text-ink">{statusLabel(item)}</dd>
            </div>
            <div className="grid gap-1">
              <dt className="label-caps text-ink-muted">Actionability</dt>
              <dd className="font-data text-sm text-ink">
                {snapshot?.actionabilityScore === null || snapshot?.actionabilityScore === undefined
                  ? "Unavailable"
                  : formatPercent(snapshot.actionabilityScore, 0)}
              </dd>
            </div>
            <div className="grid gap-1">
              <dt className="label-caps text-ink-muted">Priority</dt>
              <dd className="font-data text-sm text-ink">Queue priority {formatCount(item.queue_priority)}</dd>
            </div>
            <div className="grid gap-1 md:col-span-2 xl:col-span-1">
              <dt className="label-caps text-ink-muted">Updated</dt>
              <dd className="font-data text-sm text-ink">{updatedAtLabel(item)}</dd>
            </div>
            <div className="grid gap-2 md:col-span-2 xl:col-span-4">
              <dt className="label-caps text-ink-muted">Rationale</dt>
              <dd>
                <RationalePills codes={item.rationale_codes} />
              </dd>
            </div>
          </dl>

          <div className="mt-6 flex flex-wrap items-center justify-between gap-3 border-t border-line pt-5">
            <p className="text-sm leading-6 text-ink-muted">
              This focused strip keeps the selected case visible while the full queue stays wide enough for operator
              scanning.
            </p>
            <Link
              className="inline-flex items-center gap-2 rounded bg-ink px-4 py-2.5 font-data text-xs font-bold uppercase tracking-[0.12em] text-white transition hover:bg-slate-700 active:scale-[0.98]"
              to={`/cases/${item.job_id}`}
            >
              Open full case
              <ArrowSquareOut size={16} weight="bold" />
            </Link>
          </div>
        </div>
      </div>
    </article>
  );
}

function ContrastiveLane({
  item,
  onSelect,
  selected,
  snapshot,
  triage,
}: {
  item: QueueItem | null;
  onSelect: (item: QueueItem) => void;
  selected: boolean;
  snapshot: QueueDecisionSnapshot | null;
  triage: TriageOutcome;
}) {
  if (!item) {
    return (
      <div className="h-full min-h-[14rem] rounded-lg border border-dashed border-line bg-surface-muted p-4">
        <span className={`inline-flex rounded border px-2 py-1 text-[0.68rem] font-bold uppercase tracking-[0.12em] ${triageClass(triage)}`}>
          {triageLabel(triage)}
        </span>
        <p className="mt-4 text-sm leading-6 text-ink-muted">No loaded queue item can anchor this comparison lane.</p>
      </div>
    );
  }

  return (
    <button
      className={`h-full min-h-[14rem] w-full min-w-0 rounded-lg border p-4 text-left transition hover:-translate-y-0.5 active:scale-[0.99] ${
        selected ? "border-ink bg-white" : "border-line bg-surface-muted hover:bg-white"
      }`}
      onClick={() => onSelect(item)}
      type="button"
    >
      <div className="flex items-start justify-between gap-3">
        <span className={`inline-flex rounded border px-2 py-1 text-[0.68rem] font-bold uppercase tracking-[0.12em] ${triageClass(item.triage)}`}>
          {triageLabel(item.triage)}
        </span>
        <span className={`font-data text-xs font-bold uppercase tracking-[0.12em] ${severityTone(item.severity)}`}>
          {humanizeToken(item.severity)}
        </span>
      </div>
      <p className="mt-4 break-words font-data text-sm font-bold text-ink [overflow-wrap:anywhere]">{item.sample_id}</p>
      <p className="mt-1 font-data text-[0.68rem] uppercase tracking-[0.12em] text-ink-muted">
        {organismLabel(snapshot)}
      </p>
      <p className="mt-2 text-xs leading-5 text-ink-muted">{item.headline}</p>
      <div className="mt-4 h-1.5 overflow-hidden rounded-full bg-surface-strong">
        <div className="h-full rounded-full bg-ink" style={{ width: formatPercent(scoreFromPriority(item), 0) }} />
      </div>
    </button>
  );
}

export function AnalystQueueView({ items, snapshots }: AnalystQueueViewProps) {
  const [searchParams, setSearchParams] = useSearchParams();
  const triageFilter = normalizeTriageFilter(searchParams.get("triage"));
  const queryParam = searchParams.get("q") ?? "";
  const jobParam = searchParams.get("jobId");
  const [query, setQuery] = useState(queryParam);
  const [sortKey, setSortKey] = useState<SortKey>("actionability");
  const [selectedJobId, setSelectedJobId] = useState<string | null>(jobParam);

  useEffect(() => {
    setQuery(queryParam);
  }, [queryParam]);

  useEffect(() => {
    if (jobParam) {
      setSelectedJobId(jobParam);
    }
  }, [jobParam]);

  function selectJob(jobId: string) {
    setSelectedJobId(jobId);
    const nextParams = new URLSearchParams(searchParams);
    nextParams.set("jobId", jobId);
    setSearchParams(nextParams, { replace: true });
  }

  function setTriageFilter(nextFilter: TriageFilter) {
    const nextParams = new URLSearchParams(searchParams);
    if (nextFilter === "all") {
      nextParams.delete("triage");
    } else {
      nextParams.set("triage", nextFilter);
    }
    setSearchParams(nextParams);
  }

  function setSearchQuery(nextQuery: string) {
    setQuery(nextQuery);
    const nextParams = new URLSearchParams(searchParams);
    if (nextQuery.trim()) {
      nextParams.set("q", nextQuery);
    } else {
      nextParams.delete("q");
    }
    setSearchParams(nextParams, { replace: true });
  }

  const filteredItems = items
    .filter((item) => triageFilter === "all" || item.triage === triageFilter)
    .filter((item) => matchesQuery(item, query, snapshots))
    .sort(compareBySortKey(sortKey, snapshots));
  const selectedItem =
    (selectedJobId ? items.find((item) => item.job_id === selectedJobId) : null) ?? filteredItems[0] ?? null;
  const selectedSnapshot = selectedItem ? snapshotFor(snapshots, selectedItem) : null;
  const contrastItems = triageOrder.map((triage) => representativeForTriage(items, triage));
  const coveredLaneCount = contrastItems.filter(Boolean).length;
  const enrichedCount = Object.values(snapshots).filter((snapshot) => !snapshot.error).length;

  return (
    <div className="grid gap-gutter">
      <TriageSummaryCards items={items} onFilter={setTriageFilter} />

      <FocusPanel item={selectedItem} snapshot={selectedSnapshot} />

      <article className="clinical-panel overflow-hidden">
        <div className="border-b border-line bg-surface-muted p-5">
          <div className="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
            <div>
              <div className="flex items-center gap-3">
                <Queue size={24} weight="duotone" />
                <div>
                  <p className="label-caps text-ink-muted">Live API route</p>
                  <h2 className="mt-1 font-display text-2xl font-semibold tracking-tight">Analyst Queue</h2>
                </div>
              </div>
              <p className="mt-3 text-sm leading-6 text-ink-muted">
                {formatCount(filteredItems.length)} of {formatCount(items.length)} loaded records match the current
                filter set.
              </p>
            </div>

            <div className="flex flex-wrap gap-2">
              <span className="rounded border border-line bg-white px-2.5 py-1 font-data text-xs text-ink-muted">
                {coveredLaneCount}/3 contrast lanes loaded
              </span>
              <span className="rounded border border-line bg-white px-2.5 py-1 font-data text-xs text-ink-muted">
                {formatCount(enrichedCount)} decision snapshot{enrichedCount === 1 ? "" : "s"}
              </span>
              <button
                className="rounded border border-line bg-white px-2.5 py-1 font-data text-xs font-bold uppercase tracking-[0.12em] text-ink-muted transition hover:border-ink active:scale-[0.98]"
                onClick={() => setTriageFilter("all")}
                type="button"
              >
                Show all
              </button>
            </div>
          </div>
        </div>

        <div className="grid gap-4 border-b border-line bg-white p-5 xl:grid-cols-[minmax(20rem,1.25fr)_minmax(14rem,0.85fr)_minmax(14rem,0.85fr)]">
          <label className="grid gap-2">
            <span className="label-caps text-ink-muted">Sample, job, organism, or rationale</span>
            <input
              className="rounded border border-line bg-surface-muted px-3 py-2.5 font-data text-sm text-ink outline-none transition focus:border-ink focus:bg-white focus:ring-2 focus:ring-slate-200"
              onChange={(event) => setSearchQuery(event.target.value)}
              placeholder="Search queue payload..."
              type="search"
              value={query}
            />
          </label>

          <label className="grid gap-2">
            <span className="label-caps text-ink-muted">Triage status</span>
            <select
              className="rounded border border-line bg-surface-muted px-3 py-2.5 font-data text-sm text-ink outline-none transition focus:border-ink focus:bg-white focus:ring-2 focus:ring-slate-200"
              onChange={(event) => setTriageFilter(normalizeTriageFilter(event.target.value))}
              value={triageFilter}
            >
              <option value="all">All statuses</option>
              {triageOrder.map((triage) => (
                <option key={triage} value={triage}>
                  {triageLabel(triage)}
                </option>
              ))}
            </select>
          </label>

          <label className="grid gap-2">
            <span className="label-caps text-ink-muted">Sort by</span>
            <select
              className="rounded border border-line bg-surface-muted px-3 py-2.5 font-data text-sm text-ink outline-none transition focus:border-ink focus:bg-white focus:ring-2 focus:ring-slate-200"
              onChange={(event) => setSortKey(event.target.value as SortKey)}
              value={sortKey}
            >
              <option value="actionability">Actionability</option>
              <option value="priority">Queue priority</option>
              <option value="severity">Severity</option>
              <option value="triage">Triage lane</option>
              <option value="sample">Sample ID</option>
            </select>
          </label>
        </div>

        <div className="flex flex-col gap-3 border-b border-line bg-surface-muted/50 px-5 py-3 md:flex-row md:items-center md:justify-between">
          <div className="flex items-center gap-2 text-sm text-ink-muted">
            <Funnel size={18} weight="duotone" />
            Filtered by {triageFilter === "all" ? "all triage lanes" : triageLabel(triageFilter)}.
          </div>
          <div className="flex items-center gap-2 text-sm text-ink-muted">
            <SortAscending size={18} weight="duotone" />
            Sorted by {humanizeToken(sortKey)}. Scroll horizontally for the complete live queue footprint.
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full min-w-[1360px] border-collapse text-left">
            <thead>
              <tr className="border-b border-line bg-surface-muted">
                <th className="label-caps w-[20rem] px-5 py-3 text-ink-muted">Sample</th>
                <th className="label-caps w-[16rem] px-5 py-3 text-ink-muted">Organism / date</th>
                <th className="label-caps w-[12rem] px-5 py-3 text-ink-muted">Target</th>
                <th className="label-caps w-[10rem] px-5 py-3 text-ink-muted">Triage</th>
                <th className="label-caps w-[12rem] px-5 py-3 text-ink-muted">Severity / status</th>
                <th className="label-caps w-[24rem] px-5 py-3 text-ink-muted">Rationale</th>
                <th className="label-caps w-[14rem] px-5 py-3 text-right text-ink-muted">Actionability</th>
                <th className="label-caps w-[12rem] px-5 py-3 text-right text-ink-muted">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line font-data text-sm">
              {filteredItems.length > 0 ? (
                filteredItems.map((item) => {
                  const selected = selectedItem?.job_id === item.job_id;
                  const snapshot = snapshotFor(snapshots, item);
                  return (
                    <tr
                      className={`group transition hover:bg-surface-muted ${selected ? "bg-surface-muted" : "bg-white"}`}
                      key={item.job_id}
                    >
                      <td className="relative px-5 py-4">
                        <div className="absolute bottom-0 left-0 top-0 w-[2px] bg-ink opacity-0 transition group-hover:opacity-100" />
                        <button
                          className="text-left transition active:scale-[0.99]"
                          onClick={() => selectJob(item.job_id)}
                          type="button"
                        >
                          <div className="font-semibold text-ink">{item.sample_id}</div>
                          <div className="mt-1 max-w-[22rem] truncate text-xs text-ink-muted">{item.headline}</div>
                          <div className="mt-2 font-data text-[0.68rem] uppercase tracking-[0.12em] text-ink-muted">
                            {updatedAtLabel(item)}
                          </div>
                        </button>
                      </td>
                      <td className="px-5 py-4">
                        <p className="font-semibold text-ink">{organismLabel(snapshot)}</p>
                        <p className="mt-1 font-data text-[0.68rem] uppercase tracking-[0.12em] text-ink-muted">
                          {dateLabel(snapshot?.collectionDate)}
                        </p>
                        <p className="mt-1 max-w-[14rem] truncate font-data text-[0.68rem] uppercase tracking-[0.12em] text-ink-muted">
                          {snapshot?.accession ?? "No accession"}
                        </p>
                      </td>
                      <td className="px-5 py-4 text-ink-muted">{humanizeToken(item.target_drug)}</td>
                      <td className="px-5 py-4">
                        <span
                          className={`inline-flex rounded border px-2 py-1 text-[0.68rem] font-bold uppercase tracking-[0.12em] ${triageClass(
                            item.triage,
                          )}`}
                        >
                          {triageLabel(item.triage)}
                        </span>
                      </td>
                      <td className="px-5 py-4">
                        <p className={`font-semibold ${severityTone(item.severity)}`}>{humanizeToken(item.severity)}</p>
                        <p className="mt-1 text-xs text-ink-muted">{statusLabel(item)}</p>
                      </td>
                      <td className="px-5 py-4">
                        <RationalePills codes={item.rationale_codes} />
                      </td>
                      <td className="px-5 py-4 text-right">
                        <ActionabilityMeter item={item} snapshot={snapshot} />
                        <p className="mt-1 font-data text-[0.68rem] uppercase tracking-[0.12em] text-ink-muted">
                          Priority {formatCount(item.queue_priority)}
                          {snapshot?.evidenceCount !== null && snapshot?.evidenceCount !== undefined
                            ? ` / ${formatCount(snapshot.evidenceCount)} evidence`
                            : ""}
                        </p>
                      </td>
                      <td className="px-5 py-4 text-right">
                        <div className="flex justify-end gap-2">
                          <button
                            className="rounded border border-line bg-white px-3 py-2 text-xs font-bold uppercase tracking-[0.12em] transition hover:border-ink active:scale-[0.98]"
                            onClick={() => selectJob(item.job_id)}
                            type="button"
                          >
                            Compare
                          </button>
                          <Link
                            className="inline-flex items-center gap-2 rounded bg-ink px-3 py-2 text-xs font-bold uppercase tracking-[0.12em] text-white transition hover:bg-slate-700 active:scale-[0.98]"
                            to={`/cases/${item.job_id}`}
                          >
                            Inspect
                            <ArrowSquareOut size={14} weight="bold" />
                          </Link>
                        </div>
                      </td>
                    </tr>
                  );
                })
              ) : (
                <tr>
                  <td className="px-5 py-8 text-center text-sm text-ink-muted" colSpan={8}>
                    No loaded queue rows match the current filters.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        <div className="flex flex-col gap-3 border-t border-line bg-surface-muted px-5 py-4 md:flex-row md:items-center md:justify-between">
          <div className="flex items-center gap-2 text-sm text-ink-muted">
            <Funnel size={18} weight="duotone" />
            Filtered by {triageFilter === "all" ? "all triage lanes" : triageLabel(triageFilter)}.
          </div>
          <div className="flex items-center gap-2 text-sm text-ink-muted">
            <SortAscending size={18} weight="duotone" />
            Sorted by {humanizeToken(sortKey)}.
          </div>
        </div>
      </article>

      <section className="grid gap-gutter xl:grid-cols-[minmax(0,1.9fr)_minmax(18rem,0.72fr)] xl:items-start">
        <article className="clinical-panel overflow-hidden self-start">
          <div className="border-b border-line bg-surface-muted p-5">
            <div className="flex items-center gap-3">
              <Scales size={24} weight="duotone" />
              <div>
                <p className="label-caps text-ink-muted">Contrastive demo</p>
                <h2 className="mt-1 font-display text-xl font-semibold tracking-tight">ACT vs REVIEW vs DEFER</h2>
              </div>
            </div>
          </div>
          <div className="grid gap-3 p-5 md:grid-cols-2 xl:grid-cols-3">
            {triageOrder.map((triage, index) => {
              const item = contrastItems[index];
              return (
                <ContrastiveLane
                  item={item}
                  key={triage}
                  onSelect={(nextItem) => selectJob(nextItem.job_id)}
                  selected={Boolean(item && selectedItem?.job_id === item.job_id)}
                  snapshot={item ? snapshotFor(snapshots, item) : null}
                  triage={triage}
                />
              );
            })}
          </div>
        </article>

        <div className="grid content-start gap-gutter self-start">
          <article className="clinical-panel p-4">
            <div className="flex items-start gap-3">
              <Columns className="mt-0.5 text-ink" size={22} weight="duotone" />
              <div>
                <p className="label-caps text-ink-muted">Demo boundary</p>
                <p className="mt-2 text-sm leading-6 text-ink-muted">
                  This queue supports the presentation narrative only. It filters and compares loaded queue items, but
                  does not claim to be a full case-management workflow.
                </p>
              </div>
            </div>
          </article>

          <article className="clinical-panel p-4">
            <div className="flex items-start gap-3">
              <Target className="mt-0.5 text-ink" size={22} weight="duotone" />
              <div>
                <p className="label-caps text-ink-muted">Known-good cases</p>
                <p className="mt-2 text-sm leading-6 text-ink-muted">
                  In demo mode, the backend seeds one case per triage lane. In live or persisted mode, this panel
                  honestly reports whichever lanes are present in `/api/queue`.
                </p>
              </div>
            </div>
          </article>
        </div>
      </section>

      <section className="clinical-panel overflow-hidden">
        <div className="border-b border-line bg-surface-muted p-5">
          <div className="flex items-center gap-3">
            <Kanban size={24} weight="duotone" />
            <div>
              <p className="label-caps text-ink-muted">Queue narrative</p>
              <h2 className="mt-1 font-display text-xl font-semibold tracking-tight">Why this screen matters</h2>
            </div>
          </div>
        </div>
        <div className="grid gap-0 divide-y divide-line md:grid-cols-3 md:divide-x md:divide-y-0">
          <div className="p-5">
            <p className="label-caps text-act">ACT lane</p>
            <p className="mt-3 text-sm leading-6 text-ink-muted">
              Demonstrates high-confidence escalation without burying the required case drill-down.
            </p>
          </div>
          <div className="p-5">
            <p className="label-caps text-review">REVIEW lane</p>
            <p className="mt-3 text-sm leading-6 text-ink-muted">
              Shows ambiguous or high-novelty cases that need analyst interpretation before operational action.
            </p>
          </div>
          <div className="p-5">
            <p className="label-caps text-defer">DEFER lane</p>
            <p className="mt-3 text-sm leading-6 text-ink-muted">
              Keeps low-support cases visible while making it clear that lab confirmation is the next step.
            </p>
          </div>
        </div>
      </section>
    </div>
  );
}
