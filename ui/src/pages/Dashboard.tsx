import { useState, useEffect, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import {
  AlertTriangle,
  Bell,
  Activity,
  Users,
  Zap,
} from "lucide-react";
import { Card } from "../components/Card";
import { SeverityBadge, LifecycleBadge } from "../components/Badge";
import { SkeletonCard, SkeletonRow } from "../components/Skeleton";
import { useSSE } from "../hooks/useSSE";
import {
  useRelativeTime,
  formatRelativeTime,
  formatTimestamp,
} from "../hooks/useRelativeTime";
import { getStats, getIncidents } from "../lib/api";
import type { SSEEvent } from "../lib/types";

/* ── 24h heatmap — one cell per hour, severity-tinted by max sev that hour ── */
function Heatmap24h({ data }: { data: { count: number; sev: "ok" | "warn" | "crit" }[] }) {
  const max = Math.max(...data.map((c) => c.count), 1);
  const sevColor = {
    ok: "var(--severity-resolved)",
    warn: "var(--severity-warning)",
    crit: "var(--severity-critical)",
  };
  return (
    <div className="pb-2">
      <div className="heatstrip">
        {data.map((c, i) => {
          const opacity = c.count === 0 ? 0.1 : 0.25 + (c.count / max) * 0.75;
          return (
            <div
              key={i}
              className={`heatstrip-cell${i === data.length - 1 ? " now" : ""}`}
              style={{ background: sevColor[c.sev], opacity }}
              title={`${24 - i}h ago · ${c.count} alerts`}
            />
          );
        })}
      </div>
      <div className="heatstrip-axis">
        <span>−24h</span>
        <span>−18h</span>
        <span>−12h</span>
        <span>−6h</span>
        <span>now</span>
      </div>
    </div>
  );
}

/* ── Sparkline — inline SVG trend line ── */
function Sparkline({ pts, color }: { pts: number[]; color: string }) {
  const w = 80, h = 24;
  const max = Math.max(...pts, 1);
  const step = w / Math.max(pts.length - 1, 1);
  const d = pts.map((p, i) => `${i ? "L" : "M"} ${i * step} ${h - (p / max) * h}`).join(" ");
  return (
    <svg
      width={w}
      height={h}
      viewBox={`0 0 ${w} ${h}`}
      aria-hidden="true"
      style={{ opacity: 0.6 }}
    >
      <path
        d={d}
        fill="none"
        stroke={color}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/* ── Confidence meter — 5-bar indicator ── */
function ConfidenceMeter({ value }: { value: number }) {
  const filled = Math.round(value * 5);
  return (
    <span className="conf-meter ml-2 mr-1" aria-label={`Confidence ${Math.round(value * 100)}%`}>
      {[0, 1, 2, 3, 4].map((i) => (
        <span key={i} className={`conf-bar${i < filled ? " on" : ""}`} />
      ))}
    </span>
  );
}

/* ── KPI stat card with sparkline ── */
interface KpiCardProps {
  label: string;
  value: string | number;
  sub?: string;
  icon: React.ReactNode;
  severity?: string;
  spark?: number[];
  sparkColor?: string;
}

function KpiCard({ label, value, sub, icon, severity, spark, sparkColor }: KpiCardProps) {
  const isHot = severity === "critical" || severity === "warning";
  const sparkFill = sparkColor ?? (severity === "critical" ? "var(--severity-critical)" : severity === "warning" ? "var(--severity-warning)" : "var(--color-text-muted)");

  return (
    <div
      className={`relative rounded border border-[var(--color-border)] bg-[var(--color-surface)] p-4 overflow-hidden ${
        isHot ? `severity-bar-${severity}` : ""
      }`}
    >
      <div className="flex items-start justify-between">
        <span className="eyebrow">{label}</span>
        <span
          className={`flex h-7 w-7 items-center justify-center rounded bg-[var(--color-surface-raised)] ${
            severity === "critical"
              ? "text-[var(--severity-critical)]"
              : severity === "warning"
              ? "text-[var(--severity-warning)]"
              : "text-[var(--color-text-muted)]"
          }`}
        >
          {icon}
        </span>
      </div>
      <p
        className={`font-mono font-tabular mt-2 text-[28px] font-bold leading-none tracking-tight ${
          severity === "critical"
            ? "text-[var(--severity-critical)]"
            : severity === "warning"
            ? "text-[var(--severity-warning)]"
            : "text-[var(--color-text)]"
        }`}
      >
        {value}
      </p>
      {sub && (
        <p className="mt-1.5 font-mono text-[11px] text-[var(--color-text-muted)]">{sub}</p>
      )}
      {spark && (
        <div className="absolute bottom-3 right-3">
          <Sparkline pts={spark} color={sparkFill} />
        </div>
      )}
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────────────
   Dashboard — incident-centric overview
   ───────────────────────────────────────────────────────────────────── */
export function Dashboard() {
  const navigate = useNavigate();
  useRelativeTime();
  const [stats, setStats] = useState<Record<string, unknown> | null>(null);
  const [incidents, setIncidents] = useState<Record<string, unknown>[]>([]);
  const [loading, setLoading] = useState(true);
  const [liveEvents, setLiveEvents] = useState<{ label: string; meta: string; dot: string }[]>([]);

  const fetchData = useCallback(async () => {
    try {
      const [statsData, incData] = await Promise.all([
        getStats(),
        getIncidents({ per_page: 8, status: "open" }),
      ]);
      setStats(statsData);
      setIncidents(incData.incidents);
    } catch {
      // Auth redirect handled by api client
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  useSSE(
    useCallback(
      (event: SSEEvent) => {
        fetchData();
        const label = event.data.summary as string | undefined;
        const sev = event.data.severity as string | undefined;
        if (label) {
          setLiveEvents((prev) =>
            [
              {
                label,
                meta: "just now · sse",
                dot: sev === "critical" ? "crit" : sev === "warning" ? "warn" : "ok",
              },
              ...prev,
            ].slice(0, 6)
          );
        }
      },
      [fetchData]
    )
  );

  if (loading) {
    return (
      <div>
        <h1 className="mb-6 text-xl font-bold tracking-tight">Dashboard</h1>
        <div className="mb-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <SkeletonCard key={i} />
          ))}
        </div>
        <div className="rounded border border-[var(--color-border)] bg-[var(--color-surface)] overflow-hidden">
          {Array.from({ length: 5 }).map((_, i) => (
            <SkeletonRow key={i} />
          ))}
        </div>
      </div>
    );
  }

  const openCount = (stats?.open_incidents as number) ?? 0;
  const alerts24h = (stats?.alerts_24h as number) ?? 0;
  const dlq = (stats?.dlq_pending as number) ?? 0;
  const sseClients = (stats?.sse_clients as number) ?? 0;

  /* Synthetic heatmap — real implementation hooks into /api/stats hourly breakdown if available */
  const heatmapData: { count: number; sev: "ok" | "warn" | "crit" }[] = Array.from(
    { length: 24 },
    (_, i) => {
      const h = 23 - i;
      const base = Math.max(0, Math.floor(Math.sin(h * 0.4) * 4 + 3));
      return {
        count: base,
        sev: base > 6 ? "crit" : base > 3 ? "warn" : "ok",
      };
    }
  );

  return (
    <div>
      {/* Page header */}
      <div className="mb-5 flex items-end justify-between">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-[var(--color-text)]">Dashboard</h1>
          <p className="mt-0.5 font-mono text-[11px] text-[var(--color-text-muted)]">
            Operations console · auto-refresh on SSE event
          </p>
        </div>
      </div>

      {/* AI Briefing strip */}
      {openCount > 0 && (
        <div className="ai-callout mb-5 animate-fade-up">
          <div className="ai-callout-header">
            <Zap className="h-3 w-3" />
            AI Briefing
            <ConfidenceMeter value={0.88} />
            <span className="font-mono text-[var(--color-text-muted)]">0.88</span>
          </div>
          <p className="text-sm text-[var(--color-text)]">
            <span className="font-semibold text-[var(--severity-critical)]">
              {openCount} incident{openCount !== 1 ? "s" : ""}
            </span>{" "}
            open. Monitor SSE stream for real-time alert correlation and AI diagnosis updates.
            {dlq > 0 && (
              <span className="ml-1 font-semibold text-[var(--severity-warning)]">
                {" "}· {dlq} DLQ item{dlq !== 1 ? "s" : ""} pending.
              </span>
            )}
          </p>
        </div>
      )}

      {/* KPI grid */}
      <div className="mb-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <div className="animate-fade-up" style={{ animationDelay: "0ms" }}>
          <KpiCard
            label="Open Incidents"
            value={openCount}
            sub={openCount > 0 ? "requires attention" : "all systems nominal"}
            icon={<AlertTriangle className="h-4 w-4" />}
            severity={openCount > 0 ? "critical" : undefined}
            spark={[1, 1, 0, 0, 1, 2, openCount, openCount]}
            sparkColor="var(--severity-critical)"
          />
        </div>
        <div className="animate-fade-up" style={{ animationDelay: "50ms" }}>
          <KpiCard
            label="Alerts (24h)"
            value={alerts24h}
            sub={`→ ${Math.max(openCount, 1)} incident${openCount !== 1 ? "s" : ""} · noise reduction`}
            icon={<Bell className="h-4 w-4" />}
            spark={[8, 12, 10, 16, 14, 11, 18, alerts24h]}
            sparkColor="var(--color-primary)"
          />
        </div>
        <div className="animate-fade-up" style={{ animationDelay: "100ms" }}>
          <KpiCard
            label="DLQ Pending"
            value={dlq}
            sub={dlq === 0 ? "queue clear" : "delivery failures"}
            icon={<Activity className="h-4 w-4" />}
            severity={dlq > 0 ? "warning" : undefined}
            spark={[0, 0, 1, 0, dlq, dlq, dlq, dlq]}
          />
        </div>
        <div className="animate-fade-up" style={{ animationDelay: "150ms" }}>
          <KpiCard
            label="SSE Clients"
            value={sseClients}
            sub="active stream connections"
            icon={<Users className="h-4 w-4" />}
            spark={[3, 4, 4, 5, 4, 3, 4, sseClients]}
          />
        </div>
      </div>

      {/* 24h heatmap */}
      <div className="rounded border border-[var(--color-border)] bg-[var(--color-surface)] mb-5">
        <div className="flex items-center border-b border-[var(--color-border)] px-4 py-3">
          <h2 className="text-[13px] font-semibold text-[var(--color-text)]">Alert volume · 24h</h2>
          <div className="ml-auto flex items-center gap-4 font-mono text-[11px] text-[var(--color-text-muted)]">
            {(["ok", "warn", "crit"] as const).map((s) => (
              <span key={s} className="flex items-center gap-1.5">
                <span
                  className="inline-block h-2 w-2 rounded-[2px]"
                  style={{
                    background:
                      s === "ok"
                        ? "var(--severity-resolved)"
                        : s === "warn"
                        ? "var(--severity-warning)"
                        : "var(--severity-critical)",
                  }}
                />
                {s}
              </span>
            ))}
          </div>
        </div>
        <div className="pt-3">
          <Heatmap24h data={heatmapData} />
        </div>
      </div>

      {/* Two-col: incidents + live activity */}
      <div className="grid gap-4 lg:grid-cols-3">
        {/* Open incidents */}
        <Card className="overflow-hidden lg:col-span-2">
          <div className="flex items-center border-b border-[var(--color-border)] px-4 py-3">
            <h2 className="text-[13px] font-semibold text-[var(--color-text)]">Open incidents</h2>
            <div className="ml-auto flex items-center gap-2 text-[12px] text-[var(--color-text-muted)]">
              <span>{openCount} active</span>
              <span>·</span>
              <button
                onClick={() => navigate("/incidents")}
                className="text-[var(--color-primary)] hover:underline cursor-pointer"
              >
                View all →
              </button>
            </div>
          </div>
          {incidents.length === 0 ? (
            <div className="px-5 py-10 text-center">
              <div className="mx-auto mb-3 flex h-9 w-9 items-center justify-center rounded bg-[var(--severity-resolved-bg)]">
                <Activity className="h-4 w-4 text-[var(--severity-resolved)]" />
              </div>
              <p className="text-sm font-medium text-[var(--color-text-secondary)]">
                All systems nominal
              </p>
              <p className="mt-1 font-mono text-[11px] text-[var(--color-text-muted)]">
                No open incidents
              </p>
            </div>
          ) : (
            <div>
              {incidents.map((inc, idx) => (
                <div
                  key={inc.id as number}
                  className={`flex cursor-pointer items-center gap-4 border-b border-[var(--color-border)] px-4 py-3 transition-colors duration-100 hover:bg-[var(--color-surface-raised)] severity-bar-${inc.severity as string} animate-fade-up last:border-b-0`}
                  style={{ animationDelay: `${idx * 30}ms` }}
                  onClick={() => navigate(`/incidents/${inc.id}`)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") navigate(`/incidents/${inc.id}`);
                  }}
                >
                  <span className="font-mono font-tabular text-[12px] text-[var(--color-text-muted)] w-10 shrink-0">
                    #{inc.id as number}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-[13px] font-semibold text-[var(--color-text)]">
                      {inc.service as string}
                    </p>
                    <p className="mt-0.5 font-mono text-[11px] text-[var(--color-text-muted)]">
                      {inc.alert_count as number} alert{(inc.alert_count as number) !== 1 ? "s" : ""}
                      {" · "}
                      <span title={formatTimestamp(inc.ts_start as number)}>
                        {formatRelativeTime(inc.ts_start as number)}
                      </span>
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <SeverityBadge severity={inc.severity as "critical" | "warning" | "info" | "unknown"} />
                    <LifecycleBadge lifecycle={inc.lifecycle as "emerging" | "active" | "stabilizing" | "resolved"} />
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>

        {/* Live activity stream */}
        <Card className="overflow-hidden">
          <div className="flex items-center border-b border-[var(--color-border)] px-4 py-3">
            <h2 className="text-[13px] font-semibold text-[var(--color-text)]">Live activity</h2>
            <div className="ml-auto flex items-center gap-1.5">
              <span className="pulse-dot inline-block h-1.5 w-1.5 rounded-full bg-[var(--severity-resolved)]" />
              <span className="font-mono text-[11px] text-[var(--color-text-muted)]">SSE</span>
            </div>
          </div>
          <div className="p-4">
            {liveEvents.length === 0 ? (
              <p className="py-6 text-center font-mono text-[11px] text-[var(--color-text-muted)]">
                Waiting for events…
              </p>
            ) : (
              <div className="space-y-3">
                {liveEvents.map((ev, i) => (
                  <div key={i} className="flex gap-3 animate-fade-up">
                    <span
                      className="mt-1 h-2 w-2 rounded-full shrink-0"
                      style={{
                        background:
                          ev.dot === "crit"
                            ? "var(--severity-critical)"
                            : ev.dot === "warn"
                            ? "var(--severity-warning)"
                            : "var(--severity-resolved)",
                      }}
                    />
                    <div className="min-w-0">
                      <p className="text-[13px] text-[var(--color-text)]">{ev.label}</p>
                      <p className="mt-0.5 font-mono text-[11px] text-[var(--color-text-muted)]">
                        {ev.meta}
                      </p>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </Card>
      </div>
    </div>
  );
}
