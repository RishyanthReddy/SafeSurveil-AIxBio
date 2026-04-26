import type {
  AnalyzeJobRequest,
  AnalyzeJobResponse,
  ArtifactManifest,
  ArtifactPreviewResponse,
  CaseBundle,
  CopilotResponse,
  EvidenceGraph,
  ExecutionGateReport,
  HealthResponse,
  IntegrationHealthResponse,
  JobCopilotResponse,
  JobDecisionResponse,
  JobSemanticUIResponse,
  JobThesysC1Response,
  JobState,
  JobStatus,
  QueueSummaryResponse,
  ReasoningTrace,
  SemanticUIObject,
  TriageOutcome,
  V2AuditBundle,
} from "./types";

type QueryValue = string | number | boolean | null | undefined;
type QueryParams = Record<string, QueryValue>;

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(message: string, status: number, detail: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

function buildUrl(path: string, query?: QueryParams): string {
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value === undefined || value === null || value === "") {
      continue;
    }
    params.set(key, String(value));
  }
  const suffix = params.toString();
  return `/api${normalizedPath}${suffix ? `?${suffix}` : ""}`;
}

async function readErrorDetail(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as { detail?: unknown };
    if (typeof payload.detail === "string") {
      return payload.detail;
    }
    return JSON.stringify(payload.detail ?? payload);
  } catch {
    return response.statusText || "Request failed.";
  }
}

export async function requestJson<T>(
  path: string,
  options: {
    method?: "GET" | "POST";
    query?: QueryParams;
    signal?: AbortSignal;
    body?: unknown;
  } = {},
): Promise<T> {
  const headers: Record<string, string> = {
    Accept: "application/json",
  };
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
  }

  const response = await fetch(buildUrl(path, options.query), {
    method: options.method ?? "GET",
    headers,
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
    signal: options.signal,
  });

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new ApiError(`API request failed with ${response.status}.`, response.status, detail);
  }

  return (await response.json()) as T;
}

export function submitAnalyzeJob(
  payload: AnalyzeJobRequest,
  signal?: AbortSignal,
): Promise<AnalyzeJobResponse> {
  return requestJson<AnalyzeJobResponse>("/jobs/analyze", {
    method: "POST",
    body: payload,
    signal,
  });
}

export function fetchQueue(
  options: {
    triage?: TriageOutcome;
    status?: JobState;
    limit?: number;
    signal?: AbortSignal;
  } = {},
): Promise<QueueSummaryResponse> {
  return requestJson<QueueSummaryResponse>("/queue", {
    query: {
      triage: options.triage,
      status: options.status,
      limit: options.limit ?? 50,
    },
    signal: options.signal,
  });
}

export function fetchHealthStatus(signal?: AbortSignal): Promise<HealthResponse> {
  return requestJson<HealthResponse>("/health", { signal });
}

export function fetchIntegrationHealth(signal?: AbortSignal): Promise<IntegrationHealthResponse> {
  return requestJson<IntegrationHealthResponse>("/health/integrations", { signal });
}

export function fetchJobStatus(jobId: string, signal?: AbortSignal): Promise<JobStatus> {
  return requestJson<JobStatus>(`/jobs/${jobId}/status`, { signal });
}

export function fetchJobDecision(jobId: string, signal?: AbortSignal): Promise<JobDecisionResponse> {
  return requestJson<JobDecisionResponse>(`/jobs/${jobId}/decision`, { signal });
}

export function fetchArtifactManifest(jobId: string, signal?: AbortSignal): Promise<ArtifactManifest> {
  return requestJson<ArtifactManifest>(`/jobs/${jobId}/artifacts`, { signal });
}

export function fetchArtifactPreview(
  jobId: string,
  artifactId: string,
  options: {
    maxBytes?: number;
    signal?: AbortSignal;
  } = {},
): Promise<ArtifactPreviewResponse> {
  return requestJson<ArtifactPreviewResponse>(`/jobs/${jobId}/artifacts/${artifactId}/preview`, {
    query: { max_bytes: options.maxBytes ?? 4096 },
    signal: options.signal,
  });
}

