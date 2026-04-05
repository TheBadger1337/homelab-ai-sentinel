import type { Severity, Lifecycle } from "../lib/types";

const severityStyles: Record<Severity, string> = {
  critical:
    "bg-[var(--severity-critical-bg)] text-[var(--severity-critical)] border-[var(--severity-critical)]",
  warning:
    "bg-[var(--severity-warning-bg)] text-[var(--severity-warning)] border-[var(--severity-warning)]",
  info:
    "bg-[var(--severity-info-bg)] text-[var(--severity-info)] border-[var(--severity-info)]",
  unknown:
    "bg-[var(--color-surface-raised)] text-[var(--color-text-muted)] border-[var(--color-border)]",
};

const lifecycleStyles: Record<Lifecycle, string> = {
  emerging:
    "bg-[rgba(139,92,246,0.12)] text-[var(--lifecycle-emerging)] border-[var(--lifecycle-emerging)]",
  active:
    "bg-[var(--severity-critical-bg)] text-[var(--lifecycle-active)] border-[var(--lifecycle-active)]",
  stabilizing:
    "bg-[var(--severity-warning-bg)] text-[var(--lifecycle-stabilizing)] border-[var(--lifecycle-stabilizing)]",
  resolved:
    "bg-[var(--severity-resolved-bg)] text-[var(--lifecycle-resolved)] border-[var(--lifecycle-resolved)]",
};

interface SeverityBadgeProps {
  severity: Severity;
  className?: string;
}

export function SeverityBadge({ severity, className = "" }: SeverityBadgeProps) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium uppercase tracking-wide ${severityStyles[severity] || severityStyles.unknown} ${className}`}
    >
      {severity}
    </span>
  );
}

interface LifecycleBadgeProps {
  lifecycle: Lifecycle;
  className?: string;
}

export function LifecycleBadge({ lifecycle, className = "" }: LifecycleBadgeProps) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium capitalize ${lifecycleStyles[lifecycle] || lifecycleStyles.resolved} ${className}`}
    >
      {lifecycle === "active" && (
        <span className="pulse-dot inline-block h-1.5 w-1.5 rounded-full bg-current" />
      )}
      {lifecycle}
    </span>
  );
}

interface StatusBadgeProps {
  status: "open" | "resolved" | string;
  className?: string;
}

export function StatusBadge({ status, className = "" }: StatusBadgeProps) {
  const isResolved = status === "resolved";
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium capitalize ${
        isResolved
          ? "bg-[var(--severity-resolved-bg)] text-[var(--severity-resolved)] border-[var(--severity-resolved)]"
          : "bg-[var(--severity-critical-bg)] text-[var(--severity-critical)] border-[var(--severity-critical)]"
      } ${className}`}
    >
      {!isResolved && (
        <span className="pulse-dot inline-block h-1.5 w-1.5 rounded-full bg-current" />
      )}
      {status}
    </span>
  );
}
