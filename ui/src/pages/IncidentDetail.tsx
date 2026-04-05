import { useState, useEffect, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  ArrowLeft,
  CheckCircle,
  MessageSquare,
  Clock,
  Zap,
  Send,
} from "lucide-react";
import { SeverityBadge, LifecycleBadge, StatusBadge } from "../components/Badge";
import { Card } from "../components/Card";
import { Button } from "../components/Button";
import { Skeleton } from "../components/Skeleton";
import {
  useRelativeTime,
  formatRelativeTime,
  formatDuration,
  formatTimestamp,
} from "../hooks/useRelativeTime";
import { getIncident, resolveIncident, addNote } from "../lib/api";
import type { Severity, Lifecycle } from "../lib/types";

interface IncidentData {
  id: number;
  service: string;
  status: string;
  severity: Severity;
  lifecycle: Lifecycle;
  alert_count: number;
  ts_start: number;
  ts_end: number | null;
  root_cause: string | null;
  summary: string | null;
}

interface AlertData {
  id: number;
  ts: number;
  source: string;
  severity: Severity;
  message: string;
  insight: string | null;
  is_trigger: boolean;
}

interface NoteData {
  id: number;
  ts: number;
  content: string;
}

interface SimilarIncident {
  id: number;
  severity: Severity;
  summary: string | null;
  alert_count: number;
}

/**
 * Incident detail — timeline of linked alerts, AI analysis card,
 * operator notes, topology context, similar incidents.
 */
