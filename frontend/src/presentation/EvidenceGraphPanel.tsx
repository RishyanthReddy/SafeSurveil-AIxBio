import { ArrowsOutSimple, GitBranch, LinkSimple, ListChecks, WarningCircle } from "@phosphor-icons/react";
import { useEffect, useRef, useState } from "react";
import cytoscape from "cytoscape";
import fcose from "cytoscape-fcose";

import { displayText, formatCount, formatPercent, humanizeToken } from "../api/format";
import type {
  EvidenceGraph,
  EvidenceGraphEdge,
  EvidenceGraphNode,
  EvidenceGraphNodeClass,
  EvidenceGraphStyleTone,
} from "../api/types";

let fcoseRegistered = false;

function ensureFcoseLayout() {
  if (fcoseRegistered) {
    return;
  }
  cytoscape.use(fcose);
  fcoseRegistered = true;
}

type TonePalette = {
  fill: string;
  border: string;
  text: string;
  badge: string;
};

const tonePalette: Record<EvidenceGraphStyleTone, TonePalette> = {
  neutral: {
    fill: "#e2e8f0",
    border: "#94a3b8",
    text: "text-ink-muted",
    badge: "border-line bg-surface-muted text-ink-muted",
  },
  sample: {
    fill: "#dbeafe",
    border: "#2563eb",
    text: "text-blue-900",
    badge: "border-blue-200 bg-blue-50 text-blue-950",
  },
  evidence: {
    fill: "#dcfce7",
    border: "#16a34a",
    text: "text-emerald-900",
    badge: "border-emerald-200 bg-emerald-50 text-emerald-950",
  },
  risk: {
    fill: "#ffedd5",
    border: "#ea580c",
    text: "text-review",
    badge: "border-orange-200 bg-orange-50 text-orange-950",
  },
  policy: {
    fill: "#e0f2fe",
    border: "#0284c7",
    text: "text-sky-950",
    badge: "border-sky-200 bg-sky-50 text-sky-950",
  },
  ai: {
    fill: "#f1f5f9",
    border: "#475569",
    text: "text-defer",
    badge: "border-slate-300 bg-slate-100 text-defer",
  },
  gate: {
    fill: "#ccfbf1",
    border: "#0f766e",
    text: "text-teal-900",
    badge: "border-teal-200 bg-teal-50 text-teal-950",
  },
  decision: {
    fill: "#fee2e2",
    border: "#b91c1c",
    text: "text-act",
    badge: "border-red-200 bg-red-50 text-act",
  },
  caveat: {
    fill: "#fef3c7",
    border: "#d97706",
    text: "text-review",
    badge: "border-amber-200 bg-amber-50 text-amber-950",
  },
};

const importantNodeClasses = new Set<EvidenceGraphNodeClass>([
  "sample",
  "drug",
  "gene",
  "mechanism",
  "decision",
  "execution_gate",
]);

const cytoscapeStyle = [
  {
    selector: "core",
    style: {
      "selection-box-color": "#0f172a",
      "selection-box-opacity": 0.08,
      "active-bg-opacity": 0.04,
      "active-bg-color": "#0f172a",
    },
  },
  {
    selector: "node",
    style: {
      width: "data(size)",
      height: "data(size)",
      "background-color": "data(fill)",
      "border-color": "data(border)",
      "border-width": 2,
      color: "#0f172a",
      content: "data(label)",
      "font-family": "Space Grotesk, sans-serif",
      "font-size": 8,
      "font-weight": 700,
      "min-zoomed-font-size": 6,
      "overlay-opacity": 0,
      "text-background-color": "#ffffff",
      "text-background-opacity": 0.76,
      "text-background-padding": 2,
      "text-halign": "center",
      "text-margin-y": 9,
      "text-max-width": 84,
      "text-wrap": "wrap",
      "text-valign": "bottom",
    },
  },
  {
    selector: "node.muted-label",
    style: {
      content: "",
    },
  },
  {
    selector: "node:selected",
    style: {
      "border-color": "#0f172a",
      "border-width": 4,
      "shadow-blur": 18,
      "shadow-color": "#0f172a",
      "shadow-opacity": 0.18,
    },
  },
  {
    selector: "edge",
    style: {
      width: "data(width)",
      "curve-style": "bezier",
      "line-color": "#94a3b8",
      "opacity": 0.76,
      "target-arrow-color": "#94a3b8",
      "target-arrow-shape": "triangle",
    },
  },
  {
    selector: "edge:selected",
    style: {
      "line-color": "#0f172a",
      "target-arrow-color": "#0f172a",
      width: 3.2,
    },
  },
  {
    selector: ".decision",
    style: {
      "border-width": 4,
      shape: "round-rectangle",
    },
  },
  {
    selector: ".warning",
    style: {
      shape: "diamond",
    },
  },
  {
    selector: ".artifact, .citation",
    style: {
      shape: "hexagon",
    },
  },
] as unknown as cytoscape.StylesheetJson;

