import { ChartBar, ChartPolar, Gauge, GitBranch, ShieldCheck, WarningCircle } from "@phosphor-icons/react";

import { displayText, formatPercent, humanizeToken } from "../api/format";
import type { CaseBundle } from "../api/types";

type RiskVisualizationBlocksProps = {
  bundle: CaseBundle;
};

type NumericSignal = {
  key: string;
  label: string;
  value: number | null | undefined;
  detail: string;
  tone: "act" | "review" | "defer";
};

type SafetyAxis = {
  label: string;
  value: number | null | undefined;
  source: string;
};

function boundedRatio(value: number | null | undefined): number {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return 0;
  }
  return Math.max(0, Math.min(1, value));
}

function ratioWidth(value: number | null | undefined): string {
  return `${Math.round(boundedRatio(value) * 100)}%`;
}

function ratioDegrees(value: number | null | undefined): number {
  return Math.round(boundedRatio(value) * 360);
}

function metricValue(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "Unavailable";
  }
  return formatPercent(value, digits);
}

function numberValue(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "Unavailable";
  }
  return Number.isInteger(value) ? String(value) : value.toFixed(4);
}

function riskToneClass(tone: NumericSignal["tone"]): string {
  if (tone === "act") {
    return "bg-act";
  }
  if (tone === "review") {
    return "bg-review";
  }
  return "bg-defer";
}

function riskTextClass(tone: NumericSignal["tone"]): string {
  if (tone === "act") {
    return "text-act";
  }
  if (tone === "review") {
    return "text-review";
  }
  return "text-defer";
}

function noveltyBucketTone(bucket: string): string {
  const normalized = bucket.toLowerCase();
  if (normalized.includes("high") || normalized.includes("novel")) {
    return "border-review bg-amber-50 text-review";
  }
  if (normalized.includes("known")) {
    return "border-act bg-red-50 text-act";
  }
  return "border-defer bg-slate-100 text-defer";
}

function noveltyMarkerLeft(value: number | null | undefined): string {
  return `calc(${ratioWidth(value)} - 2px)`;
}

function ReliabilityBars({
  missingReference,
  uncertaintyFlag,
}: {
  missingReference: boolean;
  uncertaintyFlag: boolean;
}) {
  const activeBars = missingReference ? 2 : uncertaintyFlag ? 3 : 4;
  const label = missingReference ? "Limited" : uncertaintyFlag ? "Review" : "High";
  return (
    <div className="flex items-center gap-2">
      <span className="label-caps text-ink-muted">Reliability</span>
      <div className="flex gap-0.5">
        {Array.from({ length: 5 }).map((_, index) => (
          <span
            className={`h-3 w-1.5 rounded-sm ${index < activeBars ? "bg-ink" : "bg-surface-strong"}`}
            key={index}
          />
        ))}
      </div>
      <span className="font-data text-[0.68rem] font-bold uppercase tracking-[0.12em] text-ink-muted">{label}</span>
    </div>
  );
}

function RiskSignalRow({ signal }: { signal: NumericSignal }) {
  const unavailable = signal.value === null || signal.value === undefined || Number.isNaN(signal.value);
  return (
    <div className="border-t border-line py-4 first:border-t-0">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="font-data text-sm font-semibold text-ink">{signal.label}</p>
          <p className="mt-1 text-xs leading-5 text-ink-muted">{signal.detail}</p>
        </div>
        <p className={`font-display text-xl font-bold tracking-tight ${riskTextClass(signal.tone)}`}>
          {metricValue(signal.value, 0)}
        </p>
      </div>
      <div className="mt-3 h-2 overflow-hidden rounded-full bg-surface-strong">
        <div
          className={`h-full rounded-full transition-[width] duration-300 ${unavailable ? "bg-surface-strong" : riskToneClass(signal.tone)}`}
          style={{ width: unavailable ? "0%" : ratioWidth(signal.value) }}
        />
      </div>
    </div>
  );
}

