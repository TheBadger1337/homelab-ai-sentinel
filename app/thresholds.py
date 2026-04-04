"""
Alert suppression filters — severity thresholds, quiet hours, metric thresholds.

Severity thresholds
-------------------
Global floor:  MIN_SEVERITY            — applies to all services (default: info)
Per-service:   THRESHOLD_<SERVICE_KEY> — overrides the global for that service

Service names are upper-cased and non-alphanumeric characters replaced with
underscores to form the env var key, e.g.:
    service "nginx"    →  THRESHOLD_NGINX
    service "my-nginx" →  THRESHOLD_MY_NGINX
    service "web app"  →  THRESHOLD_WEB_APP

Severity ordering (ascending): info < warning < critical

Per-service thresholds can be set lower than the global floor — individual
services can be made more permissive. The global is a default, not a hard cap.

Quiet hours
-----------
QUIET_HOURS=HH:MM-HH:MM            — time range using the container's local clock
QUIET_HOURS_MIN_SEVERITY=critical  — threshold during quiet hours (default: critical)

Ranges crossing midnight are supported (e.g. 22:00-08:00). During quiet hours the
effective threshold is the more restrictive of QUIET_HOURS_MIN_SEVERITY and the
regular per-service/global threshold. The container timezone is set by the host
(TZ env var or /etc/localtime mount).

Metric thresholds
-----------------
METRIC_THRESHOLD_<KEY>=<integer>   — suppress alerts where a numeric metric value
                                     is below the configured integer threshold.

The metric key is matched (case-insensitive) against:
  1. Keys in alert.details — exact match, then base key without common suffixes
     (_percent, _usage, _utilization, _rate, _pct)
  2. The alert message — looks for the keyword near a percentage value

Examples:
  METRIC_THRESHOLD_MEMORY_PERCENT=95   — suppress if memory < 95%
  METRIC_THRESHOLD_CPU_PERCENT=90      — suppress if CPU < 90%
  METRIC_THRESHOLD_DISK_PERCENT=85     — suppress if disk < 85%
"""

import logging
import os
import re
import time as _time_mod
from datetime import datetime, time as _Time

logger = logging.getLogger(__name__)

_SEVERITY_ORDER: dict[str, int] = {"info": 0, "warning": 1, "critical": 2}


# ---------------------------------------------------------------------------
# Severity threshold helpers
# ---------------------------------------------------------------------------

def _service_env_key(service: str) -> str:
    """Convert a service name to its THRESHOLD_<KEY> env var name."""
    sanitised = "".join(c if c.isalnum() else "_" for c in service.upper())
    return f"THRESHOLD_{sanitised}"


def _threshold_for_service(service: str) -> str:
    """Return the effective severity threshold for a service (ignoring quiet hours)."""
    global_floor = os.environ.get("MIN_SEVERITY", "info").lower()
    if global_floor not in _SEVERITY_ORDER:
        global_floor = "info"

    per_service = os.environ.get(_service_env_key(service), "").lower()
    if per_service in _SEVERITY_ORDER:
        return per_service

    return global_floor


# ---------------------------------------------------------------------------
# Quiet hours
# ---------------------------------------------------------------------------

def _parse_time_str(s: str) -> _Time | None:
    """Parse 'HH:MM' into a time object. Returns None on invalid input."""
    try:
        parts = s.strip().split(":")
        return _Time(int(parts[0]), int(parts[1]))
    except (ValueError, IndexError):
        return None


def _in_quiet_hours(now: _Time | None = None) -> bool:
    """
    Return True if the current local time falls within the QUIET_HOURS window.

    Accepts an optional ``now`` argument for testing — omit to use the real clock.
    Ranges that cross midnight (e.g. 22:00-08:00) are correctly handled.
    Returns False if QUIET_HOURS is unset or unparseable.
    """
    quiet = os.environ.get("QUIET_HOURS", "").strip()
    if not quiet:
        return False

    try:
        start_str, end_str = quiet.split("-", 1)
    except ValueError:
        return False

    start = _parse_time_str(start_str)
    end = _parse_time_str(end_str)
    if start is None or end is None:
        logger.warning("QUIET_HOURS value %r could not be parsed — ignoring", quiet)
        return False

    if now is None:
        now = datetime.now().time()

    if start <= end:
        # Same-day range, e.g. 08:00–22:00
        return start <= now < end
    else:
        # Overnight range, e.g. 22:00–08:00
        return now >= start or now < end


# ---------------------------------------------------------------------------
# Metric thresholds
# ---------------------------------------------------------------------------

# Common suffixes stripped when deriving the human keyword from an env var key
_METRIC_SUFFIXES = ("_percent", "_usage", "_utilization", "_rate", "_pct")

# Matches a numeric percentage value, e.g. "71.18%" or "71 %"
_PCT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def _metric_keyword(metric_key: str) -> str:
    """
    Derive the plain-English keyword from a metric env key for message scanning.
    e.g. "memory_percent" → "memory",  "disk_usage" → "disk"
    """
    key = metric_key
    for suffix in _METRIC_SUFFIXES:
        if key.endswith(suffix):
            key = key[: -len(suffix)]
            break
    return key.replace("_", " ")


def _parse_float_value(v) -> float | None:
    """Parse a metric value that may be int, float, or a string like '71.18' or '71.18%'."""
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip().rstrip("%").strip())
        except ValueError:
            return None
    return None


