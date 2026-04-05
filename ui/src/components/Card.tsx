import type { ReactNode } from "react";

interface CardProps {
  children: ReactNode;
  className?: string;
  onClick?: () => void;
  severity?: string;
  glow?: boolean;
}

/**
 * Surface card with optional severity accent bar and atmospheric glow.
 * Per ui-ux-pro-max: elevated variant with border, 8px spacing scale.
 * Per frontend-design: atmospheric depth via radial gradient glow.
 */
export function Card({
  children,
  className = "",
  onClick,
  severity,
  glow,
}: CardProps) {
  const severityBar = severity ? `severity-bar-${severity}` : "";
  const glowClass = glow
    ? severity === "critical"
      ? "card-glow-critical"
      : "card-glow"
    : "";

  return (
    <div
      className={`relative rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] transition-colors duration-150 ${severityBar} ${glowClass} ${
        onClick
          ? "cursor-pointer active-scale hover:border-[var(--color-text-muted)]"
          : ""
      } ${className}`}
      onClick={onClick}
      role={onClick ? "button" : undefined}
      tabIndex={onClick ? 0 : undefined}
      onKeyDown={
        onClick
          ? (e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onClick();
              }
            }
          : undefined
      }
    >
      {children}
    </div>
  );
}

interface StatCardProps {
  label: string;
  value: string | number;
  icon?: ReactNode;
  severity?: string;
  className?: string;
}

/**
 * Dashboard stat card — label + large numeric value.
 * Tabular figures for numbers. Atmospheric glow on critical/warning.
 */
export function StatCard({
  label,
  value,
  icon,
  severity,
  className = "",
}: StatCardProps) {
  return (
    <Card severity={severity} glow={!!severity} className={`p-5 ${className}`}>
      <div className="flex items-start justify-between">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-widest text-[var(--color-text-muted)]">
            {label}
          </p>
          <p className="font-tabular mt-2 text-3xl font-bold tracking-tight">
            {value}
          </p>
        </div>
        {icon && (
          <div className="rounded-lg bg-[var(--color-surface-raised)] p-2 text-[var(--color-text-muted)]">
            {icon}
          </div>
        )}
      </div>
    </Card>
  );
}
