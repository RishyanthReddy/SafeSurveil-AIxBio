import { useParams } from "react-router-dom";

import { fetchCaseBundle } from "../api/client";
import { ApiErrorState, EmptyState, PanelSkeleton } from "../components/ApiState";
import { RouteHeader } from "../components/RouteHeader";
import { useApiResource } from "../hooks/useApiResource";
import { CaseDecisionScreen } from "../presentation/CaseDecisionScreen";
import { EvidenceGraphPanel } from "../presentation/EvidenceGraphPanel";
import { EvidenceMechanismView } from "../presentation/EvidenceMechanismView";
import { GroundedCopilotPanel } from "../presentation/GroundedCopilotPanel";
import { ReasoningTracePanel } from "../presentation/ReasoningTracePanel";
import { RiskVisualizationBlocks } from "../presentation/RiskVisualizationBlocks";
import { SemanticFallbackRenderer } from "../presentation/SemanticFallbackRenderer";

function CaseDetailJob({ jobId }: { jobId: string }) {
  const bundle = useApiResource((signal) => fetchCaseBundle(jobId, signal), [jobId]);

  return (
    <>
      <RouteHeader
        eyebrow={`Phase 8 / case detail / ${jobId}`}
        title="Case decision screen for grounded genomic triage"
        description="The top-level decision now foregrounds triage, severity, actionability, recommended next step, and rationale codes before deeper evidence surfaces."
      />

      {bundle.status === "loading" ? <PanelSkeleton /> : null}

      {bundle.status === "error" ? (
        <ApiErrorState title="Case bundle did not load" message={bundle.error} />
      ) : null}

      {bundle.status === "success" ? (
        <section className="flex flex-col gap-gutter">
          <CaseDecisionScreen bundle={bundle.data} jobId={jobId} />
          <ReasoningTracePanel bundle={bundle.data} />
          <EvidenceGraphPanel
            error={bundle.data.evidenceGraphError}
            graph={bundle.data.evidenceGraph}
          />
          <EvidenceMechanismView bundle={bundle.data} />
          <RiskVisualizationBlocks bundle={bundle.data} />
          <GroundedCopilotPanel bundle={bundle.data} jobId={jobId} />

          <div>
            <SemanticFallbackRenderer
              semanticUi={bundle.data.semanticUi.semantic_ui}
              title="Semantic UI adapter preview"
            />
          </div>
        </section>
      ) : null}
    </>
  );
}

export function CaseDetailRoute() {
  const { jobId } = useParams();

  if (!jobId) {
    return (
      <>
        <RouteHeader
          eyebrow="Phase 8 / case detail"
          title="Case decision screen for grounded genomic triage"
          description="Open a persisted job from the analyst queue so the case screen is anchored to live backend data."
        />
        <EmptyState
          title="Select a case from the live queue"
          message="No default demo job is loaded for case detail. Open a persisted job from the analyst queue or search for a real job ID."
        />
      </>
    );
  }

  return <CaseDetailJob jobId={jobId} />;
}
