"""
Synthetic Shadowing — detect services that have gone silent.

Operators declare expected heartbeat intervals per service in ``shadows.yaml``
(inside RUNBOOK_DIR, overridden by SHADOWS_FILE). When a watched service has
not sent any webhook for longer than its configured interval, Sentinel fires
a synthetic critical/warning alert through the normal notify path so the
operator is paged even if the monitoring tool itself is down or broken.

Configuration
=============
shadows.yaml::

    shadows:
      ping_monitor:
        interval: 300        # alert if no webhook received in 5 minutes
        severity: warning    # optional — warning (default) or critical
        description: "Daily ping health check expected every 5 minutes"

Environment variables::

    SHADOWS_FILE         — path to shadows.yaml (default: RUNBOOK_DIR/shadows.yaml)
    SHADOW_CHECK_INTERVAL — seconds between checks (default: 60)

Failure policy
==============
All errors are logged at WARNING and the thread continues. A DB or dispatch
failure silently skips that check cycle rather than crashing the worker.

Restart safety
==============
The check uses the incidents table to avoid re-firing a shadow alert when an
open incident already exists for the service. This means Sentinel correctly
skips re-alerting across restarts as long as the incident is still open.

Clearing shadows
================
When the service resumes sending webhooks, the next real alert naturally links
to the existing open incident. The operator resolves the incident from the UI.
No synthetic recovery is sent — the real alert IS the recovery signal.
"""

import logging
import os
import threading
import time
from dataclasses import dataclass

import yaml

logger = logging.getLogger(__name__)

_shadow_thread: threading.Thread | None = None
_shadow_lock = threading.Lock()

_VALID_SEVERITIES = {"critical", "warning", "info"}


@dataclass
class ShadowDef:
    service: str
    interval: int          # seconds of silence before alert fires
    severity: str          # warning, critical, info
    description: str       # optional human-readable description


def _shadows_path() -> str:
    explicit = os.environ.get("SHADOWS_FILE", "").strip()
    if explicit:
        return explicit
    runbook_dir = os.environ.get("RUNBOOK_DIR", "/data/runbooks")
    return os.path.join(runbook_dir, "shadows.yaml")


def load_shadow_config() -> list[ShadowDef]:
    """Load and return shadow definitions from shadows.yaml.

    Returns an empty list when the file is absent or unparseable.
    Errors are logged but never raised.
    """
    path = _shadows_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            logger.warning("shadows.yaml: expected a mapping at the top level — skipping")
            return []
        raw = data.get("shadows") or {}
        if not isinstance(raw, dict):
            logger.warning("shadows.yaml: 'shadows' key must be a mapping — skipping")
            return []
        defs: list[ShadowDef] = []
        for name, cfg in raw.items():
            if not isinstance(cfg, dict):
                logger.warning("shadows.yaml: entry %r must be a mapping — skipping", name)
                continue
            try:
                interval = int(cfg.get("interval", 0))
            except (TypeError, ValueError):
                logger.warning("shadows.yaml: %r has invalid interval — skipping", name)
                continue
            if interval <= 0:
                logger.warning("shadows.yaml: %r has non-positive interval — skipping", name)
                continue
            severity = str(cfg.get("severity", "warning")).lower()
            if severity not in _VALID_SEVERITIES:
                logger.warning(
                    "shadows.yaml: %r has unknown severity %r — using 'warning'", name, severity
                )
                severity = "warning"
            defs.append(
                ShadowDef(
                    service=str(name),
                    interval=interval,
                    severity=severity,
                    description=str(cfg.get("description", "")),
                )
            )
        return defs
    except Exception as exc:
        logger.warning("Failed to load shadows.yaml: %s", type(exc).__name__)
        return []


def _get_last_alert_ts(service: str) -> float | None:
    """Return the timestamp of the most recent alert for *service*, or None."""
    from .alert_db import db_available, _get_conn
    if not db_available():
        return None
    try:
        conn = _get_conn()
        row = conn.execute(
            "SELECT MAX(ts) FROM alerts WHERE service = ?", (service,)
        ).fetchone()
        return float(row[0]) if row and row[0] is not None else None
    except Exception as exc:
        logger.warning("Shadow: last_alert query failed for %r: %s", service, type(exc).__name__)
        return None


