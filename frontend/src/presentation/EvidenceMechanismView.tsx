import { ArrowRight, Database, FileMagnifyingGlass, ShieldWarning, WarningCircle } from "@phosphor-icons/react";
import { useState } from "react";

import { fetchArtifactPreview } from "../api/client";
import { displayText, formatCount, humanizeToken } from "../api/format";
import type { ArtifactPreviewResponse, ArtifactRecord, CaseBundle, MechanisticEvidence } from "../api/types";

type EvidenceMechanismViewProps = {
  bundle: CaseBundle;
};

type PreviewState =
  | { status: "idle"; artifactId: string | null }
  | { status: "loading"; artifactId: string }
  | { status: "success"; artifactId: string; data: ArtifactPreviewResponse }
  | { status: "error"; artifactId: string; message: string };

type SupportTone = {
  label: string;
  className: string;
  description: string;
};

const supportToneByLevel: Record<string, SupportTone> = {
  supported: {
    label: "Supported",
    className: "border-act bg-red-50 text-act",
    description: "Mechanism supports the drug-context signal.",
  },
  partial: {
    label: "Partial",
    className: "border-review bg-amber-50 text-review",
    description: "Evidence is present but requires analyst interpretation.",
  },
  weak: {
    label: "Weak",
    className: "border-review bg-amber-50 text-review",
    description: "Evidence is low-confidence and should not drive action alone.",
  },
  screen_only: {
    label: "Screen only",
    className: "border-defer bg-slate-100 text-defer",
    description: "Screening hit needs confirmation before operational use.",
  },
};

function previewErrorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return "Unable to load artifact preview.";
}

function formatBytes(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "Unknown size";
  }
  if (value < 1024) {
    return `${value} B`;
  }
  if (value < 1024 * 1024) {
    return `${(value / 1024).toFixed(1)} KB`;
  }
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function supportTone(level: string | null | undefined): SupportTone {
  if (!level) {
    return {
      label: "Unknown",
      className: "border-line bg-surface-muted text-ink-muted",
      description: "Support level was not reported.",
    };
  }
  return (
    supportToneByLevel[level] ?? {
      label: humanizeToken(level),
      className: "border-line bg-surface-muted text-ink-muted",
      description: "Support level is outside the expected display set.",
    }
  );
}

function evidenceLabel(row: MechanisticEvidence): string {
  return row.gene_symbol ?? row.mutation ?? "Unlabeled mechanism";
}

function drugAssociationLabel(row: MechanisticEvidence): string {
  if (row.drug_association.length === 0) {
    return "No drug association reported";
  }
  return row.drug_association.map((drug) => humanizeToken(drug)).join(", ");
}

function artifactKindLabel(artifact: ArtifactRecord): string {
  return humanizeToken(artifact.kind);
}

function dedupeWarnings(values: string[]): string[] {
  const seen = new Set<string>();
  const unique: string[] = [];
  for (const value of values) {
    const trimmed = value.trim();
    if (!trimmed) {
      continue;
    }
    const normalized = trimmed.toLowerCase().replace(/[._\s]+/g, " ");
    if (seen.has(normalized)) {
      continue;
    }
    seen.add(normalized);
    unique.push(trimmed);
  }
  return unique;
}

function collectEvidenceArtifacts(bundle: CaseBundle): ArtifactRecord[] {
  const rawArtifactIds = new Set(
    bundle.decision.decision.mechanistic_evidence
      .map((row) => row.raw_artifact_id)
      .filter((artifactId): artifactId is string => Boolean(artifactId)),
  );
  return bundle.artifacts.artifacts.filter(
    (artifact) => artifact.kind === "mechanistic_evidence" || rawArtifactIds.has(artifact.artifact_id),
  );
}

function EmptyMechanismState({ warnings }: { warnings: string[] }) {
  return (
    <div className="clinical-panel border-l-[4px] border-l-defer p-6">
      <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
        <div>
          <p className="label-caps text-defer">No mechanistic hit</p>
          <h2 className="mt-3 font-display text-2xl font-semibold tracking-tight">
            Mechanistic evidence is absent for this decision.
          </h2>
          <p className="mt-3 max-w-[68ch] text-sm leading-6 text-ink-muted">
            The UI is intentionally explicit here so missing mechanism support is not buried behind the phenotype score.
            Actionability must be interpreted through the persisted decision warnings and policy rationale.
          </p>
        </div>
        <ShieldWarning size={34} weight="duotone" className="text-defer" />
      </div>
      {warnings.length > 0 ? (
        <div className="mt-5 grid gap-2">
              {warnings.map((warning) => (
                <div className="rounded border border-line bg-surface-muted px-3 py-2 text-sm text-ink-muted" key={warning}>
                  {displayText(warning)}
                </div>
              ))}
        </div>
      ) : null}
    </div>
  );
}

