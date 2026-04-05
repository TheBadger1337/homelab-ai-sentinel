"""
Homelab Pulse — pre-computed alert frequency stats for AI context.

Queries the existing alerts SQLite table and computes:
  - Alert counts for the last 1h, 24h, and 7d
  - Average interval between alerts in the last 24h
  - Comparison to the 7-day baseline rate

The stats are injected into the AI prompt (in predictive mode) so the
model can identify patterns like "this service is failing 5x more than
usual" without needing to reason about raw timestamps.

Failure policy: returns None on any DB error. Callers treat None as
"no pulse data available" — the AI call proceeds without stats.
"""

import logging
import time

from .alert_db import _get_conn, db_available

logger = logging.getLogger(__name__)


def get_pulse(service: str) -> dict | None:
    """
    Compute frequency stats for a service's recent alert history.

    Returns a dict with:
        count_1h:     int   — alerts in the last hour
        count_24h:    int   — alerts in the last 24 hours
        count_7d:     int   — alerts in the last 7 days
        avg_interval: float | None — average seconds between alerts in the last 24h
        rate_change:  str | None   — human-readable comparison to 7-day baseline

    Returns None if the service has no alert history or on DB error.
    """
    if not db_available():
        return None
    try:
        conn = _get_conn()
        now = time.time()
        cutoff_1h = now - 3600
        cutoff_24h = now - 86400
        cutoff_7d = now - 604800

        count_1h = conn.execute(
            "SELECT COUNT(*) FROM alerts WHERE service = ? AND ts >= ?",
            (service, cutoff_1h),
        ).fetchone()[0]

        count_24h = conn.execute(
            "SELECT COUNT(*) FROM alerts WHERE service = ? AND ts >= ?",
            (service, cutoff_24h),
        ).fetchone()[0]

        count_7d = conn.execute(
            "SELECT COUNT(*) FROM alerts WHERE service = ? AND ts >= ?",
            (service, cutoff_7d),
        ).fetchone()[0]

        if count_7d == 0:
            return None

        # Average interval between alerts in the last 24h
        avg_interval = None
        if count_24h >= 2:
            timestamps = conn.execute(
                "SELECT ts FROM alerts WHERE service = ? AND ts >= ? ORDER BY ts",
                (service, cutoff_24h),
            ).fetchall()
            ts_list = [r[0] for r in timestamps]
            intervals = [ts_list[i + 1] - ts_list[i] for i in range(len(ts_list) - 1)]
            avg_interval = sum(intervals) / len(intervals)

        # Rate comparison: 24h count vs. 7-day daily average
        daily_avg_7d = count_7d / 7.0
        rate_change = None
        if daily_avg_7d > 0 and count_24h > 0:
            ratio = count_24h / daily_avg_7d
            if ratio >= 2.0:
                rate_change = f"{ratio:.0f}x above 7-day average"
            elif ratio >= 1.5:
                rate_change = f"{ratio:.1f}x above 7-day average"
            elif ratio <= 0.5:
                rate_change = "below 7-day average"

        return {
            "count_1h": count_1h,
            "count_24h": count_24h,
            "count_7d": count_7d,
            "avg_interval": avg_interval,
            "rate_change": rate_change,
        }

    except Exception as exc:
        logger.warning("Pulse query failed: %s", type(exc).__name__)
        return None


def format_pulse(pulse: dict) -> str:
    """
    Format pulse stats as a compact string for injection into the AI prompt.
    Returns empty string if pulse is None.
    """
    if not pulse:
        return ""

    parts = [
        f"Alerts: {pulse['count_1h']} in the last hour,"
        f" {pulse['count_24h']} in 24h,"
        f" {pulse['count_7d']} in 7 days.",
    ]

    if pulse.get("avg_interval") is not None:
        interval = pulse["avg_interval"]
        if interval < 60:
            parts.append(f"Average interval: {interval:.0f}s between alerts.")
        elif interval < 3600:
            parts.append(f"Average interval: {interval / 60:.0f}m between alerts.")
        else:
            parts.append(f"Average interval: {interval / 3600:.1f}h between alerts.")

    if pulse.get("rate_change"):
        parts.append(f"Rate: {pulse['rate_change']}.")

    return " ".join(parts)
