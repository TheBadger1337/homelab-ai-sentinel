import { useState, useEffect, useCallback } from "react";
import { Download } from "lucide-react";
import { Card } from "../components/Card";
import { Skeleton } from "../components/Skeleton";
import { getSettings } from "../lib/api";

/**
 * Settings page — read-only view of current configuration.
 * No secrets are exposed. Platform status shown per notification client.
 */
export function SettingsPage() {
  const [settings, setSettings] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchData = useCallback(async () => {
    try {
      const result = await getSettings();
      setSettings(result);
    } catch {
      // Auth redirect
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  if (loading) {
    return (
      <div>
        <h1 className="mb-6 text-lg font-semibold">Settings</h1>
        <div className="space-y-4">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-32 w-full" />
          ))}
        </div>
      </div>
    );
  }

  if (!settings) {
    return (
      <div className="py-12 text-center text-[var(--color-text-muted)]">
        Failed to load settings.
      </div>
    );
  }

  const platforms = (settings.platforms as Record<string, { status: string }>) ?? {};

  return (
    <div className="max-w-2xl">
      <h1 className="mb-6 text-lg font-semibold">Settings</h1>
      <p className="mb-6 text-sm text-[var(--color-text-muted)]">
        Read-only view of current configuration. Change settings via environment variables.
      </p>

      {/* General */}
      <Card className="mb-4 p-4">
        <h2 className="mb-3 text-sm font-medium text-[var(--color-text-secondary)]">
          General
        </h2>
        <dl className="grid grid-cols-2 gap-y-2 text-sm">
          <dt className="text-[var(--color-text-muted)]">Mode</dt>
          <dd className="font-medium">{settings.mode as string}</dd>

          <dt className="text-[var(--color-text-muted)]">Min Severity</dt>
          <dd className="font-medium">{settings.min_severity as string}</dd>

          <dt className="text-[var(--color-text-muted)]">AI Provider</dt>
          <dd className="font-medium">
            {settings.ai_provider === "openai" ? "OpenAI-compatible" : String(settings.ai_provider)}
          </dd>

          <dt className="text-[var(--color-text-muted)]">AI Concurrency</dt>
          <dd className="font-tabular font-medium">
            {(settings.ai_concurrency as number) || "unlimited"}
          </dd>
        </dl>
      </Card>

      {/* Alert Processing */}
      <Card className="mb-4 p-4">
        <h2 className="mb-3 text-sm font-medium text-[var(--color-text-secondary)]">
          Alert Processing
        </h2>
        <dl className="grid grid-cols-2 gap-y-2 text-sm">
          <dt className="text-[var(--color-text-muted)]">Dedup TTL</dt>
          <dd className="font-tabular font-medium">{settings.dedup_ttl as number}s</dd>

          <dt className="text-[var(--color-text-muted)]">Cooldown</dt>
          <dd className="font-tabular font-medium">{settings.cooldown as number}s</dd>

          <dt className="text-[var(--color-text-muted)]">Storm Window</dt>
          <dd className="font-tabular font-medium">
            {(settings.storm_window as number) || "disabled"}
          </dd>

          <dt className="text-[var(--color-text-muted)]">Storm Threshold</dt>
          <dd className="font-tabular font-medium">{settings.storm_threshold as number}</dd>

          <dt className="text-[var(--color-text-muted)]">Retention</dt>
          <dd className="font-tabular font-medium">{settings.retention_days as number} days</dd>
        </dl>
      </Card>

      {/* Feedback Export */}
      <Card className="mb-4 p-4">
        <h2 className="mb-1 text-sm font-medium text-[var(--color-text-secondary)]">
          Feedback Export
        </h2>
        <p className="mb-3 text-xs text-[var(--color-text-muted)]">
          Download operator-rated AI insights for offline analysis or local model fine-tuning.
        </p>
        <div className="flex flex-wrap gap-2">
          <a
            href="/api/feedback/export/jsonl"
            download="sentinel_feedback.jsonl"
            className="inline-flex items-center gap-1.5 rounded-md border border-[var(--color-border)] px-3 py-1.5 text-xs font-medium text-[var(--color-text)] hover:bg-[var(--color-surface-raised)] transition-colors"
          >
            <Download className="h-3.5 w-3.5" />
            JSONL (fine-tuning, up-rated)
          </a>
          <a
            href="/api/feedback/export"
            download="sentinel_feedback.json"
            className="inline-flex items-center gap-1.5 rounded-md border border-[var(--color-border)] px-3 py-1.5 text-xs font-medium text-[var(--color-text)] hover:bg-[var(--color-surface-raised)] transition-colors"
          >
            <Download className="h-3.5 w-3.5" />
            JSON (all feedback)
          </a>
        </div>
      </Card>

      {/* Notification Platforms */}
      <Card className="p-4">
        <h2 className="mb-3 text-sm font-medium text-[var(--color-text-secondary)]">
          Notification Platforms
        </h2>
        <div className="grid gap-2 sm:grid-cols-2">
          {Object.entries(platforms)
            .sort(([a], [b]) => a.localeCompare(b))
            .map(([name, config]) => {
              const color =
                config.status === "active"
                  ? "var(--severity-resolved)"
                  : config.status === "disabled"
                    ? "var(--severity-warning)"
                    : "var(--color-text-muted)";
              return (
                <div
                  key={name}
                  className="flex items-center gap-2 rounded-md border border-[var(--color-border)] px-3 py-2"
                >
                  <div
                    className="h-2 w-2 rounded-full"
                    style={{ backgroundColor: color }}
                  />
                  <span className="text-sm capitalize">{name}</span>
                  <span
                    className="ml-auto text-xs"
                    style={{ color }}
                  >
                    {config.status}
                  </span>
                </div>
              );
            })}
        </div>
      </Card>
    </div>
  );
}
