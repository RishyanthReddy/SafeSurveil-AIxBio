import {
  Checks,
  Fingerprint,
  Hash,
  ShieldCheck,
  ShieldSlash,
  WarningCircle,
  WarningOctagon,
} from "@phosphor-icons/react";

import { formatCount, formatPercent, humanizeToken } from "../api/format";
import type { CaseBundle, ExecutionGateCheckStatus, ExecutionGateDecision, ExecutionGateReport } from "../api/types";

type GateTone = {
  label: string;
  border: string;
  badge: string;
  panel: string;
  meter: string;
  text: string;
};

const gateTones: Record<ExecutionGateDecision, GateTone> = {
  allow: {
    label: "ALLOW",
    border: "border-emerald-500",
    badge: "border-emerald-300 bg-emerald-50 text-emerald-900",
    panel: "from-emerald-50 via-white to-white",
    meter: "bg-emerald-500",
    text: "text-emerald-800",
  },
  review: {
    label: "REVIEW",
    border: "border-amber-500",
    badge: "border-amber-300 bg-amber-50 text-amber-950",
    panel: "from-amber-50 via-white to-white",
    meter: "bg-amber-500",
    text: "text-amber-900",
  },
  block: {
    label: "BLOCK",
    border: "border-red-500",
    badge: "border-red-300 bg-red-50 text-act",
    panel: "from-red-50 via-white to-white",
    meter: "bg-act",
    text: "text-act",
  },
};

function statusTone(status: ExecutionGateCheckStatus): string {
  if (status === "pass") {
    return "border-emerald-300 bg-emerald-50 text-emerald-900";
  }
  if (status === "warn") {
    return "border-amber-300 bg-amber-50 text-amber-950";
  }
  return "border-red-300 bg-red-50 text-act";
}

function shortDigest(value: string): string {
  return value.startsWith("sha256:") ? value.slice(7, 19) : value.slice(0, 12);
}

function ratioWidth(value: number): string {
  const bounded = Math.max(0, Math.min(1, Number.isFinite(value) ? value : 0));
  return `${Math.round(bounded * 100)}%`;
}

function issueCounts(report: ExecutionGateReport): { blocking: number; warnings: number; info: number } {
  return report.issues.reduce(
    (counts, issue) => {
      if (issue.severity === "blocking") {
        counts.blocking += 1;
      } else if (issue.severity === "warning") {
        counts.warnings += 1;
      } else {
        counts.info += 1;
      }
      return counts;
    },
    { blocking: 0, warnings: 0, info: 0 },
  );
}

function checkCounts(report: ExecutionGateReport): { pass: number; warn: number; fail: number } {
  return report.checks.reduce(
    (counts, check) => {
      counts[check.status] += 1;
      return counts;
    },
    { pass: 0, warn: 0, fail: 0 },
  );
}

function GateIcon({ decision }: { decision: ExecutionGateDecision }) {
  if (decision === "allow") {
    return <ShieldCheck size={36} weight="duotone" />;
  }
  if (decision === "block") {
    return <ShieldSlash size={36} weight="duotone" />;
  }
  return <WarningOctagon size={36} weight="duotone" />;
}

function ProofTile({
  label,
  value,
  detail,
  ratio,
  tone,
}: {
  label: string;
  value: string;
  detail: string;
  ratio?: number;
  tone: GateTone;
}) {
  return (
    <article className="rounded-lg border border-line bg-white/85 p-4">
      <p className="label-caps text-ink-muted">{label}</p>
      <p className="mt-3 font-display text-2xl font-bold tracking-tight text-ink">{value}</p>
      {ratio !== undefined ? (
        <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-surface-strong">
          <div className={`h-full rounded-full ${tone.meter}`} style={{ width: ratioWidth(ratio) }} />
        </div>
      ) : null}
      <p className="mt-3 text-xs leading-5 text-ink-muted">{detail}</p>
    </article>
  );
}

