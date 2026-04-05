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
      <h1 className="mb-6 text-lg font-semibold">Incidents</h1>

      {/* Filters */}
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <select
          value={statusFilter}
          onChange={(e) => setFilter("status", e.target.value)}
          className="h-9 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 text-sm text-[var(--color-text)] focus:outline-none focus:ring-2 focus:ring-[var(--focus-ring)]"
          aria-label="Filter by status"
        >
          <option value="">All Status</option>
          <option value="open">Open</option>
          <option value="resolved">Resolved</option>
        </select>
        <select
          value={severityFilter}
          onChange={(e) => setFilter("severity", e.target.value)}
          className="h-9 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 text-sm text-[var(--color-text)] focus:outline-none focus:ring-2 focus:ring-[var(--focus-ring)]"
          aria-label="Filter by severity"
        >
          <option value="">All Severity</option>
          <option value="critical">Critical</option>
          <option value="warning">Warning</option>
          <option value="info">Info</option>
        </select>
        <input
          type="text"
          placeholder="Filter service..."
          value={serviceFilter}
          onChange={(e) => setFilter("service", e.target.value)}
          className="h-9 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)] focus:outline-none focus:ring-2 focus:ring-[var(--focus-ring)]"
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
        <span className="ml-auto text-xs text-[var(--color-text-muted)] font-tabular">
          {data?.total ?? 0} incident{(data?.total ?? 0) !== 1 ? "s" : ""}
        </span>
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
