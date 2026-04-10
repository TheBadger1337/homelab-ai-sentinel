import { useState, useEffect, useCallback, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { Network, Server, HardDrive, Maximize2, Minimize2, AlertTriangle } from "lucide-react";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  type Node,
  type Edge,
  type NodeProps,
  Handle,
  Position,
  useReactFlow,
  ReactFlowProvider,
} from "@xyflow/react";
import dagre from "@dagrejs/dagre";
import { SeverityBadge } from "../components/Badge";
import { Card } from "../components/Card";
import { Skeleton } from "../components/Skeleton";
import { getTopology } from "../lib/api";
import { useSSE } from "../hooks/useSSE";
import type { Severity, SSEEvent, TopologyService } from "../lib/types";

import "@xyflow/react/dist/style.css";

/* ──────────────────────────────────────────────────────────────────────
   Dagre layout engine — arranges nodes in a top-down hierarchy
   ────────────────────────────────────────────────────────────────────── */

const NODE_WIDTH = 220;
const NODE_HEIGHT = 80;
const RESOURCE_WIDTH = 200;
const RESOURCE_HEIGHT = 60;

interface LayoutInput {
  nodes: Node[];
  edges: Edge[];
}

function applyDagreLayout({ nodes, edges }: LayoutInput): {
  nodes: Node[];
  edges: Edge[];
} {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({
    rankdir: "TB",
    nodesep: 60,
    ranksep: 80,
    marginx: 40,
    marginy: 40,
  });

  nodes.forEach((node) => {
    const isResource = node.type === "resourceNode";
    g.setNode(node.id, {
      width: isResource ? RESOURCE_WIDTH : NODE_WIDTH,
      height: isResource ? RESOURCE_HEIGHT : NODE_HEIGHT,
    });
  });

  edges.forEach((edge) => {
    g.setEdge(edge.source, edge.target);
  });

  dagre.layout(g);

  const layoutNodes = nodes.map((node) => {
    const pos = g.node(node.id);
    const isResource = node.type === "resourceNode";
    const w = isResource ? RESOURCE_WIDTH : NODE_WIDTH;
    const h = isResource ? RESOURCE_HEIGHT : NODE_HEIGHT;
    return {
      ...node,
      position: { x: pos.x - w / 2, y: pos.y - h / 2 },
    };
  });

  return { nodes: layoutNodes, edges };
}

/* ──────────────────────────────────────────────────────────────────────
   Custom node: Service
   ────────────────────────────────────────────────────────────────────── */

interface ServiceNodeData {
  label: string;
  host?: string;
  description?: string;
  hasIncident: boolean;
  severity: Severity | null;
  incidentId: number | null;
  /** True when a direct upstream dependency has an active alert (live SSE). */
  isImpacted: boolean;
  [key: string]: unknown;
}

function ServiceNode({ data }: NodeProps<Node<ServiceNodeData>>) {
  const navigate = useNavigate();
  const { hasIncident, severity, incidentId, label, host, description, isImpacted } = data;

  const severityColor = hasIncident
    ? severity === "critical"
      ? "var(--severity-critical)"
      : severity === "warning"
        ? "var(--severity-warning)"
        : "var(--severity-info)"
    : isImpacted
      ? "var(--severity-warning)"
      : "var(--severity-resolved)";

  const borderClass = hasIncident
    ? "border-[var(--severity-critical)]/40 shadow-[0_0_12px_rgba(244,63,94,0.15)]"
    : isImpacted
      ? "border-[var(--severity-warning)]/30"
      : "border-[var(--color-border)] hover:border-[var(--color-text-muted)]";

  return (
    <div
      className={`group relative rounded-xl border bg-[var(--color-surface)] px-4 py-3 transition-all duration-150 ${borderClass} ${
        isImpacted && !hasIncident ? "opacity-60" : ""
      } ${incidentId ? "cursor-pointer" : ""}`}
      style={{ width: NODE_WIDTH }}
      onClick={
        incidentId ? () => navigate(`/incidents/${incidentId}`) : undefined
      }
      role={incidentId ? "button" : undefined}
      tabIndex={incidentId ? 0 : undefined}
      onKeyDown={
        incidentId
          ? (e) => {
              if (e.key === "Enter") navigate(`/incidents/${incidentId}`);
            }
          : undefined
      }
    >
      <Handle type="target" position={Position.Top} className="!bg-[var(--color-border)] !border-[var(--color-surface)] !w-2 !h-2" />

      <div className="flex items-center gap-2.5">
        <div
          className={`h-2.5 w-2.5 shrink-0 rounded-full ${hasIncident ? "pulse-dot" : ""}`}
          style={{ backgroundColor: severityColor }}
        />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <Server className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-muted)]" />
            <span className="truncate text-sm font-semibold">{label}</span>
          </div>
          {host && (
            <p className="mt-0.5 truncate text-[10px] font-medium text-[var(--color-text-muted)]">
              {host}
            </p>
          )}
        </div>
        {hasIncident && severity && (
          <SeverityBadge severity={severity} className="scale-90" />
        )}
        {isImpacted && !hasIncident && (
          <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-[var(--severity-warning)] opacity-70" />
        )}
      </div>

      {description && (
        <p className="mt-1.5 truncate text-[10px] text-[var(--color-text-muted)] opacity-0 group-hover:opacity-100 transition-opacity">
          {description}
        </p>
      )}

      <Handle type="source" position={Position.Bottom} className="!bg-[var(--color-border)] !border-[var(--color-surface)] !w-2 !h-2" />
    </div>
  );
}