export function fetchSemanticUi(jobId: string, signal?: AbortSignal): Promise<JobSemanticUIResponse> {
  return requestJson<JobSemanticUIResponse>(`/jobs/${jobId}/semantic-ui`, { signal });
}

export function fetchThesysC1Render(jobId: string, signal?: AbortSignal): Promise<JobThesysC1Response> {
  return requestJson<JobThesysC1Response>(`/jobs/${jobId}/semantic-ui/c1`, { signal });
}

export function fetchJobVerification(jobId: string, signal?: AbortSignal): Promise<ExecutionGateReport> {
  return requestJson<ExecutionGateReport>(`/jobs/${jobId}/verification`, { signal });
}

export function fetchReasoningTrace(jobId: string, signal?: AbortSignal): Promise<ReasoningTrace> {
  return requestJson<ReasoningTrace>(`/jobs/${jobId}/reasoning-trace`, { signal });
}

export function fetchEvidenceGraph(jobId: string, signal?: AbortSignal): Promise<EvidenceGraph> {
  return requestJson<EvidenceGraph>(`/jobs/${jobId}/evidence-graph`, { signal });
}

export function fetchV2AuditBundle(jobId: string, signal?: AbortSignal): Promise<V2AuditBundle> {
  return requestJson<V2AuditBundle>(`/jobs/${jobId}/v2-audit`, { signal });
}

export function fetchCopilotExplanation(
  jobId: string,
  options: {
    signal?: AbortSignal;
    refresh?: boolean;
  } = {},
): Promise<JobCopilotResponse> {
  return requestJson<JobCopilotResponse>(`/jobs/${jobId}/copilot/explanation`, {
    query: { refresh: options.refresh ?? false },
    signal: options.signal,
  });
}

export function fetchCopilotQueueSummary(jobId: string, signal?: AbortSignal): Promise<JobCopilotResponse> {
  return requestJson<JobCopilotResponse>(`/jobs/${jobId}/copilot/queue-summary`, { signal });
}

export function fetchCopilotAnswer(
  jobId: string,
  question: string,
  signal?: AbortSignal,
): Promise<JobCopilotResponse> {
  return requestJson<JobCopilotResponse>(`/jobs/${jobId}/copilot/answer`, {
    query: { question },
    signal,
  });
}

function sidecarErrorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return "Sidecar request failed.";
}

function fallbackCopilotResponse(
  status: JobStatus,
  decision: JobDecisionResponse,
  detail: string,
  error: unknown,
): JobCopilotResponse {
  const reason = `${detail}: ${sidecarErrorMessage(error)}`;
  const copilot: CopilotResponse = {
    job_id: status.job_id,
    sample_id: status.sample_id,
    target_drug: status.target_drug,
    summary: "Generated copilot language is unavailable for this case right now.",
    next_steps: [decision.decision.triage_decision.recommended_next_step],
    refusal_required: true,
    refusal_reason: reason,
    cited_evidence_ids: [],
    answer_blocks: [],
    warnings: [reason],
  };

  return {
    job_status: status,
    output_origin: {
      mode: "fallback",
      provider: "frontend",
      detail,
    },
    copilot,
  };
}

