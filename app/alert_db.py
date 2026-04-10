"""
SQLite alert log with WAL mode.

Provides append-only alert logging and recent-history queries used to
give the AI context about a service's behaviour over time.

Optional feature
================
The DB initializes when init_db() is called and the DB path is writable.
If initialization fails (no /data dir, permissions, etc.), all public
functions return safe defaults and the pipeline runs in stateless mode:
parse → threshold → dispatch, with no logging, incidents, dedup L2, or DLQ.
Call db_available() to check whether the DB is active.

Thread safety
=============
SQLite connections are per-thread (threading.local). Gunicorn gthread workers
share a process, so each thread gets its own connection. Multi-process workers
each have their own threading.local namespace. WAL mode allows concurrent
readers and serialises writers — fine for homelab alert volumes where writes
are rare and fast.

Failure policy
==============
Every public function catches all exceptions and logs at WARNING level.
A DB failure must never interrupt the alert processing pipeline.
"""

import json
import logging
import os
import sqlite3
import threading
import time

from .alert_parser import NormalizedAlert
from .utils import _env_int

logger = logging.getLogger(__name__)

_local = threading.local()
_db_initialized = False


def db_available() -> bool:
    """Return True if the DB was successfully initialized.

    All DB-dependent features check this before operating. When False,
    public functions return safe defaults so the pipeline keeps running
    in stateless mode (parse → dispatch, no logging/incidents/dedup L2).
    """
    return _db_initialized


def _db_path() -> str:
    return os.environ.get("DB_PATH", "/data/sentinel.db")


def _get_conn() -> sqlite3.Connection:
    """Return a per-thread SQLite connection, creating or reconnecting if needed."""
    path = _db_path()
    if (
        not hasattr(_local, "conn")
        or _local.conn is None
        or getattr(_local, "conn_path", None) != path
    ):
        if getattr(_local, "conn", None) is not None:
            try:
                _local.conn.close()
            except Exception:
                pass
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")  # retry writes for 5s before raising
        conn.row_factory = sqlite3.Row
        _local.conn = conn
        _local.conn_path = path
    return _local.conn


