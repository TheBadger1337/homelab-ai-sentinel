"""
Per-service severity threshold filtering.

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
"""

import logging
import os

logger = logging.getLogger(__name__)

_SEVERITY_ORDER: dict[str, int] = {"info": 0, "warning": 1, "critical": 2}


def _service_env_key(service: str) -> str:
    """Convert a service name to its THRESHOLD_<KEY> env var name."""
    sanitised = "".join(c if c.isalnum() else "_" for c in service.upper())
    return f"THRESHOLD_{sanitised}"


def _threshold_for_service(service: str) -> str:
    """Return the effective severity threshold for a service."""
    global_floor = os.environ.get("MIN_SEVERITY", "info").lower()
    if global_floor not in _SEVERITY_ORDER:
        global_floor = "info"

    per_service = os.environ.get(_service_env_key(service), "").lower()
    if per_service in _SEVERITY_ORDER:
        return per_service

    return global_floor


def should_suppress(alert) -> bool:
    """
    Return True if the alert's severity is below the configured threshold.

    Suppressed alerts are still logged to the DB (notified=0) so history
    reflects the true alert rate for that service.
    """
    threshold = _threshold_for_service(alert.service_name)
    alert_level = _SEVERITY_ORDER.get(alert.severity, 0)
    threshold_level = _SEVERITY_ORDER.get(threshold, 0)

    if alert_level < threshold_level:
        logger.info(
            "Alert suppressed: service=%r severity=%r below threshold=%r",
            alert.service_name, alert.severity, threshold,
        )
        return True
    return False