type EvidenceGraphPanelProps = {
  graph?: EvidenceGraph | null;
  error?: string;
  isLoading?: boolean;
  onRetry?: () => void;
};

function ratioWidth(value: number): string {
  const bounded = Math.max(0, Math.min(1, Number.isFinite(value) ? value : 0));
  return `${Math.round(bounded * 100)}%`;
}

function compactLabel(value: string): string {
  const display = displayText(value);
  return display.length > 22 ? `${display.slice(0, 19)}...` : display;
}

function nodeSize(node: EvidenceGraphNode): number {
  return 26 + Math.max(0, Math.min(4, node.style.importance)) * 7;
}

function edgeWidth(edge: EvidenceGraphEdge): number {
  return Math.max(1.1, Math.min(3, 1.2 + edge.weight));
}

function nodeClasses(node: EvidenceGraphNode): string {
  const classes: string[] = [node.node_class];
  if (!importantNodeClasses.has(node.node_class) && node.style.importance < 4) {
    classes.push("muted-label");
  }
  return classes.join(" ");
}

function buildElements(graph: EvidenceGraph): cytoscape.ElementDefinition[] {
  return [
    ...graph.nodes.map((node) => {
      const palette = tonePalette[node.style.tone] ?? tonePalette.neutral;
      return {
        data: {
          id: node.node_id,
          label: compactLabel(node.label),
          fill: palette.fill,
          border: palette.border,
          size: nodeSize(node),
        },
        classes: nodeClasses(node),
        grabbable: true,
      };
    }),
    ...graph.edges.map((edge) => ({
      data: {
        id: edge.edge_id,
        source: edge.source,
        target: edge.target,
        width: edgeWidth(edge),
      },
      classes: edge.edge_class,
    })),
  ];
}

function GraphStatePanel({
  error,
  isLoading,
  onRetry,
}: {
  error?: string;
  isLoading?: boolean;
  onRetry?: () => void;
}) {
  if (isLoading) {
    return (
      <section className="clinical-panel overflow-hidden">
        <div className="grid gap-0 xl:grid-cols-[minmax(0,1fr)_22rem]">
          <div className="min-h-[28rem] animate-pulse bg-gradient-to-br from-surface-muted via-white to-surface-muted" />
          <div className="border-t border-line p-5 xl:border-l xl:border-t-0">
            <div className="h-3 w-28 rounded bg-surface-strong" />
            <div className="mt-5 h-8 w-48 rounded bg-surface-strong" />
            <div className="mt-6 grid gap-3">
              <div className="h-20 rounded bg-surface-muted" />
              <div className="h-20 rounded bg-surface-muted" />
              <div className="h-20 rounded bg-surface-muted" />
            </div>
          </div>
        </div>
      </section>
    );
  }

  if (error) {
    return (
      <section className="clinical-panel border-l-[4px] border-l-review p-5 md:p-6">
        <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
          <div className="flex items-start gap-4">
            <WarningCircle className="mt-1 text-review" size={30} weight="duotone" />
            <div>
              <p className="label-caps text-review">Evidence graph unavailable</p>
              <h3 className="mt-2 font-display text-2xl font-semibold tracking-tight text-ink">
                Persisted decision still leads
              </h3>
              <p className="mt-3 max-w-[72ch] text-sm leading-6 text-ink-muted">
                The graph is a read-only V2 investigation layer. Decision, evidence, risk, trace, and copilot panels
                should remain usable while this endpoint is retried.
              </p>
            </div>
          </div>
          <div className="grid gap-3">
            <code className="max-w-full overflow-x-auto rounded border border-line bg-surface-muted px-3 py-2 font-data text-xs text-ink-muted md:max-w-md">
              {error}
            </code>
            {onRetry ? (
              <button className="route-card px-4 py-2 text-left label-caps text-ink" onClick={onRetry} type="button">
                Retry graph
              </button>
            ) : null}
          </div>
        </div>
      </section>
    );
  }

  return (
    <section className="clinical-panel p-5 md:p-6">
      <p className="label-caps text-ink-muted">Evidence graph</p>
      <h3 className="mt-2 font-display text-2xl font-semibold tracking-tight text-ink">No graph loaded yet</h3>
      <p className="mt-3 max-w-[70ch] text-sm leading-6 text-ink-muted">
        V2.21 provides the graph renderer as a reusable component. V2.22 will attach it to live case data.
      </p>
    </section>
  );
}