def init_db() -> None:
    """Create tables and indexes if they don't exist. Called at app startup.

    Uses a two-phase approach:
      1. CREATE TABLE IF NOT EXISTS for all base tables (idempotent, safe for fresh installs)
      2. _run_migrations() applies incremental DDL changes tracked by schema_version

    Existing v1.x deployments: schema_version table won't exist → version inferred
    as 1, and migrations from v2 onward are applied automatically.

    Set DB_DISABLED=true to explicitly skip initialization. All DB-dependent
    features (rate limiting, cooldown, escalation, dedup L2, DLQ, incidents,
    correlation, housekeeper, web UI) are automatically disabled.
    """
    if os.environ.get("DB_DISABLED", "").lower() == "true":
        logger.info("DB explicitly disabled (DB_DISABLED=true) — running in stateless mode")
        return

    try:
        conn = _get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                ts       REAL    NOT NULL,
                source   TEXT    NOT NULL,
                service  TEXT    NOT NULL,
                status   TEXT    NOT NULL,
                severity TEXT    NOT NULL,
                message  TEXT    NOT NULL,
                details  TEXT,
                insight  TEXT,
                actions  TEXT,
                notified INTEGER NOT NULL DEFAULT 1
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_service_ts ON alerts (service, ts)"
        )
        # Webhook rate limiter — one row per request, pruned on each check.
        # Shared across all Gunicorn workers via the same SQLite file.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rate_log (
                ts REAL NOT NULL
            )
        """)
        # Security audit log — auth failures, rate limit hits, injection detections.
        # Append-only; surfaced in /health for operator visibility.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS security_events (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                ts         REAL    NOT NULL,
                event_type TEXT    NOT NULL,
                detail     TEXT
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_security_ts ON security_events (event_type, ts)"
        )
        # Dedup L2 cache — shared across all Gunicorn workers.
        # L1 is in-memory per-worker (webhook.py _dedup_cache dict).
        # L2 catches cross-worker and post-restart duplicates.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS dedup_cache (
                key TEXT PRIMARY KEY,
                ts  REAL NOT NULL
            )
        """)
        # Dead letter queue — alerts that failed notification dispatch.
        # Retried by the housekeeper with exponential backoff.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS dead_letters (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                ts           REAL    NOT NULL,
                alert_json   TEXT    NOT NULL,
                ai_json      TEXT,
                retry_count  INTEGER NOT NULL DEFAULT 0,
                last_error   TEXT,
                next_retry_ts REAL   NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_dlq_retry ON dead_letters (next_retry_ts)"
        )
        # Schema version tracking — single row, updated by migrations.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER NOT NULL
            )
        """)
        conn.commit()

        # Apply incremental migrations
        _run_migrations(conn)

        global _db_initialized
        _db_initialized = True
        logger.info("Alert DB ready: %s (schema v%d)", _db_path(), _get_schema_version(conn))
    except Exception as exc:
        logger.warning(
            "Alert DB unavailable: %s — running in stateless mode "
            "(no logging, incidents, dedup L2, or DLQ)",
            type(exc).__name__,
        )


# ---------------------------------------------------------------------------
# Schema migrations
# ---------------------------------------------------------------------------

def _get_schema_version(conn: sqlite3.Connection) -> int:
    """Return the current schema version. Returns 1 if no version recorded."""
    try:
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        return row[0] if row else 1
    except Exception:
        return 1


def _set_schema_version(conn: sqlite3.Connection, version: int) -> None:
    """Set the schema version (single-row upsert)."""
    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """Check if a column exists on a table (safe for migration idempotency)."""
    cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(c[1] == column for c in cols)


def _migrate_v2(conn: sqlite3.Connection) -> None:
    """v2: Incident engine — incidents table, incident_id + is_trigger + event_id on alerts."""
    # Incidents table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS incidents (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_start    REAL    NOT NULL,
            ts_end      REAL,
            service     TEXT    NOT NULL,
            status      TEXT    NOT NULL DEFAULT 'open',
            severity    TEXT    NOT NULL,
            root_cause  TEXT,
            summary     TEXT,
            alert_count INTEGER NOT NULL DEFAULT 1,
            storm_id    INTEGER
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_incidents_service ON incidents (service, ts_start)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents (status)"
    )

    # Add columns to alerts — ALTERs are idempotent via _has_column check
    if not _has_column(conn, "alerts", "incident_id"):
        conn.execute("ALTER TABLE alerts ADD COLUMN incident_id INTEGER REFERENCES incidents(id)")
    if not _has_column(conn, "alerts", "is_trigger"):
        conn.execute("ALTER TABLE alerts ADD COLUMN is_trigger INTEGER NOT NULL DEFAULT 0")
    if not _has_column(conn, "alerts", "event_id"):
        conn.execute("ALTER TABLE alerts ADD COLUMN event_id TEXT")

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_alerts_incident ON alerts (incident_id)"
    )


def _migrate_v3(conn: sqlite3.Connection) -> None:
    """v3: Incident notes table for operator annotations."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS incident_notes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            incident_id INTEGER NOT NULL REFERENCES incidents(id),
            ts          REAL    NOT NULL,
            content     TEXT    NOT NULL
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_notes_incident ON incident_notes (incident_id)"
    )


def _migrate_v4(conn: sqlite3.Connection) -> None:
    """v4: UI config table for browser-configured settings (password, etc.)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ui_config (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)


def _migrate_v5(conn: sqlite3.Connection) -> None:
    """v5: Persistent storm buffer — survives worker recycling and restarts."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS storm_buffer (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          REAL    NOT NULL,
            alert_json  TEXT    NOT NULL,
            pulse_json  TEXT,
            runbook     TEXT,
            topology    TEXT
        )
    """)


def _migrate_v6(conn: sqlite3.Connection) -> None:
    """v6: Morning Brief log — tracks daily brief sends to prevent double-dispatch."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS morning_briefs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          REAL    NOT NULL,
            date_sent   TEXT    NOT NULL UNIQUE,
            alert_count INTEGER NOT NULL DEFAULT 0,
            insight     TEXT
        )
    """)


def _migrate_v7(conn: sqlite3.Connection) -> None:
    """v7: Alert feedback — operator ratings on AI insights (one per alert)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS alert_feedback (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_id INTEGER NOT NULL UNIQUE REFERENCES alerts(id) ON DELETE CASCADE,
            ts       REAL    NOT NULL,
            rating   TEXT    NOT NULL,
            comment  TEXT
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_feedback_alert ON alert_feedback (alert_id)"
    )


