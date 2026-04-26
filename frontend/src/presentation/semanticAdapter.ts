import type {
  EvidenceTableRow,
  QueueItem,
  RiskChartBlock,
  RiskChartPoint,
  SemanticUIObject,
  SeverityLevel,
  TriageOutcome,
} from "../api/types";

export type PresentationIssue = {
  path: string;
  message: string;
};

export type PresentationMetric = {
  key: string;
  label: string;
  value: string | number | boolean;
  unit?: string | null;
};

export type PresentationDecisionCard =
  | {
      status: "ready";
      title: string;
      triage: TriageOutcome;
      severity: SeverityLevel;
      summary: string;
      metrics: PresentationMetric[];
    }
  | {
      status: "missing";
      title: string;
      summary: string;
      metrics: PresentationMetric[];
    };

export type PresentationEvidenceTable =
  | {
      status: "ready";
      title: string;
      columns: string[];
      rows: EvidenceTableRow[];
    }
  | {
      status: "missing";
      title: string;
      columns: string[];
      rows: EvidenceTableRow[];
    };

export type PresentationRiskChart =
  | {
      status: "ready";
      chartId: string;
      title: string;
      chartType: RiskChartBlock["chart_type"];
      points: RiskChartPoint[];
    }
  | {
      status: "missing";
      chartId: string;
      title: string;
      chartType: "bar";
      points: RiskChartPoint[];
    };

export type PresentationQueueBlock = {
  status: "ready" | "missing";
  title: string;
  items: QueueItem[];
};

export type SemanticPresentationModel = {
  decisionCard: PresentationDecisionCard;
  evidenceTable: PresentationEvidenceTable;
  riskCharts: PresentationRiskChart[];
  safetyProfile: SemanticUIObject["safety_profile"] | null;
  queueBlock: PresentationQueueBlock;
  notes: string[];
  issues: PresentationIssue[];
};

