"""
Normalizes incoming webhook payloads into a common NormalizedAlert format.

Supported sources:
  - Uptime Kuma  (heartbeat + monitor fields)
  - Generic JSON (best-effort field mapping)
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class NormalizedAlert:
    source: str                          # "uptime_kuma" | "generic"
    status: str                          # "up" | "down" | "unknown"
    severity: str                        # "critical" | "warning" | "info"
    service_name: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


def _uptime_kuma_status(raw_status) -> tuple[str, str]:
    """Return (status, severity) from Uptime Kuma heartbeat status int."""
    if raw_status == 0:
        return "down", "critical"
    if raw_status == 1:
        return "up", "info"
    return "unknown", "warning"


def _is_uptime_kuma(data: dict) -> bool:
    return "heartbeat" in data and "monitor" in data


def _parse_uptime_kuma(data: dict) -> NormalizedAlert:
    hb = data.get("heartbeat", {})
    monitor = data.get("monitor", {})

    raw_status = hb.get("status", -1)
    status, severity = _uptime_kuma_status(raw_status)

    service_name = monitor.get("name") or monitor.get("url") or "Unknown Service"
    message = (
        data.get("msg")
        or hb.get("msg")
        or f"{service_name} is {status}"
    )

    details = {
        "monitor_url": monitor.get("url"),
        "monitor_type": monitor.get("type"),
        "monitor_id": monitor.get("id"),
        "ping_ms": hb.get("ping"),
        "time": hb.get("time"),
    }
    details = {k: v for k, v in details.items() if v is not None}

    return NormalizedAlert(
        source="uptime_kuma",
        status=status,
        severity=severity,
        service_name=service_name,
        message=message,
        details=details,
    )


def _parse_generic(data: dict) -> NormalizedAlert:
    # Best-effort mapping for arbitrary JSON payloads
    status_raw = (
        data.get("status")
        or data.get("state")
        or data.get("alertstate")
        or "unknown"
    )
    status_str = str(status_raw).lower()

    if status_str in ("down", "0", "false", "critical", "firing", "error"):
        status, severity = "down", "critical"
    elif status_str in ("up", "1", "true", "ok", "resolved", "normal"):
        status, severity = "up", "info"
    elif status_str in ("warning", "warn", "degraded"):
        status, severity = status_str, "warning"
    else:
        status, severity = "unknown", "warning"

    service_name = (
        data.get("service")
        or data.get("name")
        or data.get("host")
        or data.get("source")
        or "Unknown Service"
    )

    message = (
        data.get("message")
        or data.get("msg")
        or data.get("description")
        or data.get("text")
        or f"{service_name} alert: {status}"
    )

    # Store everything else as extra context for the AI
    excluded = {"status", "state", "alertstate", "service", "name",
                "host", "source", "message", "msg", "description", "text"}
    details = {k: v for k, v in data.items() if k not in excluded}

    return NormalizedAlert(
        source="generic",
        status=status,
        severity=severity,
        service_name=str(service_name),
        message=str(message),
        details=details,
    )


def parse_alert(data: dict) -> NormalizedAlert:
    """Entry point: detect format and return a NormalizedAlert."""
    if _is_uptime_kuma(data):
        return _parse_uptime_kuma(data)
    return _parse_generic(data)