def _extract_metric_from_details(alert, metric_key: str) -> float | None:
    """
    Look for the metric in alert.details by key name.
    Tries the full key first (e.g. 'memory_percent'), then the base key without
    common suffixes (e.g. 'memory') to handle parsers that omit the suffix.
    """
    if not alert.details:
        return None

    # Full key match
    for k, v in alert.details.items():
        if k.lower() == metric_key:
            return _parse_float_value(v)

    # Base key match (strip suffix)
    base = metric_key
    for suffix in _METRIC_SUFFIXES:
        if metric_key.endswith(suffix):
            base = metric_key[: -len(suffix)]
            break

    if base != metric_key:
        for k, v in alert.details.items():
            if k.lower() == base:
                return _parse_float_value(v)

    return None


def _extract_metric_from_message(message: str, metric_key: str) -> float | None:
    """
    Fall back to scanning the alert message for a percentage associated with
    the metric keyword.

    Strategy: prefer the first percentage that follows the keyword in the string
    (e.g. "memory at 71%"), which covers every standard monitoring message format.
    Falls back to the last percentage before the keyword if none appear after.
    This correctly handles "CPU at 45%, memory at 71%" — returns 71 for 'memory'.
    """
    keyword = _metric_keyword(metric_key)
    msg_lower = message.lower()
    if keyword not in msg_lower:
        return None

    matches = list(_PCT_RE.finditer(message))
    if not matches:
        return None

    kw_pos = msg_lower.find(keyword)

    after = [m for m in matches if m.start() > kw_pos]
    if after:
        return float(after[0].group(1))

    # No percentage follows the keyword — take the last one before it
    before = [m for m in matches if m.start() <= kw_pos]
    if before:
        return float(before[-1].group(1))

    return None


def _should_suppress_metric(alert) -> bool:
    """
    Return True if any METRIC_THRESHOLD_<KEY>=<N> env var suppresses this alert.
    Suppresses when the extracted metric value is strictly below the threshold.
    """
    for key, val in os.environ.items():
        if not key.startswith("METRIC_THRESHOLD_"):
            continue
        metric_key = key[len("METRIC_THRESHOLD_"):].lower()
        try:
            threshold = int(val)
        except ValueError:
            continue

        value = _extract_metric_from_details(alert, metric_key)
        if value is None:
            value = _extract_metric_from_message(alert.message, metric_key)

        if value is not None and value < threshold:
            logger.info(
                "Alert suppressed: service=%r metric %r=%.2f below threshold %d",
                alert.service_name, metric_key, value, threshold,
            )
            return True

    return False


# ---------------------------------------------------------------------------
# Severity escalation
# ---------------------------------------------------------------------------
# If a service fires N warning alerts within a time window, auto-escalate
# severity to critical. Catches slow burns (memory creeping every 30 min)
# that never individually cross a threshold.
#
# Config:
#   ESCALATION_THRESHOLD=5      — number of warnings required (default: 0 = disabled)
#   ESCALATION_WINDOW=3600      — time window in seconds (default: 3600 = 1 hour)
#
# Only escalates warning→critical. Info alerts are excluded (too noisy).
# Critical alerts are already at the highest level.

def _check_escalation(alert) -> bool:
    """
    Check if this alert should be escalated from warning to critical.
    Returns True if escalation was applied (alert.severity is mutated).
    Returns False if no escalation needed or on any error.
    """
    from .alert_db import _get_conn
    from .utils import _env_int

    threshold = _env_int("ESCALATION_THRESHOLD", 0)
    if threshold <= 0:
        return False  # escalation disabled

    if alert.severity != "warning":
        return False  # only escalate warnings

    window = _env_int("ESCALATION_WINDOW", 3600)
    try:
        conn = _get_conn()
        cutoff = _time_mod.time() - window
        count = conn.execute(
            """
            SELECT COUNT(*) FROM alerts
            WHERE service = ? AND severity = 'warning' AND ts >= ?
            """,
            (alert.service_name, cutoff),
        ).fetchone()[0]

        if count >= threshold:
            logger.info(
                "Severity escalated: service=%r had %d warnings in %ds window "
                "(threshold=%d) — escalating to critical",
                alert.service_name, count, window, threshold,
            )
            alert.severity = "critical"
            return True

    except Exception as exc:
        logger.warning("Escalation check failed: %s", type(exc).__name__)

    return False


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def should_suppress(alert) -> bool:
    """
    Return True if the alert should be suppressed by any configured filter.

    Checks in order:
    1. Severity threshold — global floor + per-service override.
       During quiet hours, the effective threshold is the more restrictive of
       the regular threshold and QUIET_HOURS_MIN_SEVERITY (default: critical).
    2. Metric threshold — structured details or message-extracted percentage.

    Suppressed alerts are still logged to the DB (notified=0) so history
    reflects the true alert rate for that service.
    """
    # --- Severity threshold ---
    threshold = _threshold_for_service(alert.service_name)

    if _in_quiet_hours():
        qh_threshold = os.environ.get("QUIET_HOURS_MIN_SEVERITY", "critical").lower()
        if qh_threshold not in _SEVERITY_ORDER:
            qh_threshold = "critical"
        # Take the more restrictive of the two thresholds
        if _SEVERITY_ORDER.get(qh_threshold, 0) > _SEVERITY_ORDER.get(threshold, 0):
            threshold = qh_threshold
            logger.debug("Quiet hours active — effective threshold elevated to %r", threshold)

    if _SEVERITY_ORDER.get(alert.severity, 0) < _SEVERITY_ORDER.get(threshold, 0):
        logger.info(
            "Alert suppressed: service=%r severity=%r below threshold=%r",
            alert.service_name, alert.severity, threshold,
        )
        return True

    # --- Metric threshold ---
    if _should_suppress_metric(alert):
        return True

    return False