/* ──────────────────────────────────────────────────────────────────────
   Custom node: Shared Resource
   ────────────────────────────────────────────────────────────────────── */

interface ResourceNodeData {
  label: string;
  resourceType?: string;
  description?: string;
  [key: string]: unknown;
}

function ResourceNode({ data }: NodeProps<Node<ResourceNodeData>>) {
  const { label, resourceType, description } = data;

  return (
    <div
      className="rounded-lg border border-dashed border-[var(--color-border)] bg-[var(--color-surface-raised)]/60 px-3 py-2.5"
      style={{ width: RESOURCE_WIDTH }}
    >
      <Handle type="target" position={Position.Top} className="!bg-[var(--color-border)] !border-[var(--color-surface-raised)] !w-2 !h-2" />

      <div className="flex items-center gap-2">
        <HardDrive className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-muted)]" />
        <span className="truncate text-xs font-semibold">{label}</span>
      </div>
      {(resourceType || description) && (
        <p className="mt-1 truncate text-[10px] text-[var(--color-text-muted)]">
          {resourceType}
          {description ? ` \u2014 ${description}` : ""}
        </p>
      )}

      <Handle type="source" position={Position.Bottom} className="!bg-[var(--color-border)] !border-[var(--color-surface-raised)] !w-2 !h-2" />
    </div>
  );
}

/* ──────────────────────────────────────────────────────────────────────
   Cascade impact computation
   ────────────────────────────────────────────────────────────────────── */

/**
 * Given a set of services with active live alerts, return the set of
 * service names (lowercase) that transitively depend on any of them.
 * These are "impacted" downstream services — they may be affected by
 * the upstream failure even if they haven't alerted yet.
 */
function getCascadeImpacted(
  alertingKeys: Set<string>,
  services: Record<string, TopologyService>,
): Set<string> {
  const impacted = new Set<string>();

  function traverse(upstream: string) {
    for (const [name, svc] of Object.entries(services)) {
      const key = name.toLowerCase();
      if (impacted.has(key) || alertingKeys.has(key)) continue;
      if ((svc.depends_on ?? []).some((d) => d.toLowerCase() === upstream)) {
        impacted.add(key);
        traverse(key);
      }
    }
  }

  for (const key of alertingKeys) {
    traverse(key);
  }
  return impacted;
}

/* ──────────────────────────────────────────────────────────────────────
   Graph builder — converts API topology into React Flow nodes/edges
   ────────────────────────────────────────────────────────────────────── */

interface TopologyData {
  services: Record<string, TopologyService>;
  shared_resources: Record<string, unknown>;
}