function NoveltyProfile({
  noveltyScore,
  noveltyPercentile,
  noveltyBucket,
  nearestNeighborId,
  nearestNeighborDistance,
  referenceSnapshotId,
  missingReference,
  uncertaintyFlag,
}: {
  noveltyScore: number | null | undefined;
  noveltyPercentile: number | null | undefined;
  noveltyBucket: string;
  nearestNeighborId: string | null | undefined;
  nearestNeighborDistance: number | null | undefined;
  referenceSnapshotId: string;
  missingReference: boolean;
  uncertaintyFlag: boolean;
}) {
  return (
    <article className="clinical-panel overflow-hidden">
      <div className="flex flex-col gap-3 border-b border-line bg-surface-muted p-5 md:flex-row md:items-center md:justify-between">
        <div className="flex items-center gap-3">
          <GitBranch size={24} weight="duotone" />
          <div>
            <p className="label-caps text-ink-muted">Lineage novelty</p>
            <h2 className="mt-1 font-display text-2xl font-semibold tracking-tight">Shift profile</h2>
          </div>
        </div>
        <ReliabilityBars missingReference={missingReference} uncertaintyFlag={uncertaintyFlag} />
      </div>

      <div className="p-5 md:p-6">
        <div className="grid gap-6">
          <div className="rounded-lg border border-line bg-white p-5">
            <p className="label-caps text-ink-muted">Novelty score</p>
            <p className="mt-3 font-display text-5xl font-bold leading-none tracking-[-0.05em] text-ink">
              {metricValue(noveltyScore, 0)}
            </p>
            <span className={`mt-4 inline-flex rounded border px-2 py-1 text-[0.68rem] font-bold uppercase tracking-[0.12em] ${noveltyBucketTone(noveltyBucket)}`}>
              {humanizeToken(noveltyBucket)}
            </span>
            <p className="mt-4 text-sm leading-6 text-ink-muted">
              Percentile: {noveltyPercentile === null || noveltyPercentile === undefined ? "Unavailable" : `${noveltyPercentile.toFixed(1)}%`}
            </p>
          </div>

          <div className="flex flex-col justify-center">
            <div className="flex items-center justify-between gap-4">
              <span className="font-data text-xs font-semibold uppercase tracking-[0.12em] text-ink">
                Reference distance
              </span>
              <span className="rounded border border-line bg-white px-2 py-1 font-data text-xs text-ink-muted">
                {numberValue(nearestNeighborDistance)}
              </span>
            </div>
            <div className="relative mt-4 h-5 overflow-visible rounded-full border border-line bg-white">
              <div className="absolute inset-y-0 left-0 w-1/3 rounded-l-full bg-slate-200" />
              <div className="absolute inset-y-0 left-1/3 w-1/3 bg-slate-300" />
              <div className="absolute inset-y-0 right-0 w-1/3 rounded-r-full bg-slate-500" />
              <div
                className="absolute -top-1 bottom-[-4px] w-1 rounded bg-ink ring-2 ring-white"
                style={{ left: noveltyMarkerLeft(noveltyScore) }}
              />
            </div>
            <div className="mt-3 grid grid-cols-3 text-[0.62rem] font-bold uppercase tracking-[0.14em] text-ink-muted">
              <span>Known</span>
              <span className="text-center">Elevated</span>
              <span className="text-right">High shift</span>
            </div>
            <dl className="mt-6 grid gap-3 text-sm sm:grid-cols-2">
              <div className="rounded border border-line bg-surface-muted p-3">
                <dt className="label-caps text-ink-muted">Nearest neighbor</dt>
                <dd className="mt-2 break-all font-data text-xs text-ink">{nearestNeighborId ?? "Unavailable"}</dd>
              </div>
              <div className="rounded border border-line bg-surface-muted p-3">
                <dt className="label-caps text-ink-muted">Reference snapshot</dt>
                <dd className="mt-2 break-all font-data text-xs text-ink">{referenceSnapshotId}</dd>
              </div>
            </dl>
          </div>
        </div>
      </div>
    </article>
  );
}

