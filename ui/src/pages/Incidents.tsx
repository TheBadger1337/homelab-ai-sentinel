import { useState, useEffect, useCallback } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { SeverityBadge, StatusBadge } from "../components/Badge";
import { Button } from "../components/Button";
import { SkeletonTable } from "../components/Skeleton";
import {
  useRelativeTime,
  formatRelativeTime,
  formatDuration,
  formatTimestamp,
} from "../hooks/useRelativeTime";
import { getIncidents } from "../lib/api";

/**
 * Incidents list — filterable, paginated table.
 * Severity bars, status badges, lifecycle state, relative times.
 */
export function Incidents() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  useRelativeTime();

  const page = Number(searchParams.get("page")) || 1;
  const statusFilter = searchParams.get("status") || "";
  const severityFilter = searchParams.get("severity") || "";
  const serviceFilter = searchParams.get("service") || "";

  const [data, setData] = useState<{
    incidents: Record<string, unknown>[];
    total: number;
    per_page: number;
  } | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const result = await getIncidents({
        page,
        per_page: 20,
        status: statusFilter,
        severity: severityFilter,
        service: serviceFilter,
      });
      setData(result);
    } catch {
      // Auth redirect handled by api client
    } finally {
      setLoading(false);
    }
  }, [page, statusFilter, severityFilter, serviceFilter]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const setFilter = (key: string, value: string) => {
    const next = new URLSearchParams(searchParams);
    if (value) {
      next.set(key, value);
    } else {
      next.delete(key);
    }
    next.set("page", "1");
    setSearchParams(next);
  };

  const totalPages = data ? Math.ceil(data.total / data.per_page) : 0;

  return (
    <div>
      <div className="mb-5 flex items-end justify-between">
        <h1 className="text-xl font-bold tracking-tight text-[var(--color-text)]">Incidents</h1>
        <span className="font-mono font-tabular text-[12px] text-[var(--color-text-muted)]">
          {data?.total ?? 0} total
        </span>
      </div>

      {/* Filter chip rail — replaces hidden dropdowns */}
      <div className="mb-4 flex flex-wrap items-center gap-2">
        {/* Status chips */}
        {(["", "open", "resolved"] as const).map((v) => (
          <button
            key={v || "all-status"}
            onClick={() => setFilter("status", v)}
            className={`filter-chip${statusFilter === v ? " active" : ""}`}
            aria-pressed={statusFilter === v}
          >
            {v || "All status"}
          </button>
        ))}
        <span className="h-4 w-px bg-[var(--color-border)]" aria-hidden="true" />
        {/* Severity chips */}
        {(["", "critical", "warning", "info"] as const).map((v) => (
          <button
            key={v || "all-sev"}
            onClick={() => setFilter("severity", v)}
            className={`filter-chip${severityFilter === v ? " active" : ""}`}
            aria-pressed={severityFilter === v}
          >
            {v || "All severity"}
          </button>
        ))}
        {/* Service search */}
        <input
          type="text"
          placeholder="Service…"
          value={serviceFilter}
          onChange={(e) => setFilter("service", e.target.value)}
          className="h-7 rounded border border-[var(--color-border)] bg-[var(--color-bg)] px-3 font-mono text-[12px] text-[var(--color-text)] placeholder-[var(--color-text-muted)] focus:outline-none focus:ring-1 focus:ring-[var(--color-primary)]"
          aria-label="Filter by service name"
        />
        {(statusFilter || severityFilter || serviceFilter) && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setSearchParams({})}
          >
            Clear
          </Button>
        )}
      </div>

      {/* Table */}
      {loading ? (
        <SkeletonTable rows={8} />
      ) : (
        <div className="overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]">
          {/* Header */}
          <div className="hidden border-b border-[var(--color-border)] bg-[var(--color-surface-raised)] px-4 py-2.5 text-xs font-medium uppercase tracking-wide text-[var(--color-text-muted)] md:grid md:grid-cols-[4rem_1fr_6rem_6rem_5rem_5rem_7rem]">
            <span>ID</span>
            <span>Service</span>
            <span>Severity</span>
            <span>Status</span>
            <span>Alerts</span>
            <span>Duration</span>
            <span>Started</span>
          </div>

          {/* Rows */}
          {!data?.incidents.length ? (
            <div className="px-4 py-8 text-center text-sm text-[var(--color-text-muted)]">
              No incidents match the current filters.
            </div>
          ) : (
            data.incidents.map((inc) => {
              const tsStart = inc.ts_start as number;
              const tsEnd = inc.ts_end as number | null;
              const duration = tsEnd
                ? tsEnd - tsStart
                : Date.now() / 1000 - tsStart;

              return (
                <div
                  key={inc.id as number}
                  className={`grid cursor-pointer items-center gap-2 border-b border-[var(--color-border)] px-4 py-3 transition-colors duration-100 hover:bg-[var(--color-surface-raised)] md:grid-cols-[4rem_1fr_6rem_6rem_5rem_5rem_7rem] severity-bar-${inc.severity as string}`}
                  onClick={() => navigate(`/incidents/${inc.id}`)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") navigate(`/incidents/${inc.id}`);
                  }}
                >
                  <span className="font-tabular text-sm font-medium text-[var(--color-text-muted)]">
                    #{inc.id as number}
                  </span>
                  <span className="truncate text-sm font-medium">
                    {inc.service as string}
                  </span>
                  <SeverityBadge severity={inc.severity as "critical" | "warning" | "info" | "unknown"} />
                  <StatusBadge status={inc.status as string} />
                  <span className="font-tabular text-sm text-[var(--color-text-secondary)]">
                    {inc.alert_count as number}
                  </span>
                  <span className="font-tabular text-sm text-[var(--color-text-secondary)]">
                    {tsEnd ? `lasted ${formatDuration(duration)}` : formatDuration(duration)}
                  </span>
                  <span
                    className="text-sm text-[var(--color-text-muted)]"
                    title={formatTimestamp(tsStart)}
                  >
                    {formatRelativeTime(tsStart)}
                  </span>
                </div>
              );
            })
          )}
        </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="mt-4 flex items-center justify-center gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={page <= 1}
            onClick={() => {
              const next = new URLSearchParams(searchParams);
              next.set("page", String(page - 1));
              setSearchParams(next);
            }}
          >
            Previous
          </Button>
          <span className="font-tabular text-sm text-[var(--color-text-secondary)]">
            {page} / {totalPages}
          </span>
          <Button
            variant="outline"
            size="sm"
            disabled={page >= totalPages}
            onClick={() => {
              const next = new URLSearchParams(searchParams);
              next.set("page", String(page + 1));
              setSearchParams(next);
            }}
          >
            Next
          </Button>
        </div>
      )}
    </div>
  );
}