function buildGraph(data: TopologyData): { nodes: Node[]; edges: Edge[] } {
  const nodes: Node[] = [];
  const edges: Edge[] = [];
  const services = data.services ?? {};
  const resources = data.shared_resources ?? {};

  // Service nodes
  Object.entries(services).forEach(([name, svc]) => {
    nodes.push({
      id: `svc-${name}`,
      type: "serviceNode",
      position: { x: 0, y: 0 }, // dagre will set this
      data: {
        label: name,
        host: svc.host,
        description: svc.description,
        hasIncident: svc.has_incident,
        severity: svc.incident_severity,
        incidentId: svc.incident_id,
        isImpacted: false, // overridden by live SSE state
      },
    });

    // depends_on edges (child → parent = downstream → upstream)
    const deps = svc.depends_on ?? [];
    deps.forEach((dep) => {
      edges.push({
        id: `dep-${name}-${dep}`,
        source: `svc-${dep}`,
        target: `svc-${name}`,
        type: "default",
        animated: false,
        style: { stroke: "var(--color-border)", strokeWidth: 1.5 },
        markerEnd: { type: "arrowclosed" as const, color: "var(--color-text-muted)" },
      });
    });

    // uses edges (service → resource)
    const uses = svc.uses ?? [];
    uses.forEach((res) => {
      edges.push({
        id: `uses-${name}-${res}`,
        source: `svc-${name}`,
        target: `res-${res}`,
        type: "default",
        animated: false,
        style: {
          stroke: "var(--color-text-muted)",
          strokeWidth: 1,
          strokeDasharray: "4 4",
        },
      });
    });
  });

  // Shared resource nodes
  Object.entries(resources).forEach(([name, res]) => {
    const resData = typeof res === "object" && res !== null ? (res as Record<string, unknown>) : {};
    nodes.push({
      id: `res-${name}`,
      type: "resourceNode",
      position: { x: 0, y: 0 },
      data: {
        label: name,
        resourceType: resData.type as string | undefined,
        description: resData.description as string | undefined,
      },
    });
  });

  return applyDagreLayout({ nodes, edges });
}

/* ──────────────────────────────────────────────────────────────────────
   Fit-to-view button
   ────────────────────────────────────────────────────────────────────── */

function FitViewButton() {
  const { fitView } = useReactFlow();
  return (
    <button
      onClick={() => fitView({ padding: 0.15, duration: 300 })}
      className="absolute right-3 top-3 z-10 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-2 text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-surface-raised)] transition-colors cursor-pointer"
      aria-label="Fit graph to view"
    >
      <Maximize2 className="h-4 w-4" />
    </button>
  );
}

/* ──────────────────────────────────────────────────────────────────────
   Main topology page
   ────────────────────────────────────────────────────────────────────── */

const nodeTypes = {
  serviceNode: ServiceNode,
  resourceNode: ResourceNode,
};

/** Live alert state received via SSE — overrides API topology on the map. */
interface LiveAlert {
  severity: Severity;
  incidentId: number | null;
}

