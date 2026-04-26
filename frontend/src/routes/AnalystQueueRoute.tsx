import { fetchJobDecision, fetchQueue } from "../api/client";
import { ApiErrorState, EmptyState, TableSkeleton } from "../components/ApiState";
import { AnalystQueueView } from "../presentation/AnalystQueueView";
import { RouteHeader } from "../components/RouteHeader";
import { useApiResource } from "../hooks/useApiResource";
import type { QueueDecisionSnapshot } from "../presentation/AnalystQueueView";

async function fetchQueueWithDecisionSnapshots(signal: AbortSignal) {
  const queue = await fetchQueue({ limit: 50, signal });
  const snapshots = await Promise.all(
    queue.items.map(async (item): Promise<[string, QueueDecisionSnapshot]> => {
      try {
        const decisionResponse = await fetchJobDecision(item.job_id, signal);
        const decision = decisionResponse.decision;
        return [
          item.job_id,
          {
            accession: decision.sample.metadata?.accession ?? null,
            actionabilityScore: decision.actionability_features.actionability_score,
            collectionDate: decision.sample.metadata?.collection_date ?? null,
            evidenceCount: decision.mechanistic_evidence.length,
            organism: decision.sample.organism_hint ?? null,
          },
        ];
      } catch (error: unknown) {
        return [
          item.job_id,
          {
            accession: null,
            actionabilityScore: null,
            collectionDate: null,
            evidenceCount: null,
            error: error instanceof Error ? error.message : "Decision snapshot unavailable.",
            organism: null,
          },
        ];
      }
    }),
  );
  return {
    items: queue.items,
    snapshots: Object.fromEntries(snapshots),
  };
}

export function AnalystQueueRoute() {
  const queue = useApiResource(fetchQueueWithDecisionSnapshots, []);

  return (
    <>
      <RouteHeader
        eyebrow="Phase 8 / analyst queue"
        title="Analyst queue and contrastive demo view"
        description="The stitched queue table is now an API-bound multi-case surface with triage filters, severity sorting, and ACT / REVIEW / DEFER comparison."
      />

      {queue.status === "loading" ? <TableSkeleton rows={5} /> : null}

      {queue.status === "error" ? (
        <ApiErrorState title="Queue data did not load" message={queue.error} />
      ) : null}

      {queue.status === "success" && queue.data.items.length === 0 ? (
        <EmptyState
          title="Queue is empty"
          message="No persisted or demo-seeded queue records are available from the backend."
        />
      ) : null}

      {queue.status === "success" && queue.data.items.length > 0 ? (
        <AnalystQueueView items={queue.data.items} snapshots={queue.data.snapshots} />
      ) : null}
    </>
  );
}
