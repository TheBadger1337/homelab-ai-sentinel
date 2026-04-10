import { useState, useCallback } from "react";
import { Play, X, CheckCircle, XCircle, Clock, Terminal, RefreshCw } from "lucide-react";
import { getActions, approveAction, rejectAction } from "../lib/api";
import type { PendingAction } from "../lib/types";

function statusBadge(status: PendingAction["status"]) {
  const map: Record<PendingAction["status"], { label: string; cls: string }> = {
    pending:   { label: "Pending",   cls: "bg-[var(--color-surface-raised)] text-[var(--color-text-muted)]" },
    running:   { label: "Running",   cls: "bg-blue-500/10 text-blue-400" },
    completed: { label: "Completed", cls: "bg-[var(--severity-info-bg)] text-[var(--severity-info)]" },
    failed:    { label: "Failed",    cls: "bg-[var(--severity-critical-bg)] text-[var(--severity-critical)]" },
    rejected:  { label: "Rejected",  cls: "bg-[var(--color-surface-raised)] text-[var(--color-text-muted)]" },
  };
  const { label, cls } = map[status] ?? { label: status, cls: "" };
  return (
    <span className={`inline-flex items-center rounded px-2 py-0.5 text-xs font-medium ${cls}`}>
      {label}
    </span>
  );
}

function ActionCard({
  action,
  onApprove,
  onReject,
}: {
  action: PendingAction;
  onApprove: (id: number) => Promise<void>;
  onReject: (id: number) => Promise<void>;
}) {
  const [loading, setLoading] = useState<"approve" | "reject" | null>(null);

  const handleApprove = async () => {
    setLoading("approve");
    try {
      await onApprove(action.id);
    } finally {
      setLoading(null);
    }
  };

  const handleReject = async () => {
    setLoading("reject");
    try {
      await onReject(action.id);
    } finally {
      setLoading(null);
    }
  };

  const ts = new Date(action.ts * 1000).toLocaleString();
  const tsCompleted = action.ts_completed
    ? new Date(action.ts_completed * 1000).toLocaleString()
    : null;

  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-mono text-sm font-semibold text-[var(--color-text)]">
              {action.action_name}
            </span>
            {statusBadge(action.status)}
          </div>
          {action.description && (
            <p className="mt-0.5 text-xs text-[var(--color-text-muted)]">{action.description}</p>
          )}
          <div className="mt-1 flex items-center gap-1.5 text-xs text-[var(--color-text-secondary)]">
            <Terminal className="h-3 w-3 shrink-0" />
            <code className="font-mono">{action.command.join(" ")}</code>
          </div>
          <p className="mt-1 text-xs text-[var(--color-text-muted)]">Queued: {ts}</p>
          {tsCompleted && (
            <p className="text-xs text-[var(--color-text-muted)]">Completed: {tsCompleted}</p>
          )}
        </div>

        {action.status === "pending" && (
          <div className="flex gap-2 shrink-0">
            <button
              onClick={handleReject}
              disabled={!!loading}
              className="flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium text-[var(--color-text-muted)] hover:bg-[var(--color-surface-raised)] disabled:opacity-50 cursor-pointer transition-colors"
            >
              {loading === "reject" ? (
                <RefreshCw className="h-3 w-3 animate-spin" />
              ) : (
                <X className="h-3 w-3" />
              )}
              Reject
            </button>
            <button
              onClick={handleApprove}
              disabled={!!loading}
              className="flex items-center gap-1.5 rounded-md bg-[var(--color-primary)] px-3 py-1.5 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50 cursor-pointer transition-opacity"
            >
              {loading === "approve" ? (
                <RefreshCw className="h-3 w-3 animate-spin" />
              ) : (
                <Play className="h-3 w-3" />
              )}
              Run
            </button>
          </div>
        )}
      </div>

      {/* Output */}
      {action.output && (
        <div className="mt-3 rounded-md bg-[var(--color-bg)] p-3">
          <div className="mb-1.5 flex items-center gap-2">
            {action.returncode === 0 ? (
              <CheckCircle className="h-3.5 w-3.5 text-[var(--severity-info)]" />
            ) : (
              <XCircle className="h-3.5 w-3.5 text-[var(--severity-critical)]" />
            )}
            <span className="text-xs font-medium text-[var(--color-text-muted)]">
              Exit {action.returncode}
            </span>
          </div>
          <pre className="whitespace-pre-wrap font-mono text-xs text-[var(--color-text-secondary)] max-h-40 overflow-y-auto">
            {action.output}
          </pre>
        </div>
      )}
    </div>
  );
}

