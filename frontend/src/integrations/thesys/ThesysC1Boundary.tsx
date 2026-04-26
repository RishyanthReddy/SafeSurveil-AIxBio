import { BracketsCurly, ShieldCheck, WarningDiamond } from "@phosphor-icons/react";
import { lazy, ReactNode, Suspense, useState } from "react";

import type { JobThesysC1Response } from "../../api/types";

const C1Runtime = lazy(() => import("./C1Runtime"));

type C1RenderError = {
  code: number;
  c1Response: string;
};

type ThesysC1BoundaryProps = {
  response: JobThesysC1Response;
  fallback: ReactNode;
};

function BoundaryStatus({
  response,
  renderError,
  lastAction,
}: {
  response: JobThesysC1Response;
  renderError: C1RenderError | null;
  lastAction: string | null;
}) {
  const isRendered = response.status === "rendered" && !renderError;
  return (
    <div className="flex flex-col gap-3 border-b border-line bg-surface-muted p-4 md:flex-row md:items-center md:justify-between">
      <div className="flex items-start gap-3">
        <div className="rounded border border-line bg-white p-2 text-ink">
          {isRendered ? <ShieldCheck size={20} weight="duotone" /> : <WarningDiamond size={20} weight="duotone" />}
        </div>
        <div>
          <p className="label-caps text-ink-muted">Thesys C1 boundary</p>
          <p className="mt-1 text-sm font-semibold text-ink">
            {isRendered ? "C1 renderer active" : "React fallback is preserving grounded content"}
          </p>
          {response.model ? (
            <p className="mt-1 font-data text-xs uppercase tracking-[0.12em] text-ink-muted">{response.model}</p>
          ) : null}
        </div>
      </div>
      <div className="max-w-xl text-sm leading-6 text-ink-muted md:text-right">
        {renderError
          ? `C1 render error ${renderError.code}; fallback remains available.`
          : lastAction
            ? lastAction
            : response.reason ?? response.output_origin.detail}
      </div>
    </div>
  );
}

export function ThesysC1Boundary({ response, fallback }: ThesysC1BoundaryProps) {
  const [renderError, setRenderError] = useState<C1RenderError | null>(null);
  const [lastAction, setLastAction] = useState<string | null>(null);
  const c1Response = response.c1_response?.trim() ?? "";
  const canRenderC1 = response.status === "rendered" && c1Response.length > 0 && renderError === null;

  return (
    <section className="clinical-panel overflow-hidden">
      <BoundaryStatus response={response} renderError={renderError} lastAction={lastAction} />
      {canRenderC1 ? (
        <div className="bg-white p-4">
          <Suspense
            fallback={
              <div className="rounded border border-line bg-surface-muted p-5">
                <p className="label-caps text-ink-muted">Preparing C1 runtime</p>
                <div className="mt-4 h-3 w-2/3 animate-pulse rounded bg-surface-strong" />
                <div className="mt-3 h-3 w-1/2 animate-pulse rounded bg-surface-strong" />
              </div>
            }
          >
            <C1Runtime
              c1Response={c1Response}
              onBoundaryAction={(message) => {
                setLastAction(
                  message || "C1 action captured at the renderer boundary.",
                );
              }}
              onBoundaryError={(error) => setRenderError(error)}
            />
          </Suspense>
        </div>
      ) : (
        <div className="grid gap-0 lg:grid-cols-[0.72fr_1.28fr]">
          <aside className="border-b border-line bg-surface-muted p-5 lg:border-b-0 lg:border-r">
            <BracketsCurly size={26} weight="duotone" />
            <p className="label-caps mt-5 text-ink-muted">Renderer contract</p>
            <h2 className="mt-3 font-display text-xl font-semibold">C1 is optional, content is not</h2>
            <p className="mt-3 text-sm leading-6 text-ink-muted">
              The backend owns the Thesys API call and passes only renderable C1 output to React. If C1 is unavailable,
              the exact semantic UI payload still renders through the local fallback.
            </p>
          </aside>
          <div className="p-5">{fallback}</div>
        </div>
      )}
    </section>
  );
}
