import { useState, useEffect, useCallback } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Trash2, AlertTriangle, ThumbsUp, ThumbsDown, Minus } from "lucide-react";
import { SeverityBadge } from "../components/Badge";
import { Button } from "../components/Button";
import { SkeletonTable } from "../components/Skeleton";
import { Card } from "../components/Card";
import {
  useRelativeTime,
  formatRelativeTime,
  formatTimestamp,
} from "../hooks/useRelativeTime";
import { getAlerts, deleteAlert, deleteAlerts, submitFeedback } from "../lib/api";

type FeedbackRating = "up" | "down" | "meh";

const RATING_ICONS: Record<FeedbackRating, { icon: React.ReactNode; label: string }> = {
  up:   { icon: <ThumbsUp  className="h-3.5 w-3.5" />, label: "Helpful" },
  down: { icon: <ThumbsDown className="h-3.5 w-3.5" />, label: "Not helpful" },
  meh:  { icon: <Minus     className="h-3.5 w-3.5" />, label: "Unsure" },
};

/**
 * Alerts list — paginated, filterable by service, with delete and AI feedback support.
 */
export function Alerts() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  useRelativeTime();

  const page = Number(searchParams.get("page")) || 1;
  const serviceFilter = searchParams.get("service") || "";

  const [data, setData] = useState<{
    alerts: Record<string, unknown>[];
    total: number;
    per_page: number;
  } | null>(null);
  const [loading, setLoading] = useState(true);
  const [deleting, setDeleting] = useState<number | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<
    null | "filtered" | "all"
  >(null);
  const [batchDeleting, setBatchDeleting] = useState(false);

  // Feedback state — expanded alert ID + per-alert submitted ratings
  const [expandedAlert, setExpandedAlert] = useState<number | null>(null);
  const [feedbackSubmitting, setFeedbackSubmitting] = useState<number | null>(null);
  const [submittedRatings, setSubmittedRatings] = useState<Record<number, FeedbackRating>>({});

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const result = await getAlerts({
        page,
        per_page: 50,
        service: serviceFilter,
      });
      setData(result);
    } catch {
      // Auth redirect handled by api client
    } finally {
      setLoading(false);
    }
  }, [page, serviceFilter]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const handleDeleteOne = async (id: number) => {
    setDeleting(id);
    try {
      await deleteAlert(id);
      fetchData();
    } catch {
      // Ignore
    } finally {
      setDeleting(null);
    }
  };

  const handleBatchDelete = async (mode: "filtered" | "all") => {
    setBatchDeleting(true);
    try {
      if (mode === "all") {
        await deleteAlerts({ all: true });
      } else {
        await deleteAlerts({ service: serviceFilter || undefined });
      }
      setConfirmDelete(null);
      fetchData();
    } catch {
      // Ignore
    } finally {
      setBatchDeleting(false);
    }
  };

  const handleFeedback = async (alertId: number, rating: FeedbackRating) => {
    setFeedbackSubmitting(alertId);
    try {
      await submitFeedback(alertId, rating);
      setSubmittedRatings((prev) => ({ ...prev, [alertId]: rating }));
    } catch {
      // Ignore — feedback is best-effort
    } finally {
      setFeedbackSubmitting(null);
    }
  };

  const totalPages = data ? Math.ceil(data.total / data.per_page) : 0;

  return (
    <div>
      <h1 className="mb-6 text-lg font-semibold">Alerts</h1>

      {/* Filters */}
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <input
          type="text"
          placeholder="Filter service..."
          value={serviceFilter}
          onChange={(e) => {
            const next = new URLSearchParams(searchParams);
            if (e.target.value) {
              next.set("service", e.target.value);
            } else {
              next.delete("service");
            }
            next.set("page", "1");
            setSearchParams(next);
          }}
          className="h-9 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)] focus:outline-none focus:ring-2 focus:ring-[var(--focus-ring)]"
          aria-label="Filter by service name"
        />

        {/* Delete buttons */}
        {(data?.total ?? 0) > 0 && (
          <>
            {serviceFilter && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => setConfirmDelete("filtered")}
              >
                <Trash2 className="h-3.5 w-3.5" />
                Delete filtered
              </Button>
            )}
            <Button
              variant="outline"
              size="sm"
              onClick={() => setConfirmDelete("all")}
            >
              <Trash2 className="h-3.5 w-3.5" />
              Delete all
            </Button>
          </>
        )}

        <span className="ml-auto text-xs text-[var(--color-text-muted)] font-tabular">
          {data?.total ?? 0} alert{(data?.total ?? 0) !== 1 ? "s" : ""}
        </span>
      </div>

      {/* Confirm dialog */}
      {confirmDelete && (
        <Card className="mb-4 border-[var(--severity-critical)]/30 p-4">
          <div className="flex items-start gap-3">
            <div className="mt-0.5 rounded-full bg-[var(--severity-critical-bg)] p-1.5">
              <AlertTriangle className="h-4 w-4 text-[var(--severity-critical)]" />
            </div>
            <div className="flex-1">
              <p className="text-sm font-medium">
                {confirmDelete === "all"
                  ? `Delete all ${data?.total ?? 0} alerts?`
                  : `Delete ${data?.total ?? 0} filtered alerts?`}
              </p>
              <p className="mt-1 text-xs text-[var(--color-text-muted)]">
                This action cannot be undone. Alert data will be permanently removed.
              </p>
              <div className="mt-3 flex gap-2">
                <Button
                  variant="destructive"
                  size="sm"
                  loading={batchDeleting}
                  onClick={() => handleBatchDelete(confirmDelete)}
                >
                  Delete
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setConfirmDelete(null)}
                  disabled={batchDeleting}
                >
                  Cancel
                </Button>
              </div>
            </div>
          </div>
        </Card>
      )}

      {/* Table */}
      {loading ? (
        <SkeletonTable rows={10} />
      ) : (
        <div className="overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]">
          {/* Header */}
          <div className="hidden border-b border-[var(--color-border)] bg-[var(--color-surface-raised)] px-4 py-2.5 text-xs font-medium uppercase tracking-wide text-[var(--color-text-muted)] md:grid md:grid-cols-[4rem_6rem_5rem_1fr_5rem_6rem_2.5rem]">
            <span>ID</span>
            <span>Service</span>
            <span>Source</span>
            <span>Message</span>
            <span>Severity</span>
            <span>Time</span>
            <span></span>
          </div>

          {!data?.alerts.length ? (
            <div className="px-4 py-8 text-center text-sm text-[var(--color-text-muted)]">
              No alerts match the current filters.
            </div>
          ) : (
            data.alerts.map((alert) => {
              const alertId = alert.id as number;
              const isExpanded = expandedAlert === alertId;
              const currentRating = submittedRatings[alertId];
              const insight = alert.insight as string | undefined;

              return (
                <div key={alertId} className={`border-b border-[var(--color-border)] severity-bar-${alert.severity as string}`}>
                  {/* Main row */}
                  <div
                    className="grid cursor-pointer items-center gap-2 px-4 py-3 transition-colors duration-100 hover:bg-[var(--color-surface-raised)] md:grid-cols-[4rem_6rem_5rem_1fr_5rem_6rem_2.5rem]"
                    onClick={() => setExpandedAlert(isExpanded ? null : alertId)}
                  >
                    <span className="font-tabular text-sm text-[var(--color-text-muted)]">
                      #{alertId}
                    </span>
                    <span
                      className={`truncate text-sm font-medium ${alert.incident_id ? "hover:text-[var(--color-primary)]" : ""}`}
                      onClick={
                        alert.incident_id
                          ? (e) => { e.stopPropagation(); navigate(`/incidents/${alert.incident_id}`); }
                          : undefined
                      }
                    >
                      {alert.service as string}
                    </span>
                    <span className="text-xs text-[var(--color-text-muted)]">
                      {alert.source as string}
                    </span>
                    <span className="truncate text-sm text-[var(--color-text-secondary)]">
                      {alert.message as string}
                    </span>
                    <SeverityBadge severity={alert.severity as "critical" | "warning" | "info" | "unknown"} />
                    <span
                      className="font-tabular text-xs text-[var(--color-text-muted)]"
                      title={formatTimestamp(alert.ts as number)}
                    >
                      {formatRelativeTime(alert.ts as number)}
                    </span>
                    <button
                      className="rounded p-1 text-[var(--color-text-muted)] hover:bg-[var(--severity-critical-bg)] hover:text-[var(--severity-critical)] transition-colors cursor-pointer disabled:opacity-30"
                      onClick={(e) => { e.stopPropagation(); handleDeleteOne(alertId); }}
                      disabled={deleting === alertId}
                      aria-label={`Delete alert ${alertId}`}
                      title="Delete alert"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>

                  {/* Expanded feedback panel */}
                  {isExpanded && (
                    <div className="border-t border-[var(--color-border)] bg-[var(--color-surface-raised)] px-4 py-3">
                      {insight ? (
                        <p className="mb-3 text-sm text-[var(--color-text-secondary)]">
                          <span className="font-medium text-[var(--color-text)]">AI insight: </span>
                          {insight}
                        </p>
                      ) : (
                        <p className="mb-3 text-xs text-[var(--color-text-muted)]">No AI insight recorded for this alert.</p>
                      )}

                      <div className="flex items-center gap-2">
                        <span className="text-xs text-[var(--color-text-muted)]">Was this insight helpful?</span>
                        {(["up", "down", "meh"] as FeedbackRating[]).map((r) => {
                          const { icon, label } = RATING_ICONS[r];
                          const isActive = currentRating === r;
                          return (
                            <button
                              key={r}
                              title={label}
                              aria-label={label}
                              disabled={feedbackSubmitting === alertId}
                              onClick={() => handleFeedback(alertId, r)}
                              className={`flex items-center gap-1 rounded px-2 py-1 text-xs transition-colors disabled:opacity-40 cursor-pointer
                                ${isActive
                                  ? "bg-[var(--color-primary)] text-white"
                                  : "border border-[var(--color-border)] text-[var(--color-text-muted)] hover:border-[var(--color-primary)] hover:text-[var(--color-primary)]"
                                }`}
                            >
                              {icon}
                              {label}
                            </button>
                          );
                        })}
                        {currentRating && (
                          <span className="ml-1 text-xs text-[var(--color-text-muted)]">Saved</span>
                        )}
                      </div>
                    </div>
                  )}
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
