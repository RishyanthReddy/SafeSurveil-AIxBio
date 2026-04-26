import type { SeverityLevel, TriageOutcome } from "./types";

export function humanizeToken(value: string | null | undefined): string {
  if (!value) {
    return "Unavailable";
  }
  return value
    .replace(/_/g, " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

export function displayText(value: string | null | undefined): string {
  if (!value) {
    return "Unavailable";
  }
  const trimmed = value.trim();
  if (!trimmed) {
    return "Unavailable";
  }
  const tokenLike = /^[a-z0-9_-]+$/i.test(trimmed);
  return tokenLike ? humanizeToken(trimmed) : trimmed;
}

export function formatPercent(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "Unavailable";
  }
  return `${(value * 100).toFixed(digits)}%`;
}

export function formatCount(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "Unavailable";
  }
  return new Intl.NumberFormat("en-US").format(value);
}

export function triageLabel(value: TriageOutcome): string {
  if (value === "defer_to_lab") {
    return "DEFER";
  }
  return value.toUpperCase();
}

export function triageClass(value: TriageOutcome): string {
  if (value === "act") {
    return "border-act bg-red-50 text-act";
  }
  if (value === "review") {
    return "border-review bg-amber-50 text-review";
  }
  return "border-defer bg-slate-100 text-defer";
}

export function severityClass(value: SeverityLevel): string {
  if (value === "critical") {
    return "text-act";
  }
  if (value === "high") {
    return "text-review";
  }
  return "text-ink-muted";
}