export function IncidentDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  useRelativeTime();

  const [data, setData] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(true);
  const [resolving, setResolving] = useState(false);
  const [noteText, setNoteText] = useState("");
  const [addingNote, setAddingNote] = useState(false);

  const fetchData = useCallback(async () => {
    if (!id) return;
    try {
      const result = await getIncident(Number(id));
      setData(result);
    } catch {
      // Auth redirect handled by api client
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const handleResolve = async () => {
    if (!id) return;
    setResolving(true);
    try {
      await resolveIncident(Number(id));
      fetchData();
    } catch {
      // Ignore
    } finally {
      setResolving(false);
    }
  };

  const handleAddNote = async () => {
    if (!id || !noteText.trim()) return;
    setAddingNote(true);
    try {
      await addNote(Number(id), noteText.trim());
      setNoteText("");
      fetchData();
    } catch {
      // Ignore
    } finally {
      setAddingNote(false);
    }
  };

  if (loading) {
    return (
      <div>
        <Skeleton className="mb-4 h-6 w-48" />
        <Skeleton className="mb-2 h-4 w-32" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }

  if (!data) {
    return (
      <div className="py-12 text-center text-[var(--color-text-muted)]">
        Incident not found.
      </div>
    );
  }

  const inc = data.incident as unknown as IncidentData;
  const alerts = ((data.alerts ?? []) as unknown) as AlertData[];
  const notes = ((data.notes ?? []) as unknown) as NoteData[];
  const similar = ((data.similar ?? []) as unknown) as SimilarIncident[];
  const isOpen = inc.status === "open";
  const tsStart = inc.ts_start as number;
  const tsEnd = inc.ts_end as number | null;
  const duration = tsEnd ? tsEnd - tsStart : Date.now() / 1000 - tsStart;

  return (
    <div className="max-w-4xl">
      {/* Back + header */}
      <button
        onClick={() => navigate(-1)}
        className="mb-4 flex items-center gap-1.5 text-sm text-[var(--color-text-secondary)] hover:text-[var(--color-text)] transition-colors cursor-pointer"
      >
        <ArrowLeft className="h-4 w-4" />
        Back
      </button>

      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-lg font-semibold">
              Incident #{String(inc.id)}
            </h1>
            <StatusBadge status={inc.status} />
            <LifecycleBadge lifecycle={inc.lifecycle} />
          </div>
          <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
            {inc.service}
            {" \u00b7 "}
            <SeverityBadge severity={inc.severity} />
            {" \u00b7 "}
            <span className="font-tabular">{inc.alert_count}</span> alerts
            {" \u00b7 "}
            <span title={formatTimestamp(tsStart)}>
              started {formatRelativeTime(tsStart)}
            </span>
            {tsEnd && ` \u00b7 lasted ${formatDuration(duration)}`}
            {!tsEnd && ` \u00b7 ongoing ${formatDuration(duration)}`}
          </p>
        </div>

        {isOpen && (
          <Button
            variant="outline"
            loading={resolving}
            onClick={handleResolve}
          >
            <CheckCircle className="h-4 w-4" />
            Resolve
          </Button>
        )}
      </div>

      {/* AI analysis card */}
      {(inc.root_cause || inc.summary) ? (
        <Card className="mb-6 p-4" severity={inc.severity}>
          <div className="mb-2 flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-[var(--color-text-muted)]">
            <Zap className="h-3.5 w-3.5" />
            AI Analysis
          </div>
          {inc.root_cause ? (
            <p className="text-sm leading-relaxed">{inc.root_cause}</p>
          ) : null}
          {inc.summary && inc.summary !== inc.root_cause ? (
            <p className="mt-2 text-sm leading-relaxed text-[var(--color-text-secondary)]">
              {inc.summary}
            </p>
          ) : null}
        </Card>
      ) : null}

      {/* Alert timeline */}
      <div className="mb-6">
        <h2 className="mb-3 flex items-center gap-2 text-sm font-medium text-[var(--color-text-secondary)]">
          <Clock className="h-4 w-4" />
          Alert Timeline ({alerts.length})
        </h2>
        <div className="space-y-0">
          {alerts.map((alert, idx) => (
            <div
              key={alert.id}
              className={`relative border-l-2 py-3 pl-6 animate-fade-up ${
                alert.is_trigger
                  ? "border-[var(--color-primary)]"
                  : "border-[var(--color-border)]"
              }`}
              style={{
                animationDelay: `${idx * 30}ms`,
              }}
            >
              {/* Timeline dot */}
              <div
                className={`absolute -left-[5px] top-4 h-2 w-2 rounded-full ${
                  alert.is_trigger
                    ? "bg-[var(--color-primary)]"
                    : "bg-[var(--color-border)]"
                }`}
              />
              <div className="flex flex-wrap items-center gap-2">
                <SeverityBadge severity={alert.severity} />
                {alert.is_trigger ? (
                  <span className="rounded bg-[var(--color-primary-muted)] px-1.5 py-0.5 text-xs font-medium text-[var(--color-primary)]">
                    TRIGGER
                  </span>
                ) : null}
                <span
                  className="font-tabular text-xs text-[var(--color-text-muted)]"
                  title={formatTimestamp(alert.ts)}
                >
                  {formatRelativeTime(alert.ts)}
                </span>
                <span className="text-xs text-[var(--color-text-muted)]">
                  {alert.source}
                </span>
              </div>
              <p className="mt-1 text-sm">{String(alert.message)}</p>
              {alert.insight ? (
                <p className="mt-1 text-xs text-[var(--color-text-secondary)] italic">
                  {alert.insight}
                </p>
              ) : null}
            </div>
          ))}
        </div>
      </div>

      {/* Operator notes */}
      <div className="mb-6">
        <h2 className="mb-3 flex items-center gap-2 text-sm font-medium text-[var(--color-text-secondary)]">
          <MessageSquare className="h-4 w-4" />
          Notes ({notes.length})
        </h2>

        {notes.length > 0 && (
          <div className="mb-3 space-y-2">
            {notes.map((note) => (
              <Card key={note.id} className="p-3">
                <p className="text-sm">{note.content}</p>
                <p
                  className="mt-1 text-xs text-[var(--color-text-muted)]"
                  title={formatTimestamp(note.ts)}
                >
                  {formatRelativeTime(note.ts)}
                </p>
              </Card>
            ))}
          </div>
        )}

        {/* Add note form */}
        <div className="flex gap-2">
          <input
            type="text"
            value={noteText}
            onChange={(e) => setNoteText(e.target.value)}
            placeholder="Add a note..."
            className="h-9 flex-1 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)] focus:outline-none focus:ring-2 focus:ring-[var(--focus-ring)]"
            maxLength={2000}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                handleAddNote();
              }
            }}
          />
          <Button
            variant="secondary"
            size="sm"
            loading={addingNote}
            disabled={!noteText.trim()}
            onClick={handleAddNote}
          >
            <Send className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>

      {/* Similar past incidents */}
      {similar.length > 0 && (
        <div>
          <h2 className="mb-3 text-sm font-medium text-[var(--color-text-secondary)]">
            Similar Past Incidents
          </h2>
          <div className="space-y-2">
            {similar.map((sim) => (
              <Card
                key={sim.id}
                className="flex cursor-pointer items-center gap-3 p-3"
                onClick={() => navigate(`/incidents/${sim.id}`)}
              >
                <span className="font-tabular text-xs text-[var(--color-text-muted)]">
                  #{sim.id}
                </span>
                <SeverityBadge severity={sim.severity} />
                <span className="min-w-0 flex-1 truncate text-sm">
                  {sim.summary || "No summary"}
                </span>
                <span className="font-tabular text-xs text-[var(--color-text-muted)]">
                  {sim.alert_count} alerts
                </span>
              </Card>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