const TRIAGE_VALUES = new Set<TriageOutcome>(["act", "review", "defer_to_lab"]);
const SEVERITY_VALUES = new Set<SeverityLevel>(["low", "medium", "high", "critical"]);
const CHART_TYPES = new Set<RiskChartBlock["chart_type"]>(["bar", "line", "area", "radial"]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringOrFallback(value: unknown, fallback: string): string {
  return typeof value === "string" && value.trim() ? value.trim() : fallback;
}

function isTriage(value: unknown): value is TriageOutcome {
  return typeof value === "string" && TRIAGE_VALUES.has(value as TriageOutcome);
}

function isSeverity(value: unknown): value is SeverityLevel {
  return typeof value === "string" && SEVERITY_VALUES.has(value as SeverityLevel);
}

function normalizeMetric(value: unknown, index: number, issues: PresentationIssue[]): PresentationMetric | null {
  if (!isRecord(value)) {
    issues.push({
      path: `semantic_ui.decision_card.metrics[${index}]`,
      message: "Metric was not an object and was omitted.",
    });
    return null;
  }
  const metricValue = value.value;
  if (
    typeof metricValue !== "string" &&
    typeof metricValue !== "number" &&
    typeof metricValue !== "boolean"
  ) {
    issues.push({
      path: `semantic_ui.decision_card.metrics[${index}].value`,
      message: "Metric value was unavailable and was omitted.",
    });
    return null;
  }
  const key = stringOrFallback(value.key, `metric_${index + 1}`);
  return {
    key,
    label: stringOrFallback(value.label, key),
    value: metricValue,
    unit: typeof value.unit === "string" ? value.unit : null,
  };
}

function normalizeDecisionCard(
  semanticUi: unknown,
  issues: PresentationIssue[],
): PresentationDecisionCard {
  if (!isRecord(semanticUi) || !isRecord(semanticUi.decision_card)) {
    return {
      status: "missing",
      title: "Decision card unavailable",
      summary: "No decision card block was supplied by the backend semantic UI response.",
      metrics: [],
    };
  }

  const card = semanticUi.decision_card;
  const metrics = Array.isArray(card.metrics)
    ? card.metrics
        .map((metric, index) => normalizeMetric(metric, index, issues))
        .filter((metric): metric is PresentationMetric => metric !== null)
    : [];
  if (!Array.isArray(card.metrics)) {
    issues.push({
      path: "semantic_ui.decision_card.metrics",
      message: "Decision metrics were missing or malformed.",
    });
  }
  if (!isTriage(card.triage_decision) || !isSeverity(card.severity)) {
    issues.push({
      path: "semantic_ui.decision_card",
      message: "Decision card triage or severity was malformed.",
    });
    return {
      status: "missing",
      title: stringOrFallback(card.title, "Decision card unavailable"),
      summary: stringOrFallback(
        card.summary,
        "The decision card did not include renderable triage and severity fields.",
      ),
      metrics,
    };
  }
  return {
    status: "ready",
    title: stringOrFallback(card.title, "Decision card"),
    triage: card.triage_decision,
    severity: card.severity,
    summary: stringOrFallback(card.summary, "No decision summary was supplied."),
    metrics,
  };
}

function normalizeTableRow(
  row: unknown,
  rowIndex: number,
  issues: PresentationIssue[],
): EvidenceTableRow | null {
  if (!isRecord(row)) {
    issues.push({
      path: `semantic_ui.evidence_table.rows[${rowIndex}]`,
      message: "Evidence row was not an object and was omitted.",
    });
    return null;
  }
  const cells = isRecord(row.cells) ? row.cells : null;
  if (cells === null) {
    issues.push({
      path: `semantic_ui.evidence_table.rows[${rowIndex}].cells`,
      message: "Evidence row cells were missing and the row was omitted.",
    });
    return null;
  }
  const normalizedCells: EvidenceTableRow["cells"] = {};
  for (const [key, cellValue] of Object.entries(cells)) {
    if (
      cellValue === null ||
      typeof cellValue === "string" ||
      typeof cellValue === "number" ||
      typeof cellValue === "boolean"
    ) {
      normalizedCells[key] = cellValue;
    }
  }
  return {
    row_id: stringOrFallback(row.row_id, `evidence_row_${rowIndex + 1}`),
    label: stringOrFallback(row.label, `Evidence row ${rowIndex + 1}`),
    cells: normalizedCells,
    evidence_id: typeof row.evidence_id === "string" ? row.evidence_id : null,
  };
}

function normalizeEvidenceTable(
  semanticUi: unknown,
  issues: PresentationIssue[],
): PresentationEvidenceTable {
  if (!isRecord(semanticUi) || !isRecord(semanticUi.evidence_table)) {
    return {
      status: "missing",
      title: "Mechanistic evidence unavailable",
      columns: [],
      rows: [],
    };
  }
  const table = semanticUi.evidence_table;
  const rows = Array.isArray(table.rows)
    ? table.rows
        .map((row, index) => normalizeTableRow(row, index, issues))
        .filter((row): row is EvidenceTableRow => row !== null)
    : [];
  if (!Array.isArray(table.rows)) {
    issues.push({
      path: "semantic_ui.evidence_table.rows",
      message: "Evidence table rows were missing or malformed.",
    });
  }
  const declaredColumns = Array.isArray(table.columns)
    ? table.columns.filter((column): column is string => typeof column === "string" && column.trim().length > 0)
    : [];
  const derivedColumns = Array.from(new Set(rows.flatMap((row) => Object.keys(row.cells))));
  const columns = declaredColumns.length > 0 ? declaredColumns : derivedColumns;
  if (rows.length === 0) {
    return {
      status: "missing",
      title: stringOrFallback(table.title, "Mechanistic evidence unavailable"),
      columns,
      rows,
    };
  }
  return {
    status: "ready",
    title: stringOrFallback(table.title, "Mechanistic evidence"),
    columns,
    rows,
  };
}

function normalizeRiskPoint(
  point: unknown,
  chartIndex: number,
  pointIndex: number,
  issues: PresentationIssue[],
): RiskChartPoint | null {
  if (!isRecord(point) || typeof point.value !== "number" || Number.isNaN(point.value)) {
    issues.push({
      path: `semantic_ui.risk_charts[${chartIndex}].points[${pointIndex}]`,
      message: "Risk point was malformed and was omitted.",
    });
    return null;
  }
  return {
    label: stringOrFallback(point.label, `Point ${pointIndex + 1}`),
    value: point.value,
    evidence_id: typeof point.evidence_id === "string" ? point.evidence_id : null,
  };
}

function normalizeRiskCharts(
  semanticUi: unknown,
  issues: PresentationIssue[],
): PresentationRiskChart[] {
  if (!isRecord(semanticUi) || !Array.isArray(semanticUi.risk_charts) || semanticUi.risk_charts.length === 0) {
    return [
      {
        status: "missing",
        chartId: "risk_unavailable",
        title: "Novelty and risk chart unavailable",
        chartType: "bar",
        points: [],
      },
    ];
  }

  const charts = semanticUi.risk_charts
    .map((chart, chartIndex): PresentationRiskChart | null => {
      if (!isRecord(chart)) {
        issues.push({
          path: `semantic_ui.risk_charts[${chartIndex}]`,
          message: "Risk chart was not an object and was omitted.",
        });
        return null;
      }
      const chartType = CHART_TYPES.has(chart.chart_type as RiskChartBlock["chart_type"])
        ? (chart.chart_type as RiskChartBlock["chart_type"])
        : "bar";
      const points = Array.isArray(chart.points)
        ? chart.points
            .map((point, pointIndex) => normalizeRiskPoint(point, chartIndex, pointIndex, issues))
            .filter((point): point is RiskChartPoint => point !== null)
        : [];
      if (points.length === 0) {
        return {
          status: "missing",
          chartId: stringOrFallback(chart.chart_id, `risk_chart_${chartIndex + 1}`),
          title: stringOrFallback(chart.title, "Risk chart unavailable"),
          chartType: "bar",
          points,
        };
      }
      return {
        status: "ready",
        chartId: stringOrFallback(chart.chart_id, `risk_chart_${chartIndex + 1}`),
        title: stringOrFallback(chart.title, "Risk chart"),
        chartType,
        points,
      };
    })
    .filter((chart): chart is PresentationRiskChart => chart !== null);

  return charts.length > 0
    ? charts
    : [
        {
          status: "missing",
          chartId: "risk_unavailable",
          title: "Novelty and risk chart unavailable",
          chartType: "bar",
          points: [],
        },
      ];
}

function normalizeQueueBlock(semanticUi: unknown): PresentationQueueBlock {
  if (!isRecord(semanticUi) || !isRecord(semanticUi.queue_block)) {
    return {
      status: "missing",
      title: "Queue context unavailable",
      items: [],
    };
  }
  const queueBlock = semanticUi.queue_block;
  const items = Array.isArray(queueBlock.items)
    ? queueBlock.items.filter((item): item is QueueItem => isRecord(item) && typeof item.job_id === "string")
    : [];
  return {
    status: items.length > 0 ? "ready" : "missing",
    title: stringOrFallback(queueBlock.title, "Analyst queue"),
    items,
  };
}

function normalizeNotes(semanticUi: unknown): string[] {
  if (!isRecord(semanticUi) || !Array.isArray(semanticUi.notes)) {
    return [];
  }
  return semanticUi.notes.filter((note): note is string => typeof note === "string" && note.trim().length > 0);
}

export function adaptSemanticUi(semanticUi: SemanticUIObject | Partial<SemanticUIObject> | null | undefined): SemanticPresentationModel {
  const issues: PresentationIssue[] = [];
  return {
    decisionCard: normalizeDecisionCard(semanticUi, issues),
    evidenceTable: normalizeEvidenceTable(semanticUi, issues),
    riskCharts: normalizeRiskCharts(semanticUi, issues),
    safetyProfile: isRecord(semanticUi) && isRecord(semanticUi.safety_profile) ? (semanticUi.safety_profile as SemanticUIObject["safety_profile"]) : null,
    queueBlock: normalizeQueueBlock(semanticUi),
    notes: normalizeNotes(semanticUi),
    issues,
  };
}
