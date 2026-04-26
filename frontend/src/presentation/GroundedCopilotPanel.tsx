import {
  Brain,
  ChatCircleText,
  LinkSimple,
  PaperPlaneTilt,
  Receipt,
  Robot,
  SealCheck,
  Sparkle,
  Timer,
  WarningCircle,
} from "@phosphor-icons/react";
import { useState, type FormEvent } from "react";

import { fetchCopilotAnswer, fetchCopilotQueueSummary } from "../api/client";
import { displayText, humanizeToken } from "../api/format";
import type {
  ArtifactManifest,
  ArtifactRecord,
  CaseBundle,
  CopilotAnswerBlock,
  CopilotOutputMode,
  CopilotOutputOrigin,
  CopilotResponse,
  JobCopilotResponse,
} from "../api/types";

type GroundedCopilotPanelProps = {
  bundle: CaseBundle;
  jobId: string;
};

type AnswerState =
  | { status: "idle"; response: null; message: null }
  | { status: "loading"; response: null; message: null }
  | { status: "success"; response: JobCopilotResponse; message: null }
  | { status: "error"; response: null; message: string };

type QueueSummaryState =
  | { status: "ready"; response: JobCopilotResponse; message: null }
  | { status: "loading"; response: JobCopilotResponse; message: null }
  | { status: "error"; response: JobCopilotResponse; message: string };

const suggestedQuestions = [
  "Which evidence IDs support this recommendation?",
  "What should the analyst verify before action?",
  "Why was this case triaged this way?",
];

function errorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return "Copilot request failed.";
}

function modeLabel(mode: CopilotOutputMode): string {
  if (mode === "live_llm") {
    return "Live LLM";
  }
  return humanizeToken(mode);
}

function modeClass(mode: CopilotOutputMode): string {
  if (mode === "live_llm") {
    return "border-act bg-red-50 text-act";
  }
  if (mode === "cached") {
    return "border-line bg-white text-ink";
  }
  if (mode === "fallback") {
    return "border-review bg-amber-50 text-review";
  }
  return "border-defer bg-slate-100 text-defer";
}

function sourceLine(origin: CopilotOutputOrigin): string {
  const pieces = [origin.provider, origin.detail].filter((piece): piece is string => Boolean(piece));
  return pieces.length > 0 ? pieces.join(" / ") : "No provider detail reported";
}

function collectCitationIds(copilot: CopilotResponse): string[] {
  const ids = new Set(copilot.cited_evidence_ids);
  for (const block of copilot.answer_blocks) {
    for (const evidenceId of block.cited_evidence_ids) {
      ids.add(evidenceId);
    }
  }
  return Array.from(ids);
}

function artifactById(manifest: ArtifactManifest): Map<string, ArtifactRecord> {
  return new Map(manifest.artifacts.map((artifact) => [artifact.artifact_id, artifact]));
}

function CitationChips({
  artifactLookup,
  citedEvidenceIds,
}: {
  artifactLookup: Map<string, ArtifactRecord>;
  citedEvidenceIds: string[];
}) {
  if (citedEvidenceIds.length === 0) {
    return (
      <p className="rounded border border-dashed border-line bg-surface-muted px-3 py-2 text-xs leading-5 text-ink-muted">
        No cited evidence IDs were supplied with this copilot response.
      </p>
    );
  }

  return (
    <div className="flex flex-wrap gap-2">
      {citedEvidenceIds.map((evidenceId) => {
        const artifact = artifactLookup.get(evidenceId);
        return (
          <span
            className="inline-flex max-w-full items-center gap-2 rounded border border-line bg-white px-2.5 py-1.5 text-left font-data text-[0.68rem] uppercase tracking-[0.1em] text-ink-muted"
            key={evidenceId}
          >
            <LinkSimple size={14} weight="bold" />
            <span className="truncate">{evidenceId}</span>
            <span className="rounded bg-surface-muted px-1.5 py-0.5 text-[0.58rem]">
              {artifact ? humanizeToken(artifact.kind) : "Context ID"}
            </span>
          </span>
        );
      })}
    </div>
  );
}

