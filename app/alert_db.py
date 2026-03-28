"""
SQLite alert log with WAL mode.

Provides append-only alert logging and recent-history queries used to
give the AI context about a service's behaviour over time.

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
        conn.row_factory = sqlite3.Row
        _local.conn = conn
        _local.conn_path = path
    return _local.conn


def init_db() -> None:
    """Create tables and indexes if they don't exist. Called at app startup."""
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
        conn.commit()
        logger.info("Alert DB ready: %s", _db_path())
    except Exception as exc:
        logger.warning("Failed to initialise alert DB: %s", type(exc).__name__)


def log_alert(alert: NormalizedAlert, ai_result: dict | None, notified: bool) -> None:
    """
    Append an alert record to the database.

    ``notified=False`` when the alert was suppressed by a severity threshold —
    it is still logged so history reflects the true alert rate for the service.
    Deduplicated alerts are not logged (they are exact repeats already in the DB).
    """
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
    records this request if under the limit. Uses the SQLite WAL file shared
    across all Gunicorn workers — replaces the per-worker in-memory deque.

    Fails open: returns False on any DB error so a DB failure never blocks
    legitimate requests. At homelab write volumes, the check+insert window is
    negligibly small; a multi-worker race is acceptable for rate reduction.
    """
    try:
        conn = _get_conn()
        now = time.time()
        cutoff = now - window
        conn.execute("DELETE FROM rate_log WHERE ts < ?", (cutoff,))
        count = conn.execute("SELECT COUNT(*) FROM rate_log").fetchone()[0]
        if count < limit:
            conn.execute("INSERT INTO rate_log (ts) VALUES (?)", (now,))
        conn.commit()
        return count >= limit
    except Exception as exc:
        logger.warning("Rate log check failed: %s", type(exc).__name__)
        return False  # fail open — never block requests on DB error


def log_security_event(event_type: str, detail: str = "") -> None:
    """
    Append a security event record (auth failure, rate limit hit, injection detected).
    Fails silently — security logging must never interrupt request processing.
    """
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
    try:
        conn = _get_conn()
        total = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
        notified = conn.execute("SELECT COUNT(*) FROM alerts WHERE notified=1").fetchone()[0]
        last_ts = conn.execute("SELECT MAX(ts) FROM alerts").fetchone()[0]
        return {"total_alerts": total, "notified_count": notified, "last_alert_ts": last_ts}
    except Exception as exc:
        logger.warning("DB stats query failed: %s", type(exc).__name__)
        return {"total_alerts": None, "notified_count": None, "last_alert_ts": None}


def get_recent_alerts(service: str) -> list[dict]:
    """
    Return recent alerts for a service for AI context.

    Uses ALERT_HISTORY_HOURS if > 0 (time window), otherwise falls back to
    ALERT_HISTORY_LIMIT most-recent records (default 5).
    Returns an empty list on any DB error — callers treat no history as
    graceful degradation, not a failure.
    """
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