export function Actions() {
  const [actions, setActions] = useState<PendingAction[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showRecent, setShowRecent] = useState(false);

  const fetchActions = useCallback(
    async (recent = showRecent) => {
      try {
        setError(null);
        const data = await getActions(recent);
        setActions(data.actions);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load actions");
      } finally {
        setLoading(false);
      }
    },
    [showRecent]
  );

  // Initial load
  useState(() => {
    fetchActions();
  });

  const handleApprove = useCallback(
    async (id: number) => {
      try {
        const result = await approveAction(id);
        // Update the action in state with the result
        setActions((prev) =>
          prev.map((a) =>
            a.id === id
              ? {
                  ...a,
                  status: result.status === "completed" ? "completed" : "failed",
                  output: result.output,
                  returncode: result.returncode,
                  ts_completed: Date.now() / 1000,
                }
              : a
          )
        );
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to run action");
      }
    },
    []
  );

  const handleReject = useCallback(async (id: number) => {
    try {
      await rejectAction(id);
      setActions((prev) =>
        prev.map((a) =>
          a.id === id ? { ...a, status: "rejected", ts_completed: Date.now() / 1000 } : a
        )
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to reject action");
    }
  }, []);

  const toggleRecent = () => {
    const next = !showRecent;
    setShowRecent(next);
    setLoading(true);
    fetchActions(next);
  };

  const pending = actions.filter((a) => a.status === "pending");
  const others = actions.filter((a) => a.status !== "pending");

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-[var(--color-text)]">Actions</h1>
          <p className="text-sm text-[var(--color-text-muted)]">
            Operator-approved scripts queued by incoming alerts
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={toggleRecent}
            className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors cursor-pointer ${
              showRecent
                ? "bg-[var(--color-primary-muted)] text-[var(--color-primary)]"
                : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-raised)]"
            }`}
          >
            {showRecent ? "Hiding" : "Show"} recent
          </button>
          <button
            onClick={() => { setLoading(true); fetchActions(); }}
            disabled={loading}
            className="flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium text-[var(--color-text-muted)] hover:bg-[var(--color-surface-raised)] disabled:opacity-50 cursor-pointer transition-colors"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </button>
        </div>
      </div>

      {error && (
        <div className="rounded-lg border border-[var(--severity-critical-bg)] bg-[var(--severity-critical-bg)] px-4 py-3 text-sm text-[var(--severity-critical)]">
          {error}
        </div>
      )}

      {loading ? (
        <div className="flex h-40 items-center justify-center">
          <div className="h-6 w-6 animate-spin rounded-full border-2 border-[var(--color-primary)] border-t-transparent" />
        </div>
      ) : (
        <>
          {/* Pending */}
          <section>
            <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold text-[var(--color-text)]">
              <Clock className="h-4 w-4 text-[var(--severity-warning)]" />
              Pending approval
              {pending.length > 0 && (
                <span className="ml-1 rounded-full bg-[var(--severity-warning-bg)] px-2 py-0.5 text-xs font-medium text-[var(--severity-warning)]">
                  {pending.length}
                </span>
              )}
            </h2>
            {pending.length === 0 ? (
              <div className="rounded-lg border border-dashed border-[var(--color-border)] py-10 text-center text-sm text-[var(--color-text-muted)]">
                No actions pending — define actions in{" "}
                <code className="font-mono text-xs">actions.yaml</code> inside your runbook directory
              </div>
            ) : (
              <div className="space-y-3">
                {pending.map((a) => (
                  <ActionCard
                    key={a.id}
                    action={a}
                    onApprove={handleApprove}
                    onReject={handleReject}
                  />
                ))}
              </div>
            )}
          </section>

          {/* Recent */}
          {showRecent && others.length > 0 && (
            <section>
              <h2 className="mb-3 text-sm font-semibold text-[var(--color-text)]">
                Recent (last 24 h)
              </h2>
              <div className="space-y-3">
                {others.map((a) => (
                  <ActionCard
                    key={a.id}
                    action={a}
                    onApprove={handleApprove}
                    onReject={handleReject}
                  />
                ))}
              </div>
            </section>
          )}
        </>
      )}
    </div>
  );
}