function SafetyProfile({ axes }: { axes: SafetyAxis[] }) {
  return (
    <article className="clinical-panel p-5">
      <div className="flex items-center justify-between gap-3 border-b border-line pb-4">
        <div className="flex items-center gap-3">
          <ChartPolar size={24} weight="duotone" />
          <div>
            <p className="label-caps text-ink-muted">Safety profile</p>
            <h3 className="mt-1 font-display text-xl font-semibold tracking-tight">Decision axes</h3>
          </div>
        </div>
        <span className="font-data text-xs uppercase tracking-[0.12em] text-ink-muted">Fallback chart</span>
      </div>

      <div className="mt-5 grid gap-5">
        {axes.map((axis) => (
          <div className="grid gap-4 sm:grid-cols-[5.5rem_minmax(0,1fr)] sm:items-center" key={axis.label}>
            <div
              className="grid h-20 w-20 place-items-center rounded-full"
              style={{
                background: `conic-gradient(var(--color-ink) ${ratioDegrees(axis.value)}deg, var(--color-surface-strong) 0deg)`,
              }}
            >
              <div className="grid h-14 w-14 place-items-center rounded-full bg-white">
                <span className="font-display text-sm font-bold">{metricValue(axis.value, 0)}</span>
              </div>
            </div>
            <div>
              <div className="flex items-baseline justify-between gap-3">
                <p className="font-data text-sm font-semibold text-ink">{axis.label}</p>
                <p className="font-data text-xs text-ink-muted">{axis.source}</p>
              </div>
              <div className="mt-3 h-2 overflow-hidden rounded-full bg-surface-strong">
                <div className="h-full rounded-full bg-ink" style={{ width: ratioWidth(axis.value) }} />
              </div>
            </div>
          </div>
        ))}
      </div>
    </article>
  );
}

