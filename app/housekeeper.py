"""
Database housekeeper — auto-pruning, WAL checkpointing, and DLQ retry.

Prevents SQLite bloat in long-running homelabs by periodically:
  1. Deleting alerts older than RETENTION_DAYS (default: 90)
  2. Deleting security events older than RETENTION_DAYS
  3. Running PRAGMA wal_checkpoint(PASSIVE) to reclaim WAL file space
  4. Retrying failed notifications from the dead letter queue

Configuration:
  RETENTION_DAYS       — delete alerts older than this (default: 90, 0 = no pruning)
  HOUSEKEEP_INTERVAL   — seconds between runs (default: 86400 = 24 hours)
  DLQ_MAX_RETRIES      — max retry attempts for dead letters (default: 3)

The housekeeper runs as a daemon thread started in create_app(). It exits
silently when the main process terminates. Errors are logged at WARNING
level but never interrupt the alert pipeline.
"""

import json
import logging
import threading
import time

from .alert_db import (
    _get_conn,
    close_thread_conn,
    get_pending_dead_letters,
    mark_dead_letter_done,
    mark_dead_letter_failed,
)
from .alert_parser import NormalizedAlert
from . import notify
from .utils import _env_int

logger = logging.getLogger(__name__)

_housekeeper_thread: threading.Thread | None = None
_housekeeper_lock = threading.Lock()


def _housekeep_loop(interval: int) -> None:
    """Periodic pruning + WAL checkpoint. Runs forever in a daemon thread."""
    # Initial delay — don't run immediately on startup
    time.sleep(60)

    while True:
        try:
            _run_housekeeping()
        except Exception:
            logger.warning("Housekeeper error", exc_info=True)
        finally:
            close_thread_conn()
        time.sleep(interval)


def _retry_dead_letters() -> None:
    """Retry failed notifications from the dead letter queue."""
    max_retries = _env_int("DLQ_MAX_RETRIES", 3)
    pending = get_pending_dead_letters(max_retries)
    if not pending:
        return

    logger.info("DLQ: retrying %d dead letter(s)", len(pending))
    for item in pending:
        try:
            alert_data = json.loads(item["alert_json"])
            alert = NormalizedAlert(**alert_data)
            ai = json.loads(item["ai_json"]) if item["ai_json"] else {}

            result = notify.dispatch(alert, ai)
            if result.succeeded > 0:
                mark_dead_letter_done(item["id"])
                logger.info("DLQ: retry succeeded for dead letter %d", item["id"])
            else:
                mark_dead_letter_failed(
                    item["id"],
                    "; ".join(result.errors) if result.errors else "no platforms attempted",
                )
                logger.warning("DLQ: retry failed for dead letter %d", item["id"])
        except Exception:
            logger.warning("DLQ: error retrying dead letter %d", item["id"], exc_info=True)
            mark_dead_letter_failed(item["id"], "retry error")


def _run_housekeeping() -> None:
    """Execute one housekeeping cycle."""
    retention = _env_int("RETENTION_DAYS", 90)

    if retention > 0:
        cutoff = time.time() - (retention * 86400)
        conn = _get_conn()

        # Prune old alerts
        cursor = conn.execute("DELETE FROM alerts WHERE ts < ?", (cutoff,))
        pruned_alerts = cursor.rowcount
        conn.commit()

        # Prune old security events
        cursor = conn.execute("DELETE FROM security_events WHERE ts < ?", (cutoff,))
        pruned_events = cursor.rowcount
        conn.commit()

        # Prune old rate_log entries (should be mostly empty, but belt-and-suspenders)
        conn.execute("DELETE FROM rate_log WHERE ts < ?", (cutoff,))
        conn.commit()

        # Prune expired dedup L2 cache entries
        conn.execute("DELETE FROM dedup_cache WHERE ts < ?", (cutoff,))
        conn.commit()

        if pruned_alerts > 0 or pruned_events > 0:
            logger.info(
                "Housekeeper pruned: %d alerts, %d security events older than %d days",
                pruned_alerts, pruned_events, retention,
            )

        # Prune completed/exhausted dead letters older than retention period
        conn.execute("DELETE FROM dead_letters WHERE ts < ?", (cutoff,))
        conn.commit()

        # Prune resolved incidents older than retention period
        try:
            cursor = conn.execute(
                "DELETE FROM incidents WHERE status = 'resolved' AND ts_end < ?",
                (cutoff,),
            )
            pruned_incidents = cursor.rowcount
            conn.commit()
            if pruned_incidents > 0:
                logger.info("Housekeeper pruned: %d resolved incidents", pruned_incidents)
        except Exception as exc:
            logger.warning("Incident pruning failed: %s", type(exc).__name__)

    # DLQ retry — re-attempt failed notifications
    _retry_dead_letters()

    # WAL checkpoint — reclaim disk space from the WAL file
    try:
        conn = _get_conn()
        conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        logger.debug("WAL checkpoint completed")
    except Exception:
        logger.warning("WAL checkpoint failed", exc_info=True)


def start_housekeeper() -> None:
    """
    Start the housekeeper thread.
    Called once from create_app(). Safe to call multiple times — only the
    first call starts the thread. Uses a lock to prevent concurrent calls
    from starting duplicate threads.
    """
    global _housekeeper_thread
    with _housekeeper_lock:
        if _housekeeper_thread is not None:
            return  # already running

        interval = _env_int("HOUSEKEEP_INTERVAL", 86400)
        if interval < 60:
            logger.warning("HOUSEKEEP_INTERVAL=%d is too low — using 60s minimum", interval)
            interval = 60

        _housekeeper_thread = threading.Thread(
            target=_housekeep_loop,
            args=(interval,),
            daemon=True,
            name="sentinel-housekeeper",
        )
        _housekeeper_thread.start()
        logger.info(
            "Housekeeper started: retention=%dd interval=%ds",
            _env_int("RETENTION_DAYS", 90), interval,
        )