function OriginBadge({ origin }: { origin: CopilotOutputOrigin }) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className={`rounded border px-2.5 py-1 font-data text-xs font-bold uppercase tracking-[0.12em] ${modeClass(origin.mode)}`}>
        {modeLabel(origin.mode)}
      </span>
      <span className="rounded border border-line bg-surface-muted px-2.5 py-1 font-data text-xs text-ink-muted">
        {sourceLine(origin)}
      </span>
    </div>
  );
}

function GuardrailFlags({ response }: { response: JobCopilotResponse }) {
  const citationCount = collectCitationIds(response.copilot).length;
  const flags = [
    citationCount > 0 ? "Evidence-bound" : "No citations",
    response.output_origin.mode === "cached" ? "Cached artifact" : modeLabel(response.output_origin.mode),
    response.copilot.refusal_required ? "Refusal path" : "Answer path",
  ];

  return (
    <div className="flex flex-wrap gap-2">
      {flags.map((flag) => (
        <span className="rounded border border-line bg-white px-2.5 py-1 font-data text-[0.68rem] uppercase tracking-[0.12em] text-ink-muted" key={flag}>
          {flag}
        </span>
      ))}
    </div>
  );
}

function renderBlockContent(block: CopilotAnswerBlock) {
  const lines = block.content
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);

  if (block.block_type === "bullets" || block.block_type === "next_steps") {
    const items = lines.length > 1 ? lines : block.content.split(/[;•]/).map((line) => line.trim()).filter(Boolean);
    return (
      <ul className="mt-3 grid gap-2 text-sm leading-6 text-ink-muted">
        {items.map((item) => (
          <li className="rounded border border-line bg-surface-muted px-3 py-2" key={item}>
            {item}
          </li>
        ))}
      </ul>
    );
  }

  return <p className="mt-3 text-sm leading-6 text-ink-muted">{block.content}</p>;
}

function CopilotBlocks({
  artifactLookup,
  copilot,
}: {
  artifactLookup: Map<string, ArtifactRecord>;
  copilot: CopilotResponse;
}) {
  if (copilot.refusal_required) {
    return (
      <div className="rounded-lg border border-review bg-amber-50 p-4">
        <div className="flex items-start gap-3">
          <WarningCircle className="mt-0.5 text-review" size={22} weight="duotone" />
          <div>
            <p className="label-caps text-review">Copilot refused safely</p>
            <p className="mt-2 text-sm leading-6 text-ink-muted">
              {copilot.refusal_reason ?? "The model did not have enough grounded context to answer."}
            </p>
          </div>
        </div>
      </div>
    );
  }

  if (copilot.answer_blocks.length === 0 && !copilot.summary) {
    return (
      <div className="rounded-lg border border-dashed border-line bg-surface-muted p-4">
        <p className="label-caps text-ink-muted">No answer body</p>
        <p className="mt-2 text-sm leading-6 text-ink-muted">
          The backend returned a valid copilot envelope without narrative blocks.
        </p>
      </div>
    );
  }

  return (
    <div className="grid gap-3">
      {copilot.summary ? (
        <div className="rounded-lg border border-line bg-white p-4">
          <p className="label-caps text-ink-muted">Summary</p>
          <p className="mt-3 text-sm leading-6 text-ink-muted">{copilot.summary}</p>
        </div>
      ) : null}

      {copilot.answer_blocks.map((block) => {
        const citedEvidenceIds = block.cited_evidence_ids;
        return (
          <article
            className={`rounded-lg border p-4 ${
              block.block_type === "refusal" ? "border-review bg-amber-50" : "border-line bg-white"
            }`}
            key={block.block_id}
          >
            <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
              <div>
                <p className="label-caps text-ink-muted">{humanizeToken(block.block_type)}</p>
                {block.title ? <h3 className="mt-2 font-display text-lg font-semibold tracking-tight">{block.title}</h3> : null}
              </div>
              <span className="rounded border border-line bg-surface-muted px-2 py-1 font-data text-[0.65rem] uppercase tracking-[0.12em] text-ink-muted">
                {citedEvidenceIds.length} citation{citedEvidenceIds.length === 1 ? "" : "s"}
              </span>
            </div>
            {renderBlockContent(block)}
            <div className="mt-4">
              <CitationChips artifactLookup={artifactLookup} citedEvidenceIds={citedEvidenceIds} />
            </div>
          </article>
        );
      })}
    </div>
  );
}