# Migration registry — index is the target version number.
# Entry 0 and 1 are None (v1 is the base schema created by init_db).
_MIGRATIONS: list = [
    None,   # v0 — placeholder
    None,   # v1 — base schema (CREATE TABLE IF NOT EXISTS in init_db)
    _migrate_v2,  # v2 — incident engine
    _migrate_v3,  # v3 — incident notes table
    _migrate_v4,  # v4 — UI config table (first-run password setup)
    _migrate_v5,  # v5 — persistent storm buffer
    _migrate_v6,  # v6 — morning brief log
    _migrate_v7,  # v7 — alert feedback
]


def _run_migrations(conn: sqlite3.Connection) -> None:
    """Apply pending migrations from current version to latest."""
    current = _get_schema_version(conn)
    target = len(_MIGRATIONS) - 1

    if current >= target:
        return

    for version in range(current + 1, target + 1):
        migrate_fn = _MIGRATIONS[version]
        if migrate_fn is None:
            continue
        logger.info("Applying migration v%d", version)
        try:
            migrate_fn(conn)
            _set_schema_version(conn, version)
            conn.commit()
            logger.info("Migration v%d applied successfully", version)
        except Exception as exc:
            logger.warning("Migration v%d failed: %s", version, type(exc).__name__)
            conn.rollback()
            return  # stop applying further migrations