export function RiskVisualizationBlocks({ bundle }: RiskVisualizationBlocksProps) {
  const decision = bundle.decision.decision;
  const novelty = decision.novelty_assessment;
  const actionability = decision.actionability_features;
  const phenotype = decision.phenotype_prediction;
  const semanticChartCount = bundle.semanticUi.semantic_ui.risk_charts.length;
  const semanticSafetyAxisCount = bundle.semanticUi.semantic_ui.safety_profile?.axes.length ?? 0;
  const riskSignals: NumericSignal[] = [
    {
      key: "probability",
      label: "Resistance probability",
      value: phenotype.probability,
      detail: `Phenotype: ${humanizeToken(phenotype.predicted_phenotype)}`,
      tone: "act",
    },
    {
      key: "actionability_score",
      label: "Actionability score",
      value: actionability.actionability_score,
      detail: `Threshold set: ${actionability.threshold_version}`,
      tone: "act",
    },
    {
      key: "novelty_risk",
      label: "Novelty risk",
      value: actionability.novelty_risk,
      detail: `Novelty bucket: ${humanizeToken(novelty.novelty_bucket)}`,
      tone: "review",
    },
    {
      key: "qc_risk",
      label: "QC risk",
      value: actionability.qc_risk,
      detail: `Assembly QC: ${humanizeToken(decision.assembly_qc.qc_status)}`,
      tone: "defer",
    },
  ];

  if (actionability.prediction_entropy !== null && actionability.prediction_entropy !== undefined) {
    riskSignals.push({
      key: "prediction_entropy",
      label: "Prediction uncertainty",
      value: actionability.prediction_entropy,
      detail: "Actionability feature: prediction_entropy",
      tone: "review",
    });
  }

  const safetyAxes: SafetyAxis[] = [
    {
      label: "Evidence support",
      value: actionability.actionability_score,
      source: "actionability_score",
    },
    {
      label: "QC confidence",
      value: 1 - boundedRatio(actionability.qc_risk),
      source: "1 - qc_risk",
    },
    {
      label: "Novelty burden",
      value: actionability.novelty_risk,
      source: "novelty_risk",
    },
    {
      label: "Metadata completeness",
      value: actionability.metadata_completeness,
      source: "metadata_completeness",
    },
  ];

  return (
    <section className="grid gap-gutter">
      <NoveltyProfile
        missingReference={novelty.missing_reference}
        nearestNeighborDistance={novelty.nearest_neighbor_distance}
        nearestNeighborId={novelty.nearest_neighbor_id}
        noveltyBucket={novelty.novelty_bucket}
        noveltyPercentile={novelty.novelty_percentile}
        noveltyScore={novelty.novelty_score}
        referenceSnapshotId={novelty.reference_snapshot_id}
        uncertaintyFlag={novelty.uncertainty_flag}
      />

      <div className="grid gap-gutter">
        <article className="clinical-panel overflow-hidden">
          <div className="flex flex-col gap-3 border-b border-line bg-surface-muted p-5 md:flex-row md:items-center md:justify-between">
            <div className="flex items-center gap-3">
              <ChartBar size={24} weight="duotone" />
              <div>
                <p className="label-caps text-ink-muted">Risk decomposition</p>
                <h2 className="mt-1 font-display text-2xl font-semibold tracking-tight">Decision signal balance</h2>
              </div>
            </div>
            <span className="rounded border border-line bg-white px-2.5 py-1 font-data text-xs text-ink-muted">
              {riskSignals.length} persisted signals
            </span>
          </div>
          <div className="p-5 md:p-6">
            {riskSignals.map((signal) => (
              <RiskSignalRow key={signal.key} signal={signal} />
            ))}
          </div>
        </article>

        <SafetyProfile axes={safetyAxes} />

        <article className="clinical-panel p-5">
          <div className="flex items-start gap-3">
            <Gauge className="mt-0.5 text-ink" size={24} weight="duotone" />
            <div>
              <p className="label-caps text-ink-muted">Semantic UI cross-check</p>
              <p className="mt-2 text-sm leading-6 text-ink-muted">
                Backend semantic UI supplied {semanticChartCount} risk chart
                {semanticChartCount === 1 ? "" : "s"} and{" "}
                {semanticSafetyAxisCount === 1
                  ? `${semanticSafetyAxisCount} safety axis`
                  : `${semanticSafetyAxisCount} safety axes`}
                . This React fallback redraws values from the persisted decision object so labels and numbers remain
                traceable.
              </p>
            </div>
          </div>
        </article>

        {(novelty.missing_reference || novelty.uncertainty_flag || novelty.warnings.length > 0) ? (
          <article className="clinical-panel border-l-[4px] border-l-review p-5">
            <div className="flex items-start gap-3">
              <WarningCircle className="mt-0.5 text-review" size={24} weight="duotone" />
              <div>
                <p className="label-caps text-review">Novelty caveats</p>
                <div className="mt-3 grid gap-2">
                  {novelty.missing_reference ? (
                    <p className="rounded border border-line bg-surface-muted px-3 py-2 text-sm text-ink-muted">
                      Reference context is missing for this novelty assessment.
                    </p>
                  ) : null}
                  {novelty.uncertainty_flag ? (
                    <p className="rounded border border-line bg-surface-muted px-3 py-2 text-sm text-ink-muted">
                      The novelty layer flagged uncertainty for analyst review.
                    </p>
                  ) : null}
                  {novelty.warnings.map((warning) => (
                    <p className="rounded border border-line bg-surface-muted px-3 py-2 text-sm text-ink-muted" key={warning}>
                      {displayText(warning)}
                    </p>
                  ))}
                </div>
              </div>
            </div>
          </article>
        ) : (
          <article className="clinical-panel p-5">
            <div className="flex items-start gap-3">
              <ShieldCheck className="mt-0.5 text-ink" size={24} weight="duotone" />
              <div>
                <p className="label-caps text-ink-muted">Novelty caveats</p>
                <p className="mt-2 text-sm leading-6 text-ink-muted">
                  No missing-reference or uncertainty flags were reported by the novelty layer.
                </p>
              </div>
            </div>
          </article>
        )}
      </div>
    </section>
  );
}
