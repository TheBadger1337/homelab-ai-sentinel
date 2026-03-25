"""
Normalizes incoming webhook payloads into a common NormalizedAlert format.

Supported sources:
  - Uptime Kuma  (heartbeat + monitor fields)
  - Grafana      (alerts array + groupLabels fields)
  - Generic JSON (best-effort field mapping)
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class NormalizedAlert:
    source: str                          # "uptime_kuma" | "grafana" | "generic"
    status: str                          # "up" | "down" | "unknown"
    severity: str                        # "critical" | "warning" | "info"
    service_name: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


def _uptime_kuma_status(raw_status: int) -> tuple[str, str]:
    """Return (status, severity) from Uptime Kuma heartbeat status int."""
    if raw_status == 0:
        return "down", "critical"
    if raw_status == 1:
        return "up", "info"
    return "unknown", "warning"


def _is_uptime_kuma(data: dict[str, Any]) -> bool:
    return "heartbeat" in data and "monitor" in data


def _parse_uptime_kuma(data: dict[str, Any]) -> NormalizedAlert:
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


def _is_grafana(data: dict[str, Any]) -> bool:
    return isinstance(data.get("alerts"), list) and "groupLabels" in data


def _parse_grafana(data: dict[str, Any]) -> NormalizedAlert:
    top_status = str(data.get("status", "unknown")).lower()
    if top_status in ("firing", "alerting"):
        status, severity = "down", "critical"
    elif top_status == "resolved":
        status, severity = "up", "info"
    else:
        status, severity = "unknown", "warning"

    # Service name: prefer commonLabels.alertname, fall back to groupLabels, then first alert
    common_labels = data.get("commonLabels", {})
    group_labels = data.get("groupLabels", {})
    first_alert = (data.get("alerts") or [{}])[0]
    first_labels = first_alert.get("labels", {})

    service_name = (
        common_labels.get("alertname")
        or group_labels.get("alertname")
        or first_labels.get("alertname")
        or first_labels.get("job")
        or "Unknown Service"
    )

    # Message: top-level message field, then annotations
    common_annotations = data.get("commonAnnotations", {})
    first_annotations = first_alert.get("annotations", {})

    message = (
        data.get("message")
        or common_annotations.get("summary")
        or common_annotations.get("description")
        or first_annotations.get("summary")
        or first_annotations.get("description")
        or f"{service_name} is {status}"
    )

    details = {
        "labels": common_labels or first_labels,
        "annotations": common_annotations or first_annotations,
        "firing_count": len([a for a in data.get("alerts", []) if a.get("status") == "firing"]),
        "generator_url": first_alert.get("generatorURL"),
        "dashboard_url": first_alert.get("dashboardURL") or None,
    }
    details = {k: v for k, v in details.items() if v}

    return NormalizedAlert(
        source="grafana",
        status=status,
        severity=severity,
        service_name=str(service_name),
        message=str(message),
        details=details,
    )


def _parse_generic(data: dict[str, Any]) -> NormalizedAlert:
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
        status, severity = "warning", "warning"
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


def parse_alert(data: dict[str, Any]) -> NormalizedAlert:
    """Entry point: detect format and return a NormalizedAlert."""
    if _is_uptime_kuma(data):
        return _parse_uptime_kuma(data)
    if _is_grafana(data):
        return _parse_grafana(data)
    return _parse_generic(data)