def log_alert(alert: NormalizedAlert, ai_result: dict | None, notified: bool) -> None:
    """
    Append an alert record to the database.

    ``notified=False`` when the alert was suppressed by a severity threshold —
    it is still logged so history reflects the true alert rate for the service.
    Deduplicated alerts are not logged (they are exact repeats already in the DB).
    """
    if not _db_initialized:
        return
    try:
        conn = _get_conn()
        conn.execute(
            """
            INSERT INTO alerts
                (ts, source, service, status, severity, message,
                 details, insight, actions, notified)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                time.time(),
                alert.source,
                alert.service_name,
                alert.status,
                alert.severity,
                alert.message,
                json.dumps(alert.details) if alert.details else None,
                ai_result.get("insight") if ai_result else None,
                json.dumps(ai_result.get("suggested_actions", [])) if ai_result else None,
                1 if notified else 0,
            ),
        )
        conn.commit()
    except Exception as exc:
        logger.warning("Failed to log alert to DB: %s", type(exc).__name__)


def check_and_record_rate(limit: int, window: int) -> bool:
    """
    Return True if the webhook rate limit is exceeded (caller should return 429).

    Prunes requests older than ``window`` seconds, counts the remainder, and
    records this request if under the limit. Uses BEGIN IMMEDIATE to serialize
    the check-and-insert within a single write transaction — prevents two
    concurrent workers from both reading count=N and both inserting.

    Fails open: returns False on any DB error so a DB failure never blocks
    legitimate requests.
    """
    if not _db_initialized:
        return False
    try:
        conn = _get_conn()
        now = time.time()
        cutoff = now - window
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM rate_log WHERE ts < ?", (cutoff,))
        count = conn.execute("SELECT COUNT(*) FROM rate_log").fetchone()[0]
        if count < limit:
            conn.execute("INSERT INTO rate_log (ts) VALUES (?)", (now,))
        conn.commit()
        return count >= limit
    except Exception as exc:
        logger.warning("Rate log check failed: %s", type(exc).__name__)
        try:
            conn.rollback()
        except Exception:
            pass
        return False  # fail open — never block requests on DB error


def log_security_event(event_type: str, detail: str = "") -> None:
    """
    Append a security event record (auth failure, rate limit hit, injection detected).
    Fails silently — security logging must never interrupt request processing.
    """
    if not _db_initialized:
        return
    try:
        conn = _get_conn()
        conn.execute(
            "INSERT INTO security_events (ts, event_type, detail) VALUES (?, ?, ?)",
            (time.time(), event_type, detail[:500]),  # cap detail length
        )
        conn.commit()
    except Exception as exc:
        logger.warning("Failed to log security event: %s", type(exc).__name__)


def get_security_summary(hours: int = 24) -> dict[str, int]:
    """
    Return counts of each security event type in the last N hours.
    Used by /health to surface attack patterns without raw log grepping.
    Returns an empty dict on DB error.
    """
    if not _db_initialized:
        return {}
    try:
        conn = _get_conn()
        cutoff = time.time() - hours * 3600
        rows = conn.execute(
            """
            SELECT event_type, COUNT(*) as count
            FROM security_events
            WHERE ts >= ?
            GROUP BY event_type
            """,
            (cutoff,),
        ).fetchall()
        return {r["event_type"]: r["count"] for r in rows}
    except Exception as exc:
        logger.warning("Security summary query failed: %s", type(exc).__name__)
        return {}


def get_db_stats() -> dict:
    """Return summary stats for the /health endpoint. Returns None values on DB error."""
    if not _db_initialized:
        return {"total_alerts": None, "notified_count": None, "last_alert_ts": None}
    try:
        conn = _get_conn()
        total = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
        notified = conn.execute("SELECT COUNT(*) FROM alerts WHERE notified=1").fetchone()[0]
        last_ts = conn.execute("SELECT MAX(ts) FROM alerts").fetchone()[0]
        return {"total_alerts": total, "notified_count": notified, "last_alert_ts": last_ts}
    except Exception as exc:
        logger.warning("DB stats query failed: %s", type(exc).__name__)
        return {"total_alerts": None, "notified_count": None, "last_alert_ts": None}


def get_last_notified_ts(service: str) -> float | None:
    """
    Return the timestamp of the most recent notified alert for a service,
    or None if no notified alerts exist. Used by the per-service cooldown check.
    Returns None on any DB error (fails open).
    """
    if not _db_initialized:
        return None
    try:
        conn = _get_conn()
        row = conn.execute(
            "SELECT MAX(ts) FROM alerts WHERE service = ? AND notified = 1",
            (service,),
        ).fetchone()
        return row[0] if row and row[0] is not None else None
    except Exception as exc:
        logger.warning("Cooldown query failed: %s", type(exc).__name__)
        return None


def get_outage_window(service: str) -> list[dict]:
    """
    Return all alerts for a service since the last recovery (status containing
    'up', 'ok', or 'resolved'). Used by resolution verification to summarize
    the outage. Returns most recent first.
    Returns an empty list on DB error.
    """
    if not _db_initialized:
        return []
    try:
        conn = _get_conn()
        # Find the timestamp of the last recovery before this one
        last_recovery = conn.execute(
            """
            SELECT MAX(ts) FROM alerts
            WHERE service = ? AND LOWER(status) IN ('up', 'ok', 'resolved')
            """,
            (service,),
        ).fetchone()
        cutoff = last_recovery[0] if last_recovery and last_recovery[0] else 0

        rows = conn.execute(
            """
            SELECT ts, status, severity, message
            FROM alerts
            WHERE service = ? AND ts > ?
            ORDER BY ts DESC
            LIMIT 20
            """,
            (service, cutoff),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.warning("Outage window query failed: %s", type(exc).__name__)
        return []


# ---------------------------------------------------------------------------
# Dedup L2 cache — SQLite-backed cross-worker dedup
# ---------------------------------------------------------------------------


def check_dedup_l2(key: str, ttl: int) -> bool:
    """
    Return True if the key exists in the L2 dedup cache within the TTL window.

    Does NOT record the key — call record_dedup_l2() after confirming this is
    not a duplicate (to avoid recording a key that was already in L1).
    Fails open: returns False on DB error so a DB failure never blocks alerts.
    """
    if not _db_initialized or ttl <= 0:
        return False
    try:
        conn = _get_conn()
        cutoff = time.time() - ttl
        row = conn.execute(
            "SELECT 1 FROM dedup_cache WHERE key = ? AND ts >= ?",
            (key, cutoff),
        ).fetchone()
        return row is not None
    except Exception as exc:
        logger.warning("Dedup L2 check failed: %s", type(exc).__name__)
        return False


def record_dedup_l2(key: str) -> None:
    """
    Record a dedup key in the L2 cache (write-through from L1).

    Uses INSERT OR REPLACE to update the timestamp if the key already exists.
    Wrapped in BEGIN IMMEDIATE to serialize against concurrent check_dedup_l2
    calls from other workers — prevents two workers from both passing the
    check and both recording.

    Fails silently — dedup is best-effort, not a correctness guarantee.
    """
    if not _db_initialized:
        return
    try:
        conn = _get_conn()
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT OR REPLACE INTO dedup_cache (key, ts) VALUES (?, ?)",
            (key, time.time()),
        )
        conn.commit()
    except Exception as exc:
        logger.warning("Dedup L2 record failed: %s", type(exc).__name__)
        try:
            conn.rollback()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Dead letter queue — failed notification dispatch retry
# ---------------------------------------------------------------------------

_DLQ_BACKOFF_BASE = 300  # 5 minutes base backoff


def enqueue_dead_letter(
    alert: NormalizedAlert,
    ai_result: dict | None,
    error: str,
) -> None:
    """
    Enqueue a failed alert for retry.

    Called when all notification platforms fail during dispatch.
    Fails silently — DLQ errors must never interrupt the pipeline.
    """
    if not _db_initialized:
        return
    try:
        conn = _get_conn()
        now = time.time()
        alert_data = {
            "source": alert.source,
            "status": alert.status,
            "severity": alert.severity,
            "service_name": alert.service_name,
            "message": alert.message,
            "details": alert.details,
        }
        conn.execute(
            """
            INSERT INTO dead_letters
                (ts, alert_json, ai_json, retry_count, last_error, next_retry_ts)
            VALUES (?, ?, ?, 0, ?, ?)
            """,
            (
                now,
                json.dumps(alert_data),
                json.dumps(ai_result) if ai_result else None,
                error[:500],
                now + _DLQ_BACKOFF_BASE,  # first retry in 5 minutes
            ),
        )
        conn.commit()
        logger.info("Alert enqueued in DLQ: service=%s error=%s", alert.service_name, error[:100])
    except Exception as exc:
        logger.warning("Failed to enqueue dead letter: %s", type(exc).__name__)


def get_pending_dead_letters(max_retries: int = 3) -> list[dict]:
    """
    Return dead letters that are due for retry.

    Only returns items where retry_count < max_retries and
    next_retry_ts <= now. Returns an empty list on DB error.
    """
    if not _db_initialized:
        return []
    try:
        conn = _get_conn()
        now = time.time()
        rows = conn.execute(
            """
            SELECT id, alert_json, ai_json, retry_count
            FROM dead_letters
            WHERE retry_count < ? AND next_retry_ts <= ?
            ORDER BY ts ASC
            LIMIT 50
            """,
            (max_retries, now),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.warning("DLQ query failed: %s", type(exc).__name__)
        return []


def mark_dead_letter_done(dlq_id: int) -> None:
    """Remove a successfully retried dead letter."""
    if not _db_initialized:
        return
    try:
        conn = _get_conn()
        conn.execute("DELETE FROM dead_letters WHERE id = ?", (dlq_id,))
        conn.commit()
    except Exception as exc:
        logger.warning("DLQ mark-done failed: %s", type(exc).__name__)


def mark_dead_letter_failed(dlq_id: int, error: str) -> None:
    """Increment retry count and set next retry time with exponential backoff."""
    if not _db_initialized:
        return
    try:
        conn = _get_conn()
        now = time.time()
        row = conn.execute(
            "SELECT retry_count FROM dead_letters WHERE id = ?", (dlq_id,),
        ).fetchone()
        if row is None:
            return
        next_count = row["retry_count"] + 1
        backoff = _DLQ_BACKOFF_BASE * (3 ** next_count)  # 5m, 15m, 45m
        conn.execute(
            """
            UPDATE dead_letters
            SET retry_count = ?, last_error = ?, next_retry_ts = ?
            WHERE id = ?
            """,
            (next_count, error[:500], now + backoff, dlq_id),
        )
        conn.commit()
    except Exception as exc:
        logger.warning("DLQ mark-failed failed: %s", type(exc).__name__)


def get_dlq_count() -> int:
    """Return the number of items in the dead letter queue. Used by /health."""
    if not _db_initialized:
        return 0
    try:
        conn = _get_conn()
        return conn.execute("SELECT COUNT(*) FROM dead_letters").fetchone()[0]
    except Exception as exc:
        logger.warning("get_dlq_count failed: %s", type(exc).__name__)
        return 0


# ---------------------------------------------------------------------------
# Incidents — grouped alert lifecycle
# ---------------------------------------------------------------------------


def create_incident(
    service: str,
    severity: str,
    alert_id: int | None = None,
    storm_id: int | None = None,
) -> int | None:
    """
    Create a new incident for a service and optionally link the trigger alert.

    Returns the new incident ID, or None on DB error.
    """
    if not _db_initialized:
        return None
    try:
        conn = _get_conn()
        now = time.time()
        cursor = conn.execute(
            """
            INSERT INTO incidents (ts_start, service, status, severity, alert_count, storm_id)
            VALUES (?, ?, 'open', ?, 1, ?)
            """,
            (now, service, severity, storm_id),
        )
        incident_id = cursor.lastrowid
        # Link the trigger alert and mark it as the first cause
        if alert_id is not None and incident_id is not None:
            conn.execute(
                "UPDATE alerts SET incident_id = ?, is_trigger = 1 WHERE id = ?",
                (incident_id, alert_id),
            )
        conn.commit()
        logger.info("Incident created: id=%d service=%s severity=%s", incident_id, service, severity)
        return incident_id
    except Exception as exc:
        logger.warning("Failed to create incident: %s", type(exc).__name__)
        return None


def get_open_incident(service: str, exclude_storm: bool = False) -> dict | None:
    """
    Return the most recent open incident for a service, or None.

    Used by the webhook to decide whether to create a new incident or
    link the alert to an existing one.

    When exclude_storm=True, storm incidents (storm_id IS NOT NULL) are
    skipped. A single-service recovery should not resolve a multi-service
    storm incident — the storm resolves when all constituent services recover.
    """
    if not _db_initialized:
        return None
    try:
        conn = _get_conn()
        storm_filter = " AND storm_id IS NULL" if exclude_storm else ""
        row = conn.execute(
            f"""
            SELECT id, ts_start, service, status, severity, alert_count, storm_id
            FROM incidents
            WHERE service = ? AND status = 'open'{storm_filter}
            ORDER BY ts_start DESC
            LIMIT 1
            """,
            (service,),
        ).fetchone()
        return dict(row) if row else None
    except Exception as exc:
        logger.warning("Failed to query open incident: %s", type(exc).__name__)
        return None


def link_alert_to_incident(alert_id: int, incident_id: int) -> None:
    """
    Link an alert to an existing incident and increment the alert count.

    Called when a subsequent alert fires for a service with an open incident.
    """
    if not _db_initialized:
        return
    try:
        conn = _get_conn()
        conn.execute(
            "UPDATE alerts SET incident_id = ? WHERE id = ?",
            (incident_id, alert_id),
        )
        conn.execute(
            "UPDATE incidents SET alert_count = alert_count + 1 WHERE id = ?",
            (incident_id,),
        )
        conn.commit()
    except Exception as exc:
        logger.warning("Failed to link alert to incident: %s", type(exc).__name__)


def resolve_incident(
    incident_id: int,
    summary: str | None = None,
    root_cause: str | None = None,
) -> bool:
    """
    Resolve an open incident — set ts_end, status='resolved', and optional AI summary.

    Called when a recovery alert arrives for a service with an open incident.
    Returns True if the incident was actually resolved (transitioned from open
    to resolved). Returns False if already resolved or on error — callers can
    use this to skip redundant AI resolution calls.
    """
    if not _db_initialized:
        return False
    try:
        conn = _get_conn()
        now = time.time()
        cursor = conn.execute(
            """
            UPDATE incidents
            SET status = 'resolved', ts_end = ?, summary = ?, root_cause = ?
            WHERE id = ? AND status = 'open'
            """,
            (now, summary[:2000] if summary else None,
             root_cause[:2000] if root_cause else None,
             incident_id),
        )
        conn.commit()
        if cursor.rowcount > 0:
            logger.info("Incident resolved: id=%d", incident_id)
            return True
        logger.debug("Incident already resolved: id=%d", incident_id)
        return False
    except Exception as exc:
        logger.warning("Failed to resolve incident: %s", type(exc).__name__)
        return False


def get_all_open_incidents() -> list[dict]:
    """
    Return all open incidents. Used by correlation engine to find
    upstream incidents for dependency-based linking.
    Returns empty list on DB error.
    """
    if not _db_initialized:
        return []
    try:
        conn = _get_conn()
        rows = conn.execute(
            """
            SELECT id, ts_start, service, status, severity, alert_count, storm_id
            FROM incidents
            WHERE status = 'open'
            ORDER BY ts_start DESC
            """,
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.warning("Failed to query open incidents: %s", type(exc).__name__)
        return []


def get_incident(incident_id: int) -> dict | None:
    """Return a single incident by ID, or None."""
    if not _db_initialized:
        return None
    try:
        conn = _get_conn()
        row = conn.execute(
            "SELECT * FROM incidents WHERE id = ?", (incident_id,),
        ).fetchone()
        return dict(row) if row else None
    except Exception as exc:
        logger.warning("Failed to get incident: %s", type(exc).__name__)
        return None


def log_alert_returning_id(alert: NormalizedAlert, ai_result: dict | None, notified: bool) -> int | None:
    """
    Like log_alert() but returns the inserted row ID for incident linking.

    Returns None on DB error — callers must handle gracefully.
    """
    if not _db_initialized:
        return None
    try:
        conn = _get_conn()
        cursor = conn.execute(
            """
            INSERT INTO alerts
                (ts, source, service, status, severity, message,
                 details, insight, actions, notified)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                time.time(),
                alert.source,
                alert.service_name,
                alert.status,
                alert.severity,
                alert.message,
                json.dumps(alert.details) if alert.details else None,
                ai_result.get("insight") if ai_result else None,
                json.dumps(ai_result.get("suggested_actions", [])) if ai_result else None,
                1 if notified else 0,
            ),
        )
        conn.commit()
        return cursor.lastrowid
    except Exception as exc:
        logger.warning("Failed to log alert to DB: %s", type(exc).__name__)
        return None