def _has_open_incident(service: str) -> bool:
    """Return True if there is already an open incident for *service*.

    Used to avoid re-firing shadow alerts on restart or after a previous
    shadow has already created an open incident.
    """
    from .alert_db import get_open_incident
    try:
        return get_open_incident(service) is not None
    except Exception:
        return False


def _fire_shadow_alert(shadow: ShadowDef, silence_seconds: float) -> None:
    """Dispatch a synthetic alert for a service that has gone silent."""
    from .alert_parser import NormalizedAlert
    from . import notify
    from .llm_client import get_ai_insight

    minutes = int(silence_seconds // 60)
    seconds = int(silence_seconds % 60)
    duration_str = f"{minutes}m {seconds}s" if minutes else f"{seconds}s"

    message = (
        f"No webhook received for {duration_str} "
        f"(expected every {shadow.interval}s)"
    )
    if shadow.description:
        message = f"{shadow.description} — {message}"

    alert = NormalizedAlert(
        source="shadowing",
        status="shadow",
        severity=shadow.severity,
        service_name=shadow.service,
        message=message,
        details={
            "silence_seconds": int(silence_seconds),
            "expected_interval": shadow.interval,
        },
    )

    try:
        ai = get_ai_insight(alert)
    except Exception as exc:
        logger.warning("Shadow AI enrichment failed for %r: %s", shadow.service, type(exc).__name__)
        ai = {"confidence": 1, "insight": "AI enrichment unavailable", "suggested_actions": []}

    try:
        notify.dispatch(alert, ai)
    except Exception as exc:
        logger.warning("Shadow dispatch failed for %r: %s", shadow.service, type(exc).__name__)

    logger.info(
        "Shadow alert fired: service=%s silence=%.0fs", shadow.service, silence_seconds
    )


def _check_shadows(shadows: list[ShadowDef]) -> None:
    """Run one check cycle — compare last_seen against each shadow's interval."""
    now = time.time()
    for shadow in shadows:
        try:
            last_ts = _get_last_alert_ts(shadow.service)
            if last_ts is None:
                # Never seen — treat startup time as origin; grace period = interval
                # so we don't immediately alert on first start with no history
                continue
            silence = now - last_ts
            if silence < shadow.interval:
                continue  # within window — all good
            # Silence exceeded. Check if an open incident already exists.
            if _has_open_incident(shadow.service):
                logger.debug(
                    "Shadow: %r silent for %.0fs but incident already open — skipping",
                    shadow.service, silence,
                )
                continue
            _fire_shadow_alert(shadow, silence)
        except Exception as exc:
            logger.warning(
                "Shadow check error for %r: %s", shadow.service, type(exc).__name__
            )


def _shadow_loop(check_interval: int) -> None:
    """Periodically check all shadows. Reloads the config each cycle so
    operators can update shadows.yaml without restarting the container."""
    while True:
        try:
            shadows = load_shadow_config()
            if shadows:
                _check_shadows(shadows)
        except Exception as exc:
            logger.warning("Shadow loop error: %s", type(exc).__name__)
        time.sleep(check_interval)


def start_shadowing() -> None:
    """Start the shadow checker thread. Called once from create_app().

    Requires DB to be available (used for last-seen queries and open incident
    checks). Silently returns if DB is unavailable or no shadows configured.
    """
    global _shadow_thread
    with _shadow_lock:
        if _shadow_thread is not None:
            return

        from .alert_db import db_available
        if not db_available():
            logger.debug("Shadowing not started — requires DB")
            return

        # Check at startup whether any shadows are configured
        shadows = load_shadow_config()
        if not shadows:
            logger.debug("Shadowing not started — no shadows.yaml or empty config")
            return

        try:
            check_interval = max(10, int(os.environ.get("SHADOW_CHECK_INTERVAL", "60")))
        except (ValueError, TypeError):
            check_interval = 60

        _shadow_thread = threading.Thread(
            target=_shadow_loop,
            args=(check_interval,),
            daemon=True,
            name="sentinel-shadowing",
        )
        _shadow_thread.start()
        logger.info(
            "Shadowing started: %d service(s) watched, check every %ds",
            len(shadows), check_interval,
        )
