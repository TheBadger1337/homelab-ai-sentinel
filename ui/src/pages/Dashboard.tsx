import { useState, useEffect, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import {
  AlertTriangle,
  Bell,
  Radio,
  Cpu,
  Database,
  Activity,
  Users,
} from "lucide-react";
import { StatCard, Card } from "../components/Card";
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

/**
 * Dashboard — incident-centric overview.
 * Real-time updates via SSE. Stat cards + live incident feed.
 *
 * frontend-design: staggered entrance, atmospheric stat cards, bold numbers.
 * ui-ux-pro-max: skeleton loading, tabular figures, keyboard nav.
 */
export function Dashboard() {
  const navigate = useNavigate();
  useRelativeTime();
  const [stats, setStats] = useState<Record<string, unknown> | null>(null);
  const [incidents, setIncidents] = useState<Record<string, unknown>[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchData = useCallback(async () => {
    try {
      const [statsData, incData] = await Promise.all([
        getStats(),
        getIncidents({ per_page: 10, status: "open" }),
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

  // SSE live updates
  useSSE(
    useCallback(
      (_event: SSEEvent) => {
        fetchData();
      },
      [fetchData]
    )
  );

  if (loading) {
    return (
      <div>
        <h1 className="mb-8 text-xl font-bold tracking-tight">Dashboard</h1>
        <div className="mb-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <SkeletonCard key={i} />
          ))}
        </div>
        <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] overflow-hidden">
          {Array.from({ length: 5 }).map((_, i) => (
            <SkeletonRow key={i} />
          ))}
        </div>
      </div>
    );
  }

  const openCount = (stats?.open_incidents as number) ?? 0;
  const alerts24h = (stats?.alerts_24h as number) ?? 0;
  const platforms = (stats?.active_platforms as string[]) ?? [];
  const mode = (stats?.mode as string) ?? "unknown";
  const sseClients = (stats?.sse_clients as number) ?? 0;
  const dlq = (stats?.dlq_pending as number) ?? 0;

  return (
    <div>
      <h1 className="mb-8 text-xl font-bold tracking-tight">Dashboard</h1>

      {/* Primary stat cards — staggered entrance */}
      <div className="mb-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {[
          {
            label: "Open Incidents",
            value: openCount,
            icon: <AlertTriangle className="h-5 w-5" />,
            severity: openCount > 0 ? "critical" : undefined,
          },
          {
            label: "Alerts (24h)",
            value: alerts24h,
            icon: <Bell className="h-5 w-5" />,
            severity: undefined,
          },
          {
            label: "Platforms",
            value: platforms.length,
            icon: <Radio className="h-5 w-5" />,
            severity: undefined,
          },
          {
            label: "Mode",
            value: mode,
            icon: <Cpu className="h-5 w-5" />,
            severity: undefined,
          },
        ].map((stat, i) => (
          <div
            key={stat.label}
            className="animate-fade-up"
            style={{ animationDelay: `${i * 50}ms` }}
          >
            <StatCard {...stat} />
          </div>
        ))}
      </div>

      {/* Secondary stats */}
      <div className="mb-8 grid gap-4 sm:grid-cols-3">
        <StatCard
          label="DB Alerts"
          value={String((stats?.db as Record<string, unknown>)?.total_alerts ?? "\u2014")}
          icon={<Database className="h-5 w-5" />}
        />
        <StatCard
          label="DLQ Pending"
          value={dlq}
          icon={<Activity className="h-5 w-5" />}
          severity={dlq > 0 ? "warning" : undefined}
        />
        <StatCard
          label="SSE Clients"
          value={sseClients}
          icon={<Users className="h-5 w-5" />}
        />
      </div>

      {/* Open incidents feed */}
      <Card className="overflow-hidden">
        <div className="border-b border-[var(--color-border)] px-5 py-3.5">
          <h2 className="text-[13px] font-semibold uppercase tracking-widest text-[var(--color-text-muted)]">
            Open Incidents
          </h2>
        </div>
        {incidents.length === 0 ? (
          <div className="px-5 py-12 text-center">
            <div className="mx-auto mb-3 flex h-10 w-10 items-center justify-center rounded-full bg-[var(--severity-resolved-bg)]">
              <Activity className="h-5 w-5 text-[var(--severity-resolved)]" />
            </div>
            <p className="text-sm font-medium text-[var(--color-text-secondary)]">
              All systems nominal
            </p>
            <p className="mt-1 text-xs text-[var(--color-text-muted)]">
              No open incidents
            </p>
          </div>
        ) : (
          <div>
            {incidents.map((inc, idx) => (
              <div
                key={inc.id as number}
                className={`flex cursor-pointer items-center gap-4 border-b border-[var(--color-border)] px-5 py-3.5 transition-colors duration-100 hover:bg-[var(--color-surface-raised)] severity-bar-${inc.severity as string} animate-fade-up`}
                style={{ animationDelay: `${idx * 30}ms` }}
                onClick={() => navigate(`/incidents/${inc.id}`)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => {
                  if (e.key === "Enter") navigate(`/incidents/${inc.id}`);
                }}
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="font-tabular text-[13px] font-bold text-[var(--color-text-muted)]">
                      #{inc.id as number}
                    </span>
                    <span className="truncate text-sm font-medium">
                      {inc.service as string}
                    </span>
                  </div>
                  <p className="mt-0.5 truncate text-xs text-[var(--color-text-muted)]">
                    {inc.alert_count as number} alert{(inc.alert_count as number) !== 1 ? "s" : ""}
                    {" \u00b7 "}
                    <span title={formatTimestamp(inc.ts_start as number)}>
                      {formatRelativeTime(inc.ts_start as number)}
                    </span>
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <SeverityBadge
                    severity={
                      inc.severity as "critical" | "warning" | "info" | "unknown"
                    }
                  />
                  <LifecycleBadge
                    lifecycle={
                      inc.lifecycle as
                        | "emerging"
                        | "active"
                        | "stabilizing"
                        | "resolved"
                    }
                  />
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