def close_thread_conn() -> None:
    """
    Close the current thread's DB connection if open.

    Called by background threads (storm flush, watchdog) before they exit
    to prevent connection leaks. Long-lived threads (Gunicorn workers) keep
    their connections open for the process lifetime.
    """
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
        _local.conn = None


def get_recent_alerts(service: str) -> list[dict]:
    """
    Return recent alerts for a service for AI context.

    Uses ALERT_HISTORY_HOURS if > 0 (time window), otherwise falls back to
    ALERT_HISTORY_LIMIT most-recent records (default 5).
    Returns an empty list on any DB error — callers treat no history as
    graceful degradation, not a failure.
    """
    if not _db_initialized:
        return []
    try:
        conn = _get_conn()
        history_hours = _env_int("ALERT_HISTORY_HOURS", 0)
        if history_hours > 0:
            cutoff = time.time() - (history_hours * 3600)
            rows = conn.execute(
                """
                SELECT ts, status, severity, message
                FROM alerts
                WHERE service = ? AND ts >= ?
                ORDER BY ts DESC
                """,
                (service, cutoff),
            ).fetchall()
        else:
            limit = _env_int("ALERT_HISTORY_LIMIT", 5)
            rows = conn.execute(
                """
                SELECT ts, status, severity, message
                FROM alerts
                WHERE service = ?
                ORDER BY ts DESC
                LIMIT ?
                """,
                (service, limit),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.warning("Failed to query alert history: %s", type(exc).__name__)
        return []


# ---------------------------------------------------------------------------
# UI config — key/value store for browser-configured settings
# ---------------------------------------------------------------------------

def get_ui_config(key: str) -> str | None:
    """Read a value from ui_config. Returns None if not set or DB unavailable."""
    if not _db_initialized:
        return None
    try:
        conn = _get_conn()
        row = conn.execute(
            "SELECT value FROM ui_config WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else None
    except Exception as exc:
        logger.warning("get_ui_config key=%s failed: %s", key, type(exc).__name__)
        return None


def set_ui_config(key: str, value: str) -> bool:
    """Write a value to ui_config. Returns True on success."""
    if not _db_initialized:
        return False
    try:
        conn = _get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO ui_config (key, value) VALUES (?, ?)",
            (key, value),
        )
        conn.commit()
        return True
    except Exception as exc:
        logger.warning("Failed to write ui_config key=%s: %s", key, type(exc).__name__)
        return False


# ---------------------------------------------------------------------------
# Storm buffer persistence — survives worker recycling / container restart
# ---------------------------------------------------------------------------

def persist_storm_entry(alert_json: str, pulse_json: str | None, runbook: str, topology: str) -> int | None:
    """Save a buffered alert to the storm_buffer table. Returns row ID or None."""
    if not _db_initialized:
        return None
    try:
        conn = _get_conn()
        cursor = conn.execute(
            "INSERT INTO storm_buffer (ts, alert_json, pulse_json, runbook, topology) VALUES (?, ?, ?, ?, ?)",
            (time.time(), alert_json, pulse_json, runbook, topology),
        )
        conn.commit()
        return cursor.lastrowid
    except Exception as exc:
        logger.warning("Failed to persist storm entry: %s", type(exc).__name__)
        return None


def load_storm_entries() -> list[dict]:
    """Load all pending storm buffer entries from DB. Returns list of row dicts."""
    if not _db_initialized:
        return []
    try:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT id, ts, alert_json, pulse_json, runbook, topology FROM storm_buffer ORDER BY ts ASC"
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.warning("Failed to load storm entries: %s", type(exc).__name__)
        return []


def clear_storm_buffer(row_ids: list[int] | None = None) -> None:
    """Delete processed entries from the storm_buffer table.

    If row_ids is None, clears ALL entries. Otherwise deletes only the specified rows.
    """
    if not _db_initialized:
        return
    try:
        conn = _get_conn()
        if row_ids is None:
            conn.execute("DELETE FROM storm_buffer")
        else:
            placeholders = ",".join("?" for _ in row_ids)
            conn.execute(f"DELETE FROM storm_buffer WHERE id IN ({placeholders})", row_ids)
        conn.commit()
    except Exception as exc:
        logger.warning("Failed to clear storm buffer: %s", type(exc).__name__)


# ---------------------------------------------------------------------------
# Morning Brief — daily digest of quiet-hours activity
# ---------------------------------------------------------------------------

def get_alerts_in_window(ts_start: float, ts_end: float) -> list[dict]:
    """Return all alerts fired between ts_start and ts_end (Unix timestamps).

    Returns empty list on DB error or when DB is unavailable.
    """
    if not _db_initialized:
        return []
    try:
        conn = _get_conn()
        rows = conn.execute(
            """
            SELECT ts, source, service, status, severity, message, insight
            FROM alerts
            WHERE ts >= ? AND ts < ?
            ORDER BY ts ASC
            """,
            (ts_start, ts_end),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.warning("get_alerts_in_window failed: %s", type(exc).__name__)
        return []


def has_sent_brief_today(date_str: str) -> bool:
    """Return True if a morning brief was already sent for date_str (YYYY-MM-DD)."""
    if not _db_initialized:
        return False
    try:
        conn = _get_conn()
        row = conn.execute(
            "SELECT 1 FROM morning_briefs WHERE date_sent = ?", (date_str,)
        ).fetchone()
        return row is not None
    except Exception as exc:
        logger.warning("has_sent_brief_today failed: %s", type(exc).__name__)
        return False  # fail open — allow send on DB error


def record_brief_sent(date_str: str, alert_count: int, insight: str | None) -> None:
    """Record a successfully dispatched morning brief. Fails silently."""
    if not _db_initialized:
        return
    try:
        conn = _get_conn()
        conn.execute(
            "INSERT OR IGNORE INTO morning_briefs (ts, date_sent, alert_count, insight) VALUES (?, ?, ?, ?)",
            (time.time(), date_str, alert_count, insight),
        )
        conn.commit()
    except Exception as exc:
        logger.warning("record_brief_sent failed: %s", type(exc).__name__)


# ---------------------------------------------------------------------------
# Alert feedback — operator ratings on AI insights
# ---------------------------------------------------------------------------

_VALID_RATINGS = frozenset({"up", "down", "meh"})


def add_feedback(alert_id: int, rating: str, comment: str | None) -> bool:
    """
    Store or update operator feedback for an alert. Returns True on success.

    Uses INSERT OR REPLACE so rating updates are idempotent. One feedback
    record per alert — subsequent calls overwrite the previous rating.
    Rating must be one of: up, down, meh.
    """
    if not _db_initialized:
        return False
    if rating not in _VALID_RATINGS:
        return False
    try:
        conn = _get_conn()
        conn.execute(
            """
            INSERT OR REPLACE INTO alert_feedback (alert_id, ts, rating, comment)
            VALUES (?, ?, ?, ?)
            """,
            (alert_id, time.time(), rating, comment[:500] if comment else None),
        )
        conn.commit()
        return True
    except Exception as exc:
        logger.warning("add_feedback failed: %s", type(exc).__name__)
        return False


def get_feedback_for_alert(alert_id: int) -> dict | None:
    """Return feedback for an alert, or None if no feedback exists."""
    if not _db_initialized:
        return None
    try:
        conn = _get_conn()
        row = conn.execute(
            "SELECT id, alert_id, ts, rating, comment FROM alert_feedback WHERE alert_id = ?",
            (alert_id,),
        ).fetchone()
        return dict(row) if row else None
    except Exception as exc:
        logger.warning("get_feedback_for_alert failed: %s", type(exc).__name__)
        return None


def export_feedback() -> list[dict]:
    """
    Return all feedback joined with alert+AI data for RAG / fine-tuning export.

    Each record contains: alert metadata, AI insight, operator rating, comment.
    Returns empty list on DB error.
    """
    if not _db_initialized:
        return []
    try:
        conn = _get_conn()
        rows = conn.execute(
            """
            SELECT
                f.id          AS feedback_id,
                f.ts          AS feedback_ts,
                f.rating,
                f.comment,
                a.id          AS alert_id,
                a.ts          AS alert_ts,
                a.source,
                a.service,
                a.status,
                a.severity,
                a.message,
                a.insight
            FROM alert_feedback f
            JOIN alerts a ON a.id = f.alert_id
            ORDER BY f.ts DESC
            """,
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.warning("export_feedback failed: %s", type(exc).__name__)
        return []
