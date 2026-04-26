import { ArrowRight } from "@phosphor-icons/react";
import { Link } from "react-router-dom";

import { fetchQueue } from "../api/client";
import { formatCount, triageLabel } from "../api/format";
import type { QueueItem, TriageOutcome } from "../api/types";
import { ApiErrorState, TableSkeleton } from "../components/ApiState";
import { RouteHeader } from "../components/RouteHeader";
import { useApiResource } from "../hooks/useApiResource";
import { SystemReadinessPanel } from "../presentation/SystemReadinessPanel";

const triageOrder: TriageOutcome[] = ["act", "review", "defer_to_lab"];

function triageCount(items: QueueItem[], triage: TriageOutcome): number {
  return items.filter((item) => item.triage === triage).length;
}

export function DashboardOverviewRoute() {
  const queue = useApiResource((signal) => fetchQueue({ limit: 50, signal }), []);

  return (
    <>
      <RouteHeader
        eyebrow="Phase 8 / dashboard overview"
        title="Clinical command surface for grounded genomic triage"
        description="This route now combines persisted queue state with live backend readiness so operators can see whether the system is actually prepared for a truthful demo run."
      />

      {queue.status === "loading" ? <TableSkeleton rows={4} /> : null}

      {queue.status === "error" ? (
        <ApiErrorState title="Dashboard data did not load" message={queue.error} />
      ) : null}

      {queue.status === "success" ? (
        <section className="grid gap-gutter md:grid-cols-3">
          {triageOrder.map((triage) => {
            const count = triageCount(queue.data.items, triage);
            const tone =
              triage === "act"
                ? "border-l-act text-act"
                : triage === "review"
                  ? "border-l-review text-review"
                  : "border-l-defer text-defer";
            return (
              <Link
                className={`clinical-panel border-l-[3px] p-5 transition hover:-translate-y-0.5 hover:border-slate-700 active:translate-y-0 ${tone}`}
                key={triage}
                to={`/queue?triage=${triage}`}
              >
                <p className="label-caps text-ink-muted">{triageLabel(triage)}</p>
                <div className="mt-5 font-display text-5xl font-bold leading-none tracking-[-0.04em]">
                  {formatCount(count)}
                </div>
                <p className="mt-3 font-data text-xs uppercase tracking-[0.14em] text-ink-muted">
                  Queue records
                </p>
              </Link>
            );
          })}
        </section>
      ) : null}

      <SystemReadinessPanel
        actionPanel={
          <aside className="route-card self-start">
            <p className="label-caps text-ink-muted">Next operator action</p>
            <h2 className="mt-4 font-display text-2xl font-semibold tracking-tight">Submit a live analysis</h2>
            <p className="mt-3 text-sm leading-6 text-ink-muted">
              Once the runtime banner is clear and integrations are healthy, create a fresh job through the real
              analysis flow and follow it across queue, case detail, copilot, and C1 surfaces.
            </p>
            <div className="mt-6 flex flex-wrap gap-3">
              <Link
                className="inline-flex items-center gap-2 rounded bg-ink px-4 py-2.5 text-xs font-bold uppercase tracking-[0.14em] text-white transition hover:bg-slate-800 active:scale-[0.98]"
                to="/analysis/new"
              >
                New Analysis
                <ArrowRight size={16} weight="bold" />
              </Link>
              <Link
                className="inline-flex items-center gap-2 rounded border border-line bg-white px-4 py-2.5 text-xs font-bold uppercase tracking-[0.14em] text-ink transition hover:border-slate-700 active:scale-[0.98]"
                to="/queue"
              >
                Open Queue
                <ArrowRight size={16} weight="bold" />
              </Link>
            </div>
          </aside>
        }
      />
    </>
  );
}