function fallbackSemanticUiResponse(
  status: JobStatus,
  decision: JobDecisionResponse,
  detail: string,
  error: unknown,
): JobSemanticUIResponse {
  const persistedDecision = decision.decision;
  const semanticUi: SemanticUIObject = {
    decision_card: {
      title: "Decision Overview",
      triage_decision: persistedDecision.triage_decision.triage,
      severity: persistedDecision.triage_decision.severity,
      summary: persistedDecision.triage_decision.recommended_next_step,
      metrics: [
        {
          key: "probability",
          label: "Probability",
          value: persistedDecision.phenotype_prediction.probability,
          unit: "%",
        },
        {
          key: "actionability_score",
          label: "Actionability",
          value: persistedDecision.actionability_features.actionability_score,
          unit: "%",
        },
      ],
    },
    evidence_table: {
      title: "Evidence Summary",
      columns: ["signal", "detail", "support"],
      rows: persistedDecision.mechanistic_evidence.slice(0, 3).map((row, index) => ({
        row_id: `fallback_evidence_${index + 1}`,
        label: row.gene_symbol ?? row.mutation ?? `Evidence ${index + 1}`,
        cells: {
          signal: row.gene_symbol ?? row.mutation ?? "Unavailable",
          detail: row.interpretation,
          support: row.support_level,
        },
        evidence_id: `mechanistic_evidence__${index + 1}`,
      })),
    },
    risk_charts: [
      {
        chart_id: "fallback_risk_overview",
        title: "Persisted Risk Overview",
        chart_type: "bar",
        points: [
          {
            label: "Probability",
            value: persistedDecision.phenotype_prediction.probability,
            evidence_id: "phenotype_prediction__summary",
          },
          {
            label: "Actionability",
            value: persistedDecision.actionability_features.actionability_score,
            evidence_id: "actionability_features__summary",
          },
          {
            label: "QC Risk",
            value: persistedDecision.actionability_features.qc_risk,
            evidence_id: "decision_object__assembly_qc",
          },
        ],
      },
    ],
    safety_profile: {
      title: "Persisted Safety Profile",
      axes: [
        {
          label: "Actionability",
          value: persistedDecision.actionability_features.actionability_score,
        },
        {
          label: "Metadata completeness",
          value: persistedDecision.actionability_features.metadata_completeness,
        },
      ],
    },
    queue_block: null,
    notes: [`${detail}: ${sidecarErrorMessage(error)}`],
  };

  return {
    job_id: status.job_id,
    output_origin: {
      mode: "fallback",
      provider: "frontend",
      detail,
    },
    semantic_ui: semanticUi,
  };
}

export async function fetchCaseBundle(jobId: string, signal?: AbortSignal): Promise<CaseBundle> {
  const [status, decision, artifacts] = await Promise.all([
    fetchJobStatus(jobId, signal),
    fetchJobDecision(jobId, signal),
    fetchArtifactManifest(jobId, signal),
  ]);
  const [
    semanticUiResult,
    explanationResult,
    queueSummaryResult,
    verificationResult,
    reasoningTraceResult,
    evidenceGraphResult,
  ] =
    await Promise.allSettled([
      fetchSemanticUi(jobId, signal),
      fetchCopilotExplanation(jobId, { signal }),
      fetchCopilotQueueSummary(jobId, signal),
      fetchJobVerification(jobId, signal),
      fetchReasoningTrace(jobId, signal),
      fetchEvidenceGraph(jobId, signal),
    ]);
  const semanticUi =
    semanticUiResult.status === "fulfilled"
      ? semanticUiResult.value
      : fallbackSemanticUiResponse(status, decision, "semantic_ui_unavailable", semanticUiResult.reason);
  const explanation =
    explanationResult.status === "fulfilled"
      ? explanationResult.value
      : fallbackCopilotResponse(status, decision, "explanation_unavailable", explanationResult.reason);
  const queueSummary =
    queueSummaryResult.status === "fulfilled"
      ? queueSummaryResult.value
      : fallbackCopilotResponse(status, decision, "queue_summary_unavailable", queueSummaryResult.reason);
  const verification = verificationResult.status === "fulfilled" ? verificationResult.value : null;
  const reasoningTrace = reasoningTraceResult.status === "fulfilled" ? reasoningTraceResult.value : null;
  const evidenceGraph = evidenceGraphResult.status === "fulfilled" ? evidenceGraphResult.value : null;

  return {
    status,
    decision,
    artifacts,
    semanticUi,
    explanation,
    queueSummary,
    verification,
    reasoningTrace,
    evidenceGraph,
    verificationError:
      verificationResult.status === "rejected" ? sidecarErrorMessage(verificationResult.reason) : undefined,
    reasoningTraceError:
      reasoningTraceResult.status === "rejected" ? sidecarErrorMessage(reasoningTraceResult.reason) : undefined,
    evidenceGraphError:
      evidenceGraphResult.status === "rejected" ? sidecarErrorMessage(evidenceGraphResult.reason) : undefined,
  };
}
