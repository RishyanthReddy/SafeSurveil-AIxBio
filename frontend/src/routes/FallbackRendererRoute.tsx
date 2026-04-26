import { BracketsCurly, ChartLineUp } from "@phosphor-icons/react";
import { useSearchParams } from "react-router-dom";

import { fetchSemanticUi, fetchThesysC1Render } from "../api/client";
import { humanizeToken, triageLabel } from "../api/format";
import type { JobSemanticUIResponse, JobThesysC1Response } from "../api/types";
import { ApiErrorState, EmptyState, PanelSkeleton } from "../components/ApiState";
import { RouteHeader } from "../components/RouteHeader";
import { useApiResource } from "../hooks/useApiResource";
import { ThesysC1Boundary } from "../integrations/thesys/ThesysC1Boundary";
import { SemanticFallbackRenderer } from "../presentation/SemanticFallbackRenderer";

function CanonicalRendererContract({
  c1Render,
  semanticUi,
}: {
  c1Render: JobThesysC1Response;
  semanticUi: JobSemanticUIResponse;
}) {
  const decisionCard = semanticUi.semantic_ui.decision_card;
  const semanticNoteCount = semanticUi.semantic_ui.notes.length;

  return (
    <section className="clinical-panel border-l-[4px] border-l-live p-5">
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-start">
        <div>
          <p className="label-caps text-live">Backend contract first</p>
          <h2 className="mt-2 font-display text-2xl font-semibold tracking-tight text-ink">
            C1 is presentation-only; backend semantic UI stays canonical
          </h2>
          <p className="mt-3 max-w-4xl text-sm leading-6 text-ink-muted">
            This route intentionally renders the provider output inside a boundary. If C1 wording diverges from the
            live runtime or audit state, use the backend semantic UI, execution gate, and V2 audit cards as the source
            of truth.
          </p>
        </div>

        <div className="grid gap-2 sm:grid-cols-3 lg:min-w-[28rem]">
          <div className="rounded border border-line bg-surface-muted p-3">
            <p className="label-caps text-ink-muted">Semantic UI</p>
            <p className="mt-2 font-data text-xs font-bold uppercase tracking-[0.12em] text-ink">
              {humanizeToken(semanticUi.output_origin.mode)}
            </p>
          </div>
          <div className="rounded border border-line bg-surface-muted p-3">
            <p className="label-caps text-ink-muted">C1 status</p>
            <p className="mt-2 font-data text-xs font-bold uppercase tracking-[0.12em] text-ink">
              {humanizeToken(c1Render.status)}
            </p>
          </div>
          <div className="rounded border border-line bg-surface-muted p-3">
            <p className="label-caps text-ink-muted">Fallback</p>
            <p className="mt-2 font-data text-xs font-bold uppercase tracking-[0.12em] text-ink">
              {c1Render.fallback_required ? "Required" : "Not required"}
            </p>
          </div>
        </div>
      </div>

      <div className="mt-5 grid gap-3 border-t border-line pt-4 md:grid-cols-3">
        <div>
          <p className="label-caps text-ink-muted">Job</p>
          <p className="mt-1 break-words font-data text-xs font-semibold text-ink [overflow-wrap:anywhere]">
            {semanticUi.job_id}
          </p>
        </div>
        <div>
          <p className="label-caps text-ink-muted">Decision card</p>
          <p className="mt-1 font-data text-xs font-semibold uppercase tracking-[0.12em] text-ink">
            {decisionCard ? `${triageLabel(decisionCard.triage_decision)} / ${humanizeToken(decisionCard.severity)}` : "Unavailable"}
          </p>
        </div>
        <div>
          <p className="label-caps text-ink-muted">Backend notes</p>
          <p className="mt-1 font-data text-xs font-semibold uppercase tracking-[0.12em] text-ink">
            {semanticNoteCount} grounded notes visible
          </p>
        </div>
      </div>
    </section>
  );
}

function FallbackRendererJob({ jobId }: { jobId: string }) {
  const semanticUi = useApiResource((signal) => fetchSemanticUi(jobId, signal), [jobId]);
  const c1Render = useApiResource((signal) => fetchThesysC1Render(jobId, signal), [jobId]);

  return (
    <>
      <RouteHeader
        eyebrow="Phase 8 / renderer boundary"
        title="Thesys C1 primary render with React fallback"
        description={`The rendering boundary starts here for ${jobId}: C1 can enhance semantic UI, but the React fallback keeps decision evidence accessible.`}
      />

      {semanticUi.status === "loading" || c1Render.status === "loading" ? <PanelSkeleton /> : null}

      {semanticUi.status === "error" ? (
        <ApiErrorState title="Semantic UI did not load" message={semanticUi.error} />
      ) : null}

      {c1Render.status === "error" ? (
        <ApiErrorState title="Thesys boundary did not load" message={c1Render.error} />
      ) : null}

      {semanticUi.status === "success" && c1Render.status === "success" ? (
        <>
          <CanonicalRendererContract c1Render={c1Render.data} semanticUi={semanticUi.data} />
          <ThesysC1Boundary
            response={c1Render.data}
            fallback={
              <SemanticFallbackRenderer
                semanticUi={semanticUi.data.semantic_ui}
                title={`React fallback surface for ${jobId}`}
              />
            }
          />
        </>
      ) : null}

      <section className="grid gap-gutter lg:grid-cols-[0.85fr_1.15fr]">
        <article className="route-card">
          <BracketsCurly size={28} weight="duotone" />
          <p className="label-caps mt-5 text-ink-muted">Backend-owned C1 call</p>
          <h2 className="mt-3 font-display text-2xl font-semibold">Thesys key never enters the browser</h2>
          <p className="mt-3 text-sm leading-6 text-ink-muted">
            The React route calls <code>{`/api/jobs/${jobId}/semantic-ui/c1`}</code>; FastAPI reads{" "}
            <code>THESYS_API_KEY</code>, asks C1 for renderable UI, and returns either a C1 response string or a
            fallback status.
          </p>
        </article>

        <article className="route-card">
          <ChartLineUp size={28} weight="duotone" />
          <p className="label-caps mt-5 text-ink-muted">Guardrail</p>
          <h2 className="mt-3 font-display text-2xl font-semibold">Renderer failure cannot hide the case</h2>
          <p className="mt-3 text-sm leading-6 text-ink-muted">
            C1 output is treated as presentation-only. Decision, evidence, queue context, and notes remain visible from
            the backend semantic UI contract even when the C1 request or client render fails.
          </p>
        </article>
      </section>
    </>
  );
}

export function FallbackRendererRoute() {
  const [searchParams] = useSearchParams();
  const jobId = searchParams.get("jobId");

  if (!jobId) {
    return (
      <>
        <RouteHeader
          eyebrow="Phase 8 / renderer boundary"
          title="Thesys C1 primary render with React fallback"
          description="Open this renderer from a live case or acceptance run so C1 receives an explicit backend job context."
        />
        <EmptyState
          title="Select a live case first"
          message="No default demo job is loaded here. Open a persisted job from the analyst queue or pass ?jobId=... to render its semantic UI boundary."
        />
      </>
    );
  }

  return <FallbackRendererJob jobId={jobId} />;
}