function StatTile({
  label,
  ratio,
  value,
}: {
  label: string;
  ratio?: number;
  value: string;
}) {
  return (
    <div className="rounded-lg border border-line bg-white/85 p-3">
      <p className="label-caps text-ink-muted">{label}</p>
      <p className="mt-2 font-display text-xl font-bold tracking-tight text-ink">{value}</p>
      {ratio !== undefined ? (
        <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-surface-strong">
          <div className="h-full rounded-full bg-defer" style={{ width: ratioWidth(ratio) }} />
        </div>
      ) : null}
    </div>
  );
}

function Legend({ graph }: { graph: EvidenceGraph }) {
  const counts = graph.nodes.reduce<Record<string, number>>((accumulator, node) => {
    accumulator[node.node_class] = (accumulator[node.node_class] ?? 0) + 1;
    return accumulator;
  }, {});
  const ordered = Object.entries(counts).sort((left, right) => right[1] - left[1]).slice(0, 9);

  return (
    <div className="grid gap-2">
      {ordered.map(([nodeClass, count]) => (
        <div className="flex items-center justify-between gap-3 rounded border border-line bg-surface-muted px-3 py-2" key={nodeClass}>
          <span className="label-caps truncate text-ink-muted">{humanizeToken(nodeClass)}</span>
          <span className="font-data text-xs font-bold text-ink">{formatCount(count)}</span>
        </div>
      ))}
    </div>
  );
}

function EvidenceRefs({ node }: { node: EvidenceGraphNode }) {
  const refs = [...node.evidence_refs, ...node.artifact_refs];
  if (refs.length === 0) {
    return (
      <p className="rounded border border-line bg-surface-muted p-3 text-xs leading-5 text-ink-muted">
        No direct evidence or artifact refs on this node.
      </p>
    );
  }

  return (
    <div className="flex flex-wrap gap-2">
      {refs.slice(0, 10).map((ref) => (
        <span
          className="inline-flex max-w-full items-center gap-2 rounded border border-line bg-white px-2.5 py-1 font-data text-[0.66rem] font-bold uppercase tracking-[0.1em] text-ink-muted"
          key={ref}
        >
          <LinkSimple size={12} weight="bold" />
          <span className="truncate">{ref}</span>
        </span>
      ))}
    </div>
  );
}

function NodeDrawer({ node }: { node: EvidenceGraphNode | null }) {
  if (!node) {
    return (
      <aside className="rounded-lg border border-line bg-white/85 p-4">
        <p className="label-caps text-ink-muted">Node inspector</p>
        <p className="mt-3 text-sm leading-6 text-ink-muted">
          Select a graph node to inspect its source fields, evidence IDs, artifact refs, and role in the decision.
        </p>
      </aside>
    );
  }

  const palette = tonePalette[node.style.tone] ?? tonePalette.neutral;

  return (
    <aside className="rounded-lg border border-line bg-white/90 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className={`rounded border px-2.5 py-1 label-caps ${palette.badge}`}>
          {humanizeToken(node.node_class)}
        </span>
        <span className="label-caps text-ink-muted">Importance {formatCount(node.style.importance)}</span>
      </div>
      <h4 className="mt-4 break-words font-display text-2xl font-bold tracking-tight text-ink">{node.label}</h4>
      <p className="mt-3 text-sm leading-6 text-ink-muted">{node.summary}</p>

      {node.details.length > 0 ? (
        <dl className="mt-5 grid gap-2">
          {node.details.slice(0, 8).map((detail) => (
            <div className="rounded border border-line bg-surface-muted p-3" key={detail.key}>
              <dt className="label-caps text-ink-muted">{detail.label}</dt>
              <dd className="mt-2 break-words font-data text-xs font-bold text-ink">
                {typeof detail.value === "boolean" ? String(detail.value) : displayText(String(detail.value ?? ""))}
              </dd>
            </div>
          ))}
        </dl>
      ) : null}

      <div className="mt-5">
        <p className="label-caps text-ink-muted">Evidence links</p>
        <div className="mt-3">
          <EvidenceRefs node={node} />
        </div>
      </div>
    </aside>
  );
}