function PreviewPanel({
  state,
  selectedArtifact,
}: {
  state: PreviewState;
  selectedArtifact: ArtifactRecord | null;
}) {
  if (!selectedArtifact) {
    return (
      <div className="rounded-lg border border-dashed border-line bg-surface-muted p-5">
        <p className="label-caps text-ink-muted">Preview</p>
        <p className="mt-3 text-sm leading-6 text-ink-muted">
          Select a previewable artifact to inspect bounded browser-safe content.
        </p>
      </div>
    );
  }

  if (!selectedArtifact.preview_eligible) {
    return (
      <div className="rounded-lg border border-line bg-surface-muted p-5">
        <p className="label-caps text-defer">Preview unavailable</p>
        <p className="mt-3 text-sm leading-6 text-ink-muted">
          This artifact is not marked previewable by the backend, so the browser view withholds raw content.
        </p>
      </div>
    );
  }

  if (state.status === "loading" && state.artifactId === selectedArtifact.artifact_id) {
    return (
      <div className="rounded-lg border border-line bg-ink p-5 text-white">
        <p className="label-caps text-slate-300">Preview loading</p>
        <div className="mt-4 space-y-2">
          <div className="h-3 w-4/5 animate-pulse rounded bg-white/20" />
          <div className="h-3 w-2/3 animate-pulse rounded bg-white/20" />
          <div className="h-3 w-5/6 animate-pulse rounded bg-white/20" />
        </div>
      </div>
    );
  }

  if (state.status === "error" && state.artifactId === selectedArtifact.artifact_id) {
    return (
      <div className="rounded-lg border border-act bg-red-50 p-5">
        <p className="label-caps text-act">Preview failed</p>
        <p className="mt-3 text-sm leading-6 text-ink-muted">{state.message}</p>
      </div>
    );
  }

  if (state.status === "success" && state.artifactId === selectedArtifact.artifact_id) {
    const preview = state.data;
    return (
      <div className="overflow-hidden rounded-lg border border-line bg-ink text-white">
        <div className="flex flex-col gap-2 border-b border-white/10 p-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <p className="label-caps text-slate-300">Bounded artifact preview</p>
            <p className="mt-2 break-all font-data text-xs text-slate-200">{preview.artifact_id}</p>
          </div>
          <span className="rounded border border-white/10 px-2 py-1 font-data text-[0.68rem] uppercase tracking-[0.12em] text-slate-300">
            {preview.truncated ? "Truncated" : "Complete"}
          </span>
        </div>
        {preview.encoding === "utf-8" ? (
          <pre className="max-h-80 overflow-auto p-4 font-data text-xs leading-6 text-slate-100">
            {preview.content}
          </pre>
        ) : (
          <div className="p-4">
            <p className="text-sm leading-6 text-slate-200">
              Binary preview content is available only as {preview.encoding}; raw rendering is withheld in this table.
            </p>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-line bg-surface-muted p-5">
      <p className="label-caps text-ink-muted">Preview ready</p>
      <p className="mt-3 text-sm leading-6 text-ink-muted">
        Use the artifact list to request up to 4 KB of preview content through the backend route.
      </p>
    </div>
  );
}

export function EvidenceMechanismView({ bundle }: EvidenceMechanismViewProps) {
  const decision = bundle.decision.decision;
  const rows = decision.mechanistic_evidence;
  const evidenceArtifacts = collectEvidenceArtifacts(bundle);
  const citedEvidenceIds = new Set(bundle.explanation.copilot.cited_evidence_ids);
  const warnings = dedupeWarnings([
    ...decision.warnings,
    ...decision.triage_decision.warnings,
    ...decision.actionability_features.warnings,
  ]);
  const artifactsById = new Map(bundle.artifacts.artifacts.map((artifact) => [artifact.artifact_id, artifact]));
  const [previewState, setPreviewState] = useState<PreviewState>({
    status: "idle",
    artifactId: evidenceArtifacts[0]?.artifact_id ?? null,
  });
  const selectedArtifact =
    previewState.artifactId !== null ? artifactsById.get(previewState.artifactId) ?? null : null;

  async function loadPreview(artifact: ArtifactRecord) {
    if (!artifact.preview_eligible) {
      setPreviewState({ status: "idle", artifactId: artifact.artifact_id });
      return;
    }
    setPreviewState({ status: "loading", artifactId: artifact.artifact_id });
    try {
      const preview = await fetchArtifactPreview(artifact.job_id, artifact.artifact_id, { maxBytes: 4096 });
      setPreviewState({ status: "success", artifactId: artifact.artifact_id, data: preview });
    } catch (error: unknown) {
      setPreviewState({
        status: "error",
        artifactId: artifact.artifact_id,
        message: previewErrorMessage(error),
      });
    }
  }

  if (rows.length === 0) {
    return <EmptyMechanismState warnings={warnings} />;
  }

  return (
    <section className="grid gap-gutter">
      <div className="clinical-panel min-w-0 overflow-hidden">
        <div className="flex flex-col gap-4 border-b border-line bg-surface-muted p-5 md:flex-row md:items-end md:justify-between">
          <div>
            <p className="label-caps text-ink-muted">Mechanistic evidence</p>
            <h2 className="mt-2 font-display text-2xl font-semibold tracking-tight">Evidence table and drug context</h2>
          </div>
          <div className="flex flex-wrap gap-2">
            <span className="rounded border border-line bg-white px-2.5 py-1 font-data text-xs">
              {formatCount(rows.length)} mechanism row{rows.length === 1 ? "" : "s"}
            </span>
            <span className="rounded border border-line bg-white px-2.5 py-1 font-data text-xs">
              {formatCount(evidenceArtifacts.length)} evidence artifact{evidenceArtifacts.length === 1 ? "" : "s"}
            </span>
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full min-w-[980px] border-collapse text-left">
            <thead>
              <tr className="border-b border-line">
                <th className="label-caps px-5 py-3 text-ink-muted">Feature</th>
                <th className="label-caps px-5 py-3 text-ink-muted">Support</th>
                <th className="label-caps px-5 py-3 text-ink-muted">Mechanism</th>
                <th className="label-caps px-5 py-3 text-ink-muted">Drug context</th>
                <th className="label-caps px-5 py-3 text-ink-muted">Raw artifact</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {rows.map((row, index) => {
                const tone = supportTone(row.support_level);
                const artifact = row.raw_artifact_id ? artifactsById.get(row.raw_artifact_id) : null;
                return (
                  <tr className="align-top transition hover:bg-surface-muted" key={`${evidenceLabel(row)}-${index}`}>
                    <td className="px-5 py-4">
                      <p className="font-data text-sm font-bold text-ink">{evidenceLabel(row)}</p>
                      <p className="mt-2 text-xs leading-5 text-ink-muted">{humanizeToken(row.source_tool)}</p>
                    </td>
                    <td className="px-5 py-4">
                      <span
                        className={`inline-flex rounded border px-2 py-1 text-[0.68rem] font-bold uppercase tracking-[0.12em] ${tone.className}`}
                      >
                        {tone.label}
                      </span>
                      <p className="mt-2 max-w-[16rem] text-xs leading-5 text-ink-muted">{tone.description}</p>
                    </td>
                    <td className="px-5 py-4">
                      <p className="max-w-[18rem] text-sm font-semibold leading-5 text-ink">
                        {row.mechanism_class}
                      </p>
                      <p className="mt-2 max-w-[22rem] text-xs leading-5 text-ink-muted">{row.interpretation}</p>
                    </td>
                    <td className="px-5 py-4">
                      <p className="text-sm text-ink-muted">{drugAssociationLabel(row)}</p>
                      {row.raw_row_index !== null && row.raw_row_index !== undefined ? (
                        <p className="mt-2 font-data text-[0.7rem] uppercase tracking-[0.12em] text-ink-muted">
                          Raw row {formatCount(row.raw_row_index)}
                        </p>
                      ) : null}
                    </td>
                    <td className="px-5 py-4">
                      {artifact ? (
                        <button
                          className="group inline-flex max-w-[18rem] items-center gap-2 rounded border border-line bg-white px-3 py-2 text-left font-data text-xs transition hover:border-ink active:scale-[0.98]"
                          onClick={() => void loadPreview(artifact)}
                          type="button"
                        >
                          <FileMagnifyingGlass size={16} weight="duotone" />
                          <span className="truncate">{artifact.artifact_id}</span>
                          <ArrowRight className="transition group-hover:translate-x-0.5" size={14} weight="bold" />
                        </button>
                      ) : (
                        <span className="text-sm text-ink-muted">No raw artifact linked</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {warnings.length > 0 ? (
          <div className="border-t border-line bg-white p-5">
            <div className="flex items-start gap-3">
              <WarningCircle className="mt-0.5 text-review" size={20} weight="duotone" />
              <div>
                <p className="label-caps text-ink-muted">Evidence warnings</p>
                <div className="mt-3 flex flex-wrap gap-2">
                  {warnings.map((warning) => (
                    <span className="rounded border border-line bg-surface-muted px-2.5 py-1 text-xs text-ink-muted" key={warning}>
                      {displayText(warning)}
                    </span>
                  ))}
                </div>
              </div>
            </div>
          </div>
        ) : null}
      </div>

      <div className="grid gap-gutter">
        <div className="clinical-panel overflow-hidden">
          <div className="border-b border-line bg-surface-muted p-5">
            <p className="label-caps text-ink-muted">Artifact manifest</p>
            <h3 className="mt-2 font-display text-xl font-semibold">Raw evidence metadata</h3>
          </div>
          {evidenceArtifacts.length > 0 ? (
            <div className="divide-y divide-line">
              {evidenceArtifacts.map((artifact) => {
                const isSelected = previewState.artifactId === artifact.artifact_id;
                return (
                  <button
                    className={`block w-full p-4 text-left transition hover:bg-white active:scale-[0.99] ${
                      isSelected ? "bg-white" : "bg-surface-panel"
                    }`}
                    key={artifact.artifact_id}
                    onClick={() => void loadPreview(artifact)}
                    type="button"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="truncate font-data text-xs font-bold text-ink">{artifact.artifact_id}</p>
                        <p className="mt-2 text-xs text-ink-muted">{artifactKindLabel(artifact)}</p>
                      </div>
                      <span
                        className={`rounded border px-2 py-1 text-[0.65rem] font-bold uppercase tracking-[0.12em] ${
                          artifact.preview_eligible
                            ? "border-line bg-surface-muted text-ink-muted"
                            : "border-defer bg-slate-100 text-defer"
                        }`}
                      >
                        {artifact.preview_eligible ? "Previewable" : "Metadata only"}
                      </span>
                    </div>
                    <dl className="mt-4 grid gap-2 text-xs">
                      <div>
                        <dt className="label-caps text-ink-muted">Media</dt>
                        <dd className="mt-1 font-data text-ink">{artifact.media_type}</dd>
                      </div>
                      <div>
                        <dt className="label-caps text-ink-muted">Generated by</dt>
                        <dd className="mt-1 font-data text-ink">{artifact.generated_by}</dd>
                      </div>
                      <div>
                        <dt className="label-caps text-ink-muted">Path</dt>
                        <dd className="mt-1 break-all font-data text-ink-muted">{artifact.path}</dd>
                      </div>
                      <div>
                        <dt className="label-caps text-ink-muted">Size</dt>
                        <dd className="mt-1 font-data text-ink-muted">{formatBytes(artifact.size_bytes)}</dd>
                      </div>
                      {artifact.sha256 ? (
                        <div>
                          <dt className="label-caps text-ink-muted">SHA-256</dt>
                          <dd className="mt-1 break-all font-data text-ink-muted">{artifact.sha256}</dd>
                        </div>
                      ) : null}
                    </dl>
                    {citedEvidenceIds.has(artifact.artifact_id) ? (
                      <p className="mt-4 rounded border border-line bg-surface-muted px-2 py-1 font-data text-[0.68rem] uppercase tracking-[0.12em] text-ink-muted">
                        Cited by copilot
                      </p>
                    ) : null}
                  </button>
                );
              })}
            </div>
          ) : (
            <div className="p-5">
              <p className="text-sm leading-6 text-ink-muted">
                No mechanistic evidence artifacts are present in the manifest for this case.
              </p>
            </div>
          )}
        </div>

        <div className="grid gap-gutter">
          <PreviewPanel selectedArtifact={selectedArtifact} state={previewState} />

          <div className="clinical-panel p-5">
            <div className="flex items-start gap-3">
              <Database className="mt-0.5 text-ink" size={22} weight="duotone" />
              <div>
                <p className="label-caps text-ink-muted">Preview boundary</p>
                <p className="mt-2 text-sm leading-6 text-ink-muted">
                  Preview buttons call the backend artifact route with a 4 KB limit. Non-previewable media remains
                  metadata-only in the browser.
                </p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