function TopologyGraph() {
  const [data, setData] = useState<TopologyData | null>(null);
  const [loading, setLoading] = useState(true);
  const [fullscreen, setFullscreen] = useState(false);
  const [liveAlerts, setLiveAlerts] = useState<Map<string, LiveAlert>>(new Map());

  const fetchData = useCallback(async () => {
    try {
      const result = await getTopology();
      setData(result as TopologyData);
    } catch {
      // Auth redirect
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // Subscribe to SSE — update live alert state in real time.
  // "alert" events activate a node; "resolution" events clear it.
  useSSE(
    useCallback((event: SSEEvent) => {
      if (event.type === "alert") {
        const service = event.data.service as string | undefined;
        const severity = event.data.severity as Severity | undefined;
        const incidentId = (event.data.incident_id as number | null) ?? null;
        if (!service || !severity) return;
        setLiveAlerts((prev) => {
          const next = new Map(prev);
          next.set(service.toLowerCase(), { severity, incidentId });
          return next;
        });
      } else if (event.type === "resolution") {
        const service = event.data.service as string | undefined;
        if (!service) return;
        setLiveAlerts((prev) => {
          const next = new Map(prev);
          next.delete(service.toLowerCase());
          return next;
        });
      }
    }, []),
  );

  // Base graph — dagre layout runs only when topology data changes.
  const baseGraph = useMemo(() => {
    if (!data) return null;
    return buildGraph(data);
  }, [data]);

  // Apply live SSE state on top of the base graph without re-running dagre.
  // Alerting nodes glow; cascade-impacted dependents are dimmed with a warning
  // icon; edges flowing out of an alerting node animate to show propagation.
  const { nodes, edges } = useMemo(() => {
    if (!baseGraph || !data) return { nodes: [], edges: [] };

    const alertingKeys = new Set(liveAlerts.keys());
    const impacted = alertingKeys.size > 0
      ? getCascadeImpacted(alertingKeys, data.services)
      : new Set<string>();

    const nodes = baseGraph.nodes.map((node) => {
      if (node.type !== "serviceNode") return node;
      const key = (node.data as ServiceNodeData).label.toLowerCase();
      const live = liveAlerts.get(key);
      const isImpacted = !live && impacted.has(key);
      if (!live && !isImpacted) return node;
      return {
        ...node,
        data: {
          ...node.data,
          hasIncident: live ? true : (node.data as ServiceNodeData).hasIncident,
          severity: live ? live.severity : (node.data as ServiceNodeData).severity,
          incidentId: live ? live.incidentId : (node.data as ServiceNodeData).incidentId,
          isImpacted,
        },
      };
    });

    const edges = baseGraph.edges.map((edge) => {
      const srcKey = edge.source.startsWith("svc-")
        ? edge.source.slice(4).toLowerCase()
        : null;
      if (!srcKey || !alertingKeys.has(srcKey)) return edge;
      return {
        ...edge,
        animated: true,
        style: {
          ...edge.style,
          stroke: "var(--severity-warning)",
          strokeWidth: 2,
        },
      };
    });

    return { nodes, edges };
  }, [baseGraph, data, liveAlerts]);

  if (loading) {
    return (
      <div>
        <h1 className="mb-8 text-xl font-bold tracking-tight">Topology</h1>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-28 w-full" />
          ))}
        </div>
      </div>
    );
  }

  const serviceCount = Object.keys(data?.services ?? {}).length;

  if (serviceCount === 0) {
    return (
      <div>
        <h1 className="mb-8 text-xl font-bold tracking-tight">Topology</h1>
        <Card className="p-12 text-center">
          <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-[var(--color-surface-raised)]">
            <Network className="h-6 w-6 text-[var(--color-text-muted)]" />
          </div>
          <p className="text-sm font-medium text-[var(--color-text-secondary)]">
            No topology configured
          </p>
          <p className="mt-1 text-xs text-[var(--color-text-muted)]">
            Add a{" "}
            <code className="font-mono rounded bg-[var(--color-surface-raised)] px-1.5 py-0.5 text-[11px]">
              topology.yaml
            </code>{" "}
            to see your service dependency graph
          </p>
        </Card>
      </div>
    );
  }

  const graphHeight = fullscreen ? "h-[calc(100vh-2rem)]" : "h-[600px]";

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-xl font-bold tracking-tight">Topology</h1>
        <div className="flex items-center gap-3">
          <span className="text-xs text-[var(--color-text-muted)] font-tabular">
            {serviceCount} services
            {Object.keys(data?.shared_resources ?? {}).length > 0 &&
              ` \u00b7 ${Object.keys(data?.shared_resources ?? {}).length} shared resources`}
          </span>
          <button
            onClick={() => setFullscreen(!fullscreen)}
            className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-2 text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-surface-raised)] transition-colors cursor-pointer"
            aria-label={fullscreen ? "Exit fullscreen" : "Enter fullscreen"}
          >
            {fullscreen ? (
              <Minimize2 className="h-4 w-4" />
            ) : (
              <Maximize2 className="h-4 w-4" />
            )}
          </button>
        </div>
      </div>

      <div
        className={`relative overflow-hidden rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] ${graphHeight} transition-all duration-300`}
      >
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          fitView
          fitViewOptions={{ padding: 0.15 }}
          minZoom={0.3}
          maxZoom={2}
          proOptions={{ hideAttribution: true }}
          defaultEdgeOptions={{
            type: "default",
          }}
        >
          <Background
            color="var(--color-border)"
            gap={24}
            size={1}
          />
          <Controls
            showInteractive={false}
            className="!bg-[var(--color-surface)] !border-[var(--color-border)] !shadow-none [&>button]:!bg-[var(--color-surface)] [&>button]:!border-[var(--color-border)] [&>button]:!text-[var(--color-text-muted)] [&>button:hover]:!bg-[var(--color-surface-raised)]"
          />
          <MiniMap
            nodeColor={(node) => {
              if (node.type === "resourceNode") return "var(--color-text-muted)";
              const d = node.data as ServiceNodeData;
              if (d.hasIncident) {
                if (d.severity === "critical") return "var(--severity-critical)";
                if (d.severity === "warning") return "var(--severity-warning)";
                return "var(--severity-info)";
              }
              return "var(--severity-resolved)";
            }}
            maskColor="var(--color-bg)"
            className="!bg-[var(--color-surface)] !border-[var(--color-border)]"
            pannable
            zoomable
          />
          <FitViewButton />
        </ReactFlow>
      </div>
    </div>
  );
}

/**
 * Topology page — interactive service dependency graph.
 *
 * Uses React Flow for node/edge rendering with dagre for automatic
 * hierarchical layout. Services are nodes, depends_on are directed edges,
 * shared resources are dashed-border resource nodes.
 *
 * Live incident status: nodes glow red/amber with pulsing dot when
 * their service has an open incident. Click to navigate to incident detail.
 */
export function Topology() {
  return (
    <ReactFlowProvider>
      <TopologyGraph />
    </ReactFlowProvider>
  );
}