export function EvidenceGraphPanel({ error, graph, isLoading, onRetry }: EvidenceGraphPanelProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const cytoscapeRef = useRef<cytoscape.Core | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [renderError, setRenderError] = useState<string | null>(null);

  useEffect(() => {
    if (!graph || !containerRef.current) {
      return undefined;
    }
    ensureFcoseLayout();
    setRenderError(null);
    const initialNodeId =
      graph.nodes.find((node) => node.node_class === "decision")?.node_id ?? graph.nodes[0]?.node_id ?? null;
    setSelectedNodeId(initialNodeId);

    try {
      const cy = cytoscape({
        container: containerRef.current,
        elements: buildElements(graph),
        layout: {
          name: "fcose",
          animate: false,
          fit: true,
          nodeDimensionsIncludeLabels: true,
          padding: 38,
          quality: "proof",
          randomize: false,
          idealEdgeLength: 94,
          nodeRepulsion: 7200,
          edgeElasticity: 0.38,
        } as cytoscape.LayoutOptions,
        maxZoom: 2.3,
        minZoom: 0.35,
        style: cytoscapeStyle,
        wheelSensitivity: 0.16,
      });
      cytoscapeRef.current = cy;
      if (initialNodeId) {
        cy.$id(initialNodeId).select();
      }
      cy.on("tap", "node", (event) => {
        setSelectedNodeId(event.target.id());
      });
      cy.on("tap", (event) => {
        if (event.target === cy) {
          setSelectedNodeId(null);
        }
      });
      cy.ready(() => {
        cy.fit(undefined, 42);
      });
      return () => {
        cy.destroy();
        cytoscapeRef.current = null;
      };
    } catch (err) {
      setRenderError(err instanceof Error ? err.message : "Cytoscape render failed.");
      return undefined;
    }
  }, [graph]);

  if (!graph || isLoading || error || renderError) {
    return <GraphStatePanel error={error ?? renderError ?? undefined} isLoading={isLoading} onRetry={onRetry} />;
  }

  const selectedNode = graph.nodes.find((node) => node.node_id === selectedNodeId) ?? null;
  const providerCallsTriggered = graph.metadata.provider_calls_triggered === true;
  const graphStatus = graph.stats.weakly_connected
    ? "Connected investigation map"
    : `${formatCount(graph.stats.connected_component_count)} graph components`;

  return (
    <section className="clinical-panel overflow-hidden">
      <div className="border-b border-line bg-gradient-to-br from-surface-muted via-white to-white p-5 md:p-6">
        <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_26rem] xl:items-end">
          <div className="flex min-w-0 gap-4">
            <div className="mt-1 text-defer">
              <GitBranch size={38} weight="duotone" />
            </div>
            <div className="min-w-0">
              <p className="label-caps text-ink-muted">Evidence graph</p>
              <h3 className="mt-3 max-w-[18ch] font-display text-4xl font-bold leading-none tracking-[-0.045em] text-ink md:text-5xl">
                Decision evidence map
              </h3>
              <p className="mt-4 max-w-[88ch] text-sm leading-6 text-ink-muted">
                Deterministic graph of sample, organism, drug, mechanisms, risk signals, citations, execution gate,
                reasoning trace, and final triage. The canvas is exploratory; the persisted decision remains the source
                of truth.
              </p>
            </div>
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <StatTile label="Completeness" ratio={graph.stats.completeness_ratio} value={formatPercent(graph.stats.completeness_ratio, 0)} />
            <StatTile label="Artifact linkage" ratio={graph.stats.artifact_linkage_ratio} value={formatPercent(graph.stats.artifact_linkage_ratio, 0)} />
            <StatTile label="Citation linkage" ratio={graph.stats.citation_linkage_ratio} value={formatPercent(graph.stats.citation_linkage_ratio, 0)} />
            <StatTile label="Graph status" value={graphStatus} />
          </div>
        </div>
      </div>

      <div className="grid gap-0 xl:grid-cols-[minmax(0,1fr)_26rem]">
        <div className="min-w-0 bg-[#f8fafc] p-4 md:p-6">
          <div className="relative min-h-[34rem] overflow-hidden rounded-xl border border-line bg-white shadow-[inset_0_1px_0_rgba(255,255,255,0.75)]">
            <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_20%_20%,rgba(37,99,235,0.08),transparent_17rem),radial-gradient(circle_at_80%_30%,rgba(15,118,110,0.08),transparent_16rem)]" />
            <div ref={containerRef} className="absolute inset-0" />
            <div className="absolute left-4 top-4 flex flex-wrap gap-2">
              <button
                className="rounded border border-line bg-white/90 px-3 py-2 font-data text-[0.68rem] font-bold uppercase tracking-[0.12em] text-ink transition duration-300 ease-out hover:-translate-y-0.5 active:translate-y-0"
                onClick={() => cytoscapeRef.current?.fit(undefined, 42)}
                type="button"
              >
                Fit graph
              </button>
              <button
                className="rounded border border-line bg-white/90 px-3 py-2 font-data text-[0.68rem] font-bold uppercase tracking-[0.12em] text-ink transition duration-300 ease-out hover:-translate-y-0.5 active:translate-y-0"
                onClick={() => {
                  const decisionNodeId = graph.nodes.find((node) => node.node_class === "decision")?.node_id;
                  if (!decisionNodeId || !cytoscapeRef.current) {
                    return;
                  }
                  cytoscapeRef.current.elements().unselect();
                  cytoscapeRef.current.$id(decisionNodeId).select();
                  cytoscapeRef.current.animate({ center: { eles: cytoscapeRef.current.$id(decisionNodeId) }, zoom: 1.28 }, { duration: 320 });
                  setSelectedNodeId(decisionNodeId);
                }}
                type="button"
              >
                Focus decision
              </button>
            </div>
            <div className="absolute bottom-4 left-4 right-4 flex flex-wrap items-center gap-2 rounded-lg border border-line bg-white/90 px-3 py-2 text-xs text-ink-muted">
              <ArrowsOutSimple size={15} weight="bold" />
              <span>Pan, zoom, and select nodes. Labels are intentionally sparse to avoid graph noise.</span>
            </div>
          </div>
        </div>

        <aside className="grid gap-4 border-t border-line bg-surface-muted/55 p-5 md:p-6 xl:border-l xl:border-t-0">
          <div className="grid grid-cols-2 gap-3">
            <StatTile label="Nodes" value={formatCount(graph.stats.node_count)} />
            <StatTile label="Edges" value={formatCount(graph.stats.edge_count)} />
            <StatTile label="Warnings" value={formatCount(graph.stats.warning_nodes)} />
            <StatTile label="Isolated" value={formatCount(graph.stats.isolated_node_count)} />
          </div>

          <div className="rounded-lg border border-line bg-white/85 p-4">
            <div className="flex items-center gap-3">
              <ListChecks size={24} weight="duotone" className="text-defer" />
              <div>
                <p className="label-caps text-ink-muted">Graph proof</p>
                <p className="mt-2 text-sm font-semibold text-ink">
                  {providerCallsTriggered ? "Provider call reported" : "No OpenRouter or Thesys call"}
                </p>
              </div>
            </div>
            <p className="mt-3 text-xs leading-5 text-ink-muted">
              Built from the backend graph contract. Completeness is transparency coverage, not a scientific confidence
              score.
            </p>
          </div>

          <div className="rounded-lg border border-line bg-white/85 p-4">
            <p className="label-caps text-ink-muted">Node class mix</p>
            <div className="mt-3">
              <Legend graph={graph} />
            </div>
          </div>

          <NodeDrawer node={selectedNode} />
        </aside>
      </div>
    </section>
  );
}
