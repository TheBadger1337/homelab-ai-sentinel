import type { Severity, Lifecycle } from "../lib/types";

/* Design spec: pill pattern — dot + UPPERCASE eyebrow label, 999px radius.
   Left-border severity stripe is handled separately via .severity-bar-* CSS class. */

const severityConfig: Record<Severity, { bg: string; text: string; border: string }> = {
  critical: {
    bg: "var(--severity-critical-bg)",
    text: "var(--severity-critical)",
    border: "rgba(244, 63, 94, 0.3)",
  },
  warning: {
    bg: "var(--severity-warning-bg)",
    text: "var(--severity-warning)",
    border: "rgba(255, 140, 0, 0.3)",
  },
  info: {
    bg: "var(--severity-info-bg)",
    text: "var(--severity-info)",
    border: "rgba(0, 245, 255, 0.3)",
  },
  unknown: {
    bg: "var(--color-surface-raised)",
    text: "var(--color-text-muted)",
    border: "var(--color-border)",
  },
};

const lifecycleConfig: Record<Lifecycle, { bg: string; text: string; border: string }> = {
  emerging: {
    bg: "rgba(167, 139, 250, 0.12)",
    text: "var(--lifecycle-emerging)",
    border: "rgba(167, 139, 250, 0.3)",
  },
  active: {
    bg: "var(--severity-critical-bg)",
    text: "var(--lifecycle-active)",
    border: "rgba(244, 63, 94, 0.3)",
  },
  stabilizing: {
    bg: "var(--severity-warning-bg)",
    text: "var(--lifecycle-stabilizing)",
    border: "rgba(255, 140, 0, 0.3)",
  },
  resolved: {
    bg: "var(--severity-resolved-bg)",
    text: "var(--lifecycle-resolved)",
    border: "rgba(0, 200, 83, 0.3)",
  },
};

interface SeverityBadgeProps {
  severity: Severity;
  className?: string;
}

export function SeverityBadge({ severity, className = "" }: SeverityBadgeProps) {
  const cfg = severityConfig[severity] ?? severityConfig.unknown;
  return (
    <span
      className={`inline-flex items-center gap-1 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.08em] leading-snug ${className}`}
      style={{
        background: cfg.bg,
        color: cfg.text,
        border: `1px solid ${cfg.border}`,
        borderRadius: "999px",
      }}
    >
      <span
        className="inline-block h-[5px] w-[5px] rounded-full"
        style={{ background: "currentColor" }}
      />
      {severity}
    </span>
  );
}

interface LifecycleBadgeProps {
  lifecycle: Lifecycle;
  className?: string;
}

export function LifecycleBadge({ lifecycle, className = "" }: LifecycleBadgeProps) {
  const cfg = lifecycleConfig[lifecycle] ?? lifecycleConfig.resolved;
  return (
    <span
      className={`inline-flex items-center gap-1 px-2 py-0.5 text-[10px] font-semibold leading-snug ${className}`}
      style={{
        background: cfg.bg,
        color: cfg.text,
        border: `1px solid ${cfg.border}`,
        borderRadius: "999px",
        textTransform: "capitalize",
      }}
    >
      {lifecycle === "active" && (
        <span className="pulse-dot inline-block h-[5px] w-[5px] rounded-full bg-current" />
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
      className={`inline-flex items-center gap-1 px-2 py-0.5 text-[10px] font-semibold leading-snug ${className}`}
      style={{
        background: isResolved ? "var(--severity-resolved-bg)" : "var(--severity-critical-bg)",
        color: isResolved ? "var(--severity-resolved)" : "var(--severity-critical)",
        border: `1px solid ${isResolved ? "rgba(0,200,83,0.3)" : "rgba(244,63,94,0.3)"}`,
        borderRadius: "999px",
        textTransform: "capitalize",
      }}
    >
      {!isResolved && (
        <span className="pulse-dot inline-block h-[5px] w-[5px] rounded-full bg-current" />
      )}
      {status}
    </span>
  );
}