function CopilotResponseCard({
  artifactLookup,
  eyebrow,
  response,
  title,
}: {
  artifactLookup: Map<string, ArtifactRecord>;
  eyebrow: string;
  response: JobCopilotResponse;
  title: string;
}) {
  const citedEvidenceIds = collectCitationIds(response.copilot);

  return (
    <article className="clinical-panel overflow-hidden">
      <div className="border-b border-line bg-surface-muted p-5">
        <div className="flex flex-col gap-3 xl:flex-row xl:items-start xl:justify-between">
          <div>
            <p className="label-caps text-ink-muted">{eyebrow}</p>
            <h2 className="mt-2 font-display text-2xl font-semibold tracking-tight">{title}</h2>
          </div>
          <OriginBadge origin={response.output_origin} />
        </div>
        <div className="mt-4">
          <GuardrailFlags response={response} />
        </div>
      </div>

      <div className="grid gap-5 p-5">
        <CopilotBlocks artifactLookup={artifactLookup} copilot={response.copilot} />

        {response.copilot.next_steps.length > 0 ? (
          <div className="rounded-lg border border-line bg-ink p-4 text-white">
            <p className="label-caps text-slate-300">Next steps</p>
            <div className="mt-3 grid gap-2">
              {response.copilot.next_steps.map((step) => (
                <p className="rounded border border-white/10 bg-white/5 px-3 py-2 text-sm leading-6 text-slate-100" key={step}>
                  {step}
                </p>
              ))}
            </div>
          </div>
        ) : null}

        <div>
          <p className="label-caps text-ink-muted">Response citations</p>
          <div className="mt-3">
            <CitationChips artifactLookup={artifactLookup} citedEvidenceIds={citedEvidenceIds} />
          </div>
        </div>

        {response.copilot.warnings.length > 0 ? (
          <div className="rounded-lg border border-review bg-amber-50 p-4">
            <p className="label-caps text-review">Copilot warnings</p>
            <div className="mt-3 flex flex-wrap gap-2">
              {response.copilot.warnings.map((warning) => (
                <span className="rounded border border-line bg-white px-2.5 py-1 text-xs text-ink-muted" key={warning}>
                  {displayText(warning)}
                </span>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </article>
  );
}

export function GroundedCopilotPanel({ bundle, jobId }: GroundedCopilotPanelProps) {
  const artifactLookup = artifactById(bundle.artifacts);
  const [question, setQuestion] = useState(suggestedQuestions[0]);
  const [answerState, setAnswerState] = useState<AnswerState>({
    status: "idle",
    response: null,
    message: null,
  });
  const [queueState, setQueueState] = useState<QueueSummaryState>({
    status: "ready",
    response: bundle.queueSummary,
    message: null,
  });

  async function runQuestion(nextQuestion: string) {
    const trimmedQuestion = nextQuestion.trim();
    if (trimmedQuestion.length < 8) {
      setAnswerState({
        status: "error",
        response: null,
        message: "Ask a specific grounded question with at least 8 characters.",
      });
      return;
    }

    setQuestion(trimmedQuestion);
    setAnswerState({ status: "loading", response: null, message: null });
    try {
      const response = await fetchCopilotAnswer(jobId, trimmedQuestion);
      setAnswerState({ status: "success", response, message: null });
    } catch (error: unknown) {
      setAnswerState({ status: "error", response: null, message: errorMessage(error) });
    }
  }

  async function refreshQueueSummary() {
    setQueueState((current) => ({ status: "loading", response: current.response, message: null }));
    try {
      const response = await fetchCopilotQueueSummary(jobId);
      setQueueState({ status: "ready", response, message: null });
    } catch (error: unknown) {
      setQueueState((current) => ({ status: "error", response: current.response, message: errorMessage(error) }));
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void runQuestion(question);
  }

  return (
    <section className="grid gap-gutter">
      <CopilotResponseCard
        artifactLookup={artifactLookup}
        eyebrow="Grounded copilot"
        response={bundle.explanation}
        title="Case explanation"
      />

      <div className="grid gap-gutter">
        <article className="clinical-panel overflow-hidden">
          <div className="border-b border-line bg-surface-muted p-5">
            <div className="flex items-center gap-3">
              <ChatCircleText size={24} weight="duotone" />
              <div>
                <p className="label-caps text-ink-muted">Analyst question</p>
                <h2 className="mt-1 font-display text-2xl font-semibold tracking-tight">Ask within the evidence boundary</h2>
              </div>
            </div>
          </div>

          <form className="grid gap-4 p-5" onSubmit={handleSubmit}>
            <label className="grid gap-2">
              <span className="label-caps text-ink-muted">Question</span>
              <textarea
                className="min-h-28 resize-y rounded-lg border border-line bg-white p-4 text-sm leading-6 text-ink outline-none transition focus:border-ink focus:ring-2 focus:ring-slate-200"
                onChange={(event) => setQuestion(event.target.value)}
                placeholder="Ask why this decision was made, which evidence IDs support it, or what to verify next."
                value={question}
              />
            </label>

            <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
              <div className="flex flex-wrap gap-2">
                {suggestedQuestions.map((suggestedQuestion) => (
                  <button
                    className="rounded border border-line bg-surface-muted px-3 py-2 text-left text-xs font-semibold text-ink-muted transition hover:border-ink hover:bg-white active:scale-[0.98]"
                    key={suggestedQuestion}
                    onClick={() => void runQuestion(suggestedQuestion)}
                    type="button"
                  >
                    {suggestedQuestion}
                  </button>
                ))}
              </div>
              <button
                className="inline-flex items-center justify-center gap-2 rounded bg-ink px-4 py-3 font-data text-xs font-bold uppercase tracking-[0.12em] text-white transition hover:bg-slate-700 active:scale-[0.98] disabled:cursor-not-allowed disabled:bg-slate-400"
                disabled={answerState.status === "loading"}
                type="submit"
              >
                {answerState.status === "loading" ? "Asking" : "Ask copilot"}
                <PaperPlaneTilt size={16} weight="bold" />
              </button>
            </div>
          </form>

          <div className="border-t border-line p-5">
            {answerState.status === "idle" ? (
              <div className="rounded-lg border border-dashed border-line bg-surface-muted p-4">
                <p className="label-caps text-ink-muted">No question submitted</p>
                <p className="mt-2 text-sm leading-6 text-ink-muted">
                  Answers will show citations, output origin, refusal status, and any backend warnings.
                </p>
              </div>
            ) : null}

            {answerState.status === "loading" ? (
              <div className="rounded-lg border border-line bg-ink p-5 text-white">
                <p className="label-caps text-slate-300">Copilot working</p>
                <div className="mt-4 space-y-2">
                  <div className="h-3 w-11/12 animate-pulse rounded bg-white/20" />
                  <div className="h-3 w-3/4 animate-pulse rounded bg-white/20" />
                  <div className="h-3 w-5/6 animate-pulse rounded bg-white/20" />
                </div>
              </div>
            ) : null}

            {answerState.status === "error" ? (
              <div className="rounded-lg border border-act bg-red-50 p-4">
                <p className="label-caps text-act">Copilot unavailable</p>
                <p className="mt-2 text-sm leading-6 text-ink-muted">{answerState.message}</p>
              </div>
            ) : null}

            {answerState.status === "success" ? (
              <CopilotResponseCard
                artifactLookup={artifactLookup}
                eyebrow="Grounded answer"
                response={answerState.response}
                title="Evidence-linked response"
              />
            ) : null}
          </div>
        </article>

        <article className="clinical-panel overflow-hidden">
          <div className="border-b border-line bg-surface-muted p-5">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
              <div className="flex items-center gap-3">
                <Robot size={24} weight="duotone" />
                <div>
                  <p className="label-caps text-ink-muted">Queue summary</p>
                  <h2 className="mt-1 font-display text-xl font-semibold tracking-tight">Current case in queue context</h2>
                </div>
              </div>
              <button
                className="inline-flex items-center justify-center gap-2 rounded border border-line bg-white px-3 py-2 font-data text-[0.68rem] font-bold uppercase tracking-[0.12em] text-ink-muted transition hover:border-ink active:scale-[0.98] disabled:cursor-not-allowed disabled:text-slate-400"
                disabled={queueState.status === "loading"}
                onClick={() => void refreshQueueSummary()}
                type="button"
              >
                {queueState.status === "loading" ? "Refreshing" : "Refresh"}
                <Timer size={15} weight="bold" />
              </button>
            </div>
          </div>
          <div className="grid gap-4 p-5">
            <OriginBadge origin={queueState.response.output_origin} />
            <CopilotBlocks artifactLookup={artifactLookup} copilot={queueState.response.copilot} />
            {queueState.status === "error" ? (
              <div className="rounded-lg border border-act bg-red-50 p-4">
                <p className="label-caps text-act">Refresh failed</p>
                <p className="mt-2 text-sm leading-6 text-ink-muted">{queueState.message}</p>
              </div>
            ) : null}
          </div>
        </article>
      </div>

      <div className="clinical-panel overflow-hidden">
        <div className="divide-y divide-line">
          <article className="p-5">
            <div className="flex items-start gap-3">
              <SealCheck className="mt-0.5 text-ink" size={24} weight="duotone" />
              <div>
                <p className="label-caps text-ink-muted">Grounding contract</p>
                <p className="mt-3 text-sm leading-6 text-ink-muted">
                  This panel renders only backend-returned copilot payloads. It labels live, cached, fallback, and mock
                  modes exactly as reported by the API and exposes missing citations instead of hiding them.
                </p>
              </div>
            </div>
          </article>

          <article className="p-5">
            <div className="flex items-start gap-3">
              <Brain className="mt-0.5 text-ink" size={24} weight="duotone" />
              <div>
                <p className="label-caps text-ink-muted">Safe prompt lane</p>
                <p className="mt-3 text-sm leading-6 text-ink-muted">
                  Good questions ask for cited evidence, analyst verification, or decision rationale. If the backend
                  refuses a weakly grounded request, the refusal is displayed as a first-class result.
                </p>
              </div>
            </div>
          </article>

          <article className="p-5">
            <div className="flex items-start gap-3">
              <Receipt className="mt-0.5 text-ink" size={24} weight="duotone" />
              <div>
                <p className="label-caps text-ink-muted">Artifact coverage</p>
                <p className="mt-3 text-sm leading-6 text-ink-muted">
                  {bundle.artifacts.artifacts.length} manifest artifact
                  {bundle.artifacts.artifacts.length === 1 ? "" : "s"} can be matched against cited evidence IDs for
                  this case.
                </p>
              </div>
            </div>
          </article>

          <article className="border-l-[4px] border-l-review p-5">
            <div className="flex items-start gap-3">
              <Sparkle className="mt-0.5 text-review" size={24} weight="duotone" />
              <div>
                <p className="label-caps text-review">Design note</p>
                <p className="mt-3 text-sm leading-6 text-ink-muted">
                  The copilot is intentionally a sidecar, not the decision source. The persisted decision and evidence
                  tables stay visually upstream of generated language.
                </p>
              </div>
            </div>
          </article>
        </div>
      </div>
    </section>
  );
}