function VerifierUnavailable({ error }: { error?: string }) {
  return (
    <section className="clinical-panel border-l-[4px] border-l-review p-5 md:p-6">
      <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
        <div className="flex items-start gap-4">
          <WarningCircle className="mt-1 text-review" size={30} weight="duotone" />
          <div>
            <p className="label-caps text-review">Execution gate unavailable</p>
            <h3 className="mt-2 font-display text-2xl font-semibold tracking-tight text-ink">
              Persisted decision remains visible
            </h3>
            <p className="mt-3 max-w-[70ch] text-sm leading-6 text-ink-muted">
              The case page did not hide decision, evidence, or risk surfaces because the verifier is a read-only V2
              overlay. Re-run the verification endpoint before final sign-off.
            </p>
          </div>
        </div>
        {error ? (
          <code className="max-w-full overflow-x-auto rounded border border-line bg-surface-muted px-3 py-2 font-data text-xs text-ink-muted md:max-w-md">
            {error}
          </code>
        ) : null}
      </div>
    </section>
  );
}

export function ExecutionGatePanel({ bundle }: { bundle: CaseBundle }) {
  const report = bundle.verification;
  if (!report) {
    return <VerifierUnavailable error={bundle.verificationError} />;
  }

  const tone = gateTones[report.gate_decision];
  const checks = checkCounts(report);
  const issues = issueCounts(report);
  const providerCallsTriggered = report.metadata.provider_calls_triggered === true;
  const sidecarDetail = providerCallsTriggered
    ? "Verifier metadata reports provider calls during verification."
    : "Read-only verifier; no OpenRouter or Thesys calls were triggered.";

  return (
    <section className={`clinical-panel overflow-hidden border-l-[4px] ${tone.border}`}>
      <div className={`bg-gradient-to-br ${tone.panel} p-5 md:p-6`}>
        <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_22rem] xl:items-start">
          <div className="flex min-w-0 gap-4">
            <div className={`mt-1 ${tone.text}`}>
              <GateIcon decision={report.gate_decision} />
            </div>
            <div className="min-w-0">
              <p className="label-caps text-ink-muted">SafeSurveil execution gate</p>
              <div className="mt-3 flex flex-wrap items-center gap-3">
                <span
                  className={`rounded border px-3 py-1.5 font-data text-xs font-bold uppercase tracking-[0.18em] ${tone.badge}`}
                >
                  {tone.label}
                </span>
                <span className="rounded border border-line bg-white px-3 py-1.5 font-data text-xs font-bold uppercase tracking-[0.14em] text-ink-muted">
                  {humanizeToken(report.decision)} / {humanizeToken(report.severity)}
                </span>
              </div>
              <h3 className="mt-5 max-w-[20ch] font-display text-4xl font-bold leading-none tracking-[-0.045em] text-ink md:text-5xl">
                Runtime verifier result
              </h3>
              <p className="mt-4 max-w-[78ch] text-sm leading-6 text-ink-muted">{report.summary}</p>
            </div>
          </div>

          <aside className="rounded-lg border border-line bg-white/85 p-4">
            <div className="flex items-center gap-3">
              <Fingerprint className={tone.text} size={24} weight="duotone" />
              <div>
                <p className="label-caps text-ink-muted">Audit fingerprint</p>
                <p className="mt-2 break-all font-data text-xs font-bold text-ink">{shortDigest(report.audit_fingerprint)}</p>
              </div>
            </div>
            <div className="mt-4 grid grid-cols-3 gap-2">
              <div className="rounded border border-line bg-surface-muted p-3">
                <p className="label-caps text-ink-muted">Pass</p>
                <p className="mt-2 font-display text-xl font-bold text-ink">{formatCount(checks.pass)}</p>
              </div>
              <div className="rounded border border-line bg-surface-muted p-3">
                <p className="label-caps text-ink-muted">Warn</p>
                <p className="mt-2 font-display text-xl font-bold text-review">{formatCount(checks.warn)}</p>
              </div>
              <div className="rounded border border-line bg-surface-muted p-3">
                <p className="label-caps text-ink-muted">Fail</p>
                <p className="mt-2 font-display text-xl font-bold text-act">{formatCount(checks.fail)}</p>
              </div>
            </div>
          </aside>
        </div>

        <div className="mt-6 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <ProofTile
            detail={`${formatCount(report.evidence_coverage.covered_evidence_ids.length)} of ${formatCount(
              report.evidence_coverage.required_evidence_ids.length,
            )} required evidence IDs covered.`}
            label="Evidence coverage"
            ratio={report.evidence_coverage.coverage_ratio}
            tone={tone}
            value={formatPercent(report.evidence_coverage.coverage_ratio, 0)}
          />
          <ProofTile
            detail={`${formatCount(report.numeric_consistency.matched_fields.length)} of ${formatCount(
              report.numeric_consistency.checked_fields.length,
            )} numeric fields match the persisted decision.`}
            label="Numeric consistency"
            ratio={report.numeric_consistency.consistency_ratio}
            tone={tone}
            value={formatPercent(report.numeric_consistency.consistency_ratio, 0)}
          />
          <ProofTile
            detail={`${formatCount(report.citation_validity.invalid_evidence_ids.length)} invalid cited evidence IDs detected.`}
            label="Citation validity"
            ratio={report.citation_validity.validity_ratio}
            tone={tone}
            value={formatPercent(report.citation_validity.validity_ratio, 0)}
          />
          <ProofTile
            detail={sidecarDetail}
            label="Policy hash"
            tone={tone}
            value={shortDigest(report.policy_hash)}
          />
        </div>
      </div>

      <div className="grid gap-0 divide-y divide-line xl:grid-cols-[1fr_0.85fr] xl:divide-x xl:divide-y-0">
        <div className="p-5 md:p-6">
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <Checks size={22} weight="duotone" className="text-ink" />
              <div>
                <p className="label-caps text-ink-muted">Verifier checks</p>
                <h4 className="mt-1 font-display text-xl font-semibold tracking-tight text-ink">
                  {formatCount(report.checks.length)} runtime checks
                </h4>
              </div>
            </div>
            <span className={`rounded border px-2.5 py-1 font-data text-[0.68rem] font-bold uppercase tracking-[0.14em] ${tone.badge}`}>
              {formatCount(issues.blocking)} blocking / {formatCount(issues.warnings)} warning
            </span>
          </div>
          <div className="mt-5 grid gap-3 md:grid-cols-2">
            {report.checks.slice(0, 6).map((check) => (
              <article className="rounded-lg border border-line bg-surface-panel p-4" key={check.check_id}>
                <div className="flex flex-wrap items-center gap-2">
                  <span
                    className={`rounded border px-2 py-1 font-data text-[0.65rem] font-bold uppercase tracking-[0.14em] ${statusTone(
                      check.status,
                    )}`}
                  >
                    {humanizeToken(check.status)}
                  </span>
                  <span className="label-caps text-ink-muted">{humanizeToken(check.category)}</span>
                </div>
                <h5 className="mt-3 font-display text-base font-semibold tracking-tight text-ink">{check.title}</h5>
                <p className="mt-2 text-xs leading-5 text-ink-muted">{check.detail}</p>
              </article>
            ))}
          </div>
        </div>

        <aside className="bg-surface-muted/60 p-5 md:p-6">
          <div className="flex items-start gap-3">
            <Hash size={22} weight="duotone" className="text-ink" />
            <div>
              <p className="label-caps text-ink-muted">Verifier ledger</p>
              <p className="mt-2 text-sm leading-6 text-ink-muted">
                Policy alignment, cited evidence, numeric values, and artifact coverage are checked before this gate
                labels the case as allow, review, or block.
              </p>
            </div>
          </div>
          <div className="mt-5 space-y-3">
            <div className="rounded border border-line bg-white p-3">
              <p className="label-caps text-ink-muted">Policy version</p>
              <p className="mt-2 font-data text-xs font-bold text-ink">{report.policy_alignment.policy_version}</p>
            </div>
            <div className="rounded border border-line bg-white p-3">
              <p className="label-caps text-ink-muted">Fingerprint</p>
              <p className="mt-2 break-all font-data text-xs font-bold text-ink">{report.audit_fingerprint}</p>
            </div>
            {report.issues.length > 0 ? (
              <div className="rounded border border-line bg-white p-3">
                <p className="label-caps text-ink-muted">Top issues</p>
                <div className="mt-3 grid gap-2">
                  {report.issues.slice(0, 3).map((issue) => (
                    <p className="text-xs leading-5 text-ink-muted" key={issue.issue_id}>
                      <span className="font-semibold text-ink">{humanizeToken(issue.severity)}:</span> {issue.title}
                    </p>
                  ))}
                </div>
              </div>
            ) : (
              <div className="rounded border border-emerald-200 bg-emerald-50 p-3">
                <p className="label-caps text-emerald-900">No open gate issues</p>
                <p className="mt-2 text-xs leading-5 text-emerald-900">
                  The verifier found no blocking or warning issues for this report.
                </p>
              </div>
            )}
          </div>
        </aside>
      </div>
    </section>
  );
}
