"""
Normalizes incoming webhook payloads into a common NormalizedAlert format.

Supported sources:
  - Uptime Kuma        (heartbeat + monitor fields)
  - Grafana            (alerts array + orgId — Unified Alerting webhook)
  - Alertmanager       (alerts array + receiver, no orgId — Prometheus Alertmanager)
  - Healthchecks.io    (check_id + slug fields)
  - Netdata            (alarm + chart + hostname fields)
  - Zabbix             (trigger_name + trigger_severity fields)
  - Checkmk            (NOTIFICATIONTYPE + HOSTNAME — ALL_CAPS keys)
  - WUD                (updateAvailable + image fields — What's Up Docker)
  - Docker Events      (Type + Action + Actor — Docker event API, forwarded by Portainer)
  - Glances            (glances_host + glances_type — via glances_poller.py sidecar)
  - Generic JSON       (best-effort field mapping — catches everything else)

Detection order matters: more specific formats are tested first so that
Grafana and Alertmanager payloads (both have "alerts" + "groupLabels") are
correctly routed. The key discriminator is orgId (Grafana-only field).

Node Exporter alert context: alerts originating from node_exporter rules
come through Prometheus Alertmanager with labels like job="node" and
instance="host:9100". The Alertmanager parser captures these automatically
— node_exporter details land in alert.details["job"] and alert.details["instance"].
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class NormalizedAlert:
    source: str                          # "uptime_kuma" | "grafana" | "alertmanager" |
    #                                      "healthchecks" | "netdata" | "generic"
    status: str                          # "up" | "down" | "warning" | "unknown"
    severity: str                        # "critical" | "warning" | "info"
    service_name: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Uptime Kuma
# ---------------------------------------------------------------------------

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
        message=str(message),
        details=details,
    )


# ---------------------------------------------------------------------------
# Grafana Unified Alerting
# ---------------------------------------------------------------------------

def _is_grafana(data: dict[str, Any]) -> bool:
    # Grafana Unified Alerting always includes orgId.
    # Prometheus Alertmanager uses the same alerts+groupLabels structure but
    # never sends orgId — it sends receiver instead. Use orgId as discriminator.
    return (
        isinstance(data.get("alerts"), list)
        and "groupLabels" in data
        and "orgId" in data
    )


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
    details = {k: v for k, v in details.items() if v is not None}

    return NormalizedAlert(
        source="grafana",
        status=status,
        severity=severity,
        service_name=str(service_name),
        message=str(message),
        details=details,
    )


# ---------------------------------------------------------------------------
# Prometheus Alertmanager
# ---------------------------------------------------------------------------

def _is_alertmanager(data: dict[str, Any]) -> bool:
    # Alertmanager v4 webhook: has alerts array + receiver + groupLabels, no orgId.
    # Grafana Unified Alerting also sends receiver but always includes orgId —
    # the orgId absence check ensures we don't misidentify Grafana as Alertmanager.
    return (
        isinstance(data.get("alerts"), list)
        and "receiver" in data
        and "groupLabels" in data
        and "orgId" not in data
    )


def _alertmanager_severity(labels: dict[str, Any]) -> str:
    """Map Prometheus severity label to normalized severity."""
    sev = str(labels.get("severity", "")).lower()
    if sev in ("critical", "page"):
        return "critical"
    if sev in ("warning", "warn"):
        return "warning"
    return "info"


def _parse_alertmanager(data: dict[str, Any]) -> NormalizedAlert:
    top_status = str(data.get("status", "unknown")).lower()
    if top_status == "firing":
        status = "down"
    elif top_status == "resolved":
        status = "up"
    else:
        status = "unknown"

    common_labels = data.get("commonLabels", {})
    group_labels = data.get("groupLabels", {})
    first_alert = (data.get("alerts") or [{}])[0]
    first_labels = first_alert.get("labels", {})

    all_labels = {**first_labels, **common_labels}  # commonLabels overrides per-alert labels
    severity = _alertmanager_severity(all_labels) if status != "unknown" else "warning"

    service_name = (
        common_labels.get("alertname")
        or group_labels.get("alertname")
        or first_labels.get("alertname")
        or common_labels.get("job")
        or first_labels.get("job")
        or "Unknown Service"
    )

    common_annotations = data.get("commonAnnotations", {})
    first_annotations = first_alert.get("annotations", {})

    message = (
        common_annotations.get("summary")
        or common_annotations.get("description")
        or first_annotations.get("summary")
        or first_annotations.get("description")
        or f"{service_name} is {status}"
    )

    details = {
        "receiver": data.get("receiver"),
        "labels": all_labels,
        "annotations": common_annotations or first_annotations,
        "firing_count": len([a for a in data.get("alerts", []) if a.get("status") == "firing"]),
        "instance": all_labels.get("instance"),
        "job": all_labels.get("job"),
        "external_url": data.get("externalURL"),
    }
    details = {k: v for k, v in details.items() if v is not None}

    return NormalizedAlert(
        source="alertmanager",
        status=status,
        severity=severity,
        service_name=str(service_name),
        message=str(message),
        details=details,
    )


# ---------------------------------------------------------------------------
# Healthchecks.io
# ---------------------------------------------------------------------------

def _is_healthchecks(data: dict[str, Any]) -> bool:
    # check_id (UUID) + slug are unique to healthchecks.io payloads
    return "check_id" in data and "slug" in data


def _parse_healthchecks(data: dict[str, Any]) -> NormalizedAlert:
    raw_status = str(data.get("status", "unknown")).lower()

    if raw_status == "down":
        status, severity = "down", "critical"
        default_msg = "{name}: check failed — no ping received within the deadline"
    elif raw_status == "grace":
        # Ping overdue but within the grace window — service may still recover
        status, severity = "warning", "warning"
        default_msg = "{name}: in grace period — ping overdue, check may be failing"
    elif raw_status == "up":
        status, severity = "up", "info"
        default_msg = "{name}: check recovered — ping received"
    else:
        status, severity = "unknown", "warning"
        default_msg = "{name}: status {raw_status}"

    service_name = data.get("name") or data.get("slug") or "Unknown Check"
    message = default_msg.format(name=service_name, raw_status=raw_status)

    details = {
        "check_id": data.get("check_id"),
        "slug": data.get("slug"),
        "period_seconds": data.get("period"),
        "grace_seconds": data.get("grace"),
        "last_ping": data.get("last_ping"),
        "ping_url": data.get("ping_url"),
    }
    details = {k: v for k, v in details.items() if v is not None}

    return NormalizedAlert(
        source="healthchecks",
        status=status,
        severity=severity,
        service_name=str(service_name),
        message=message,
        details=details,
    )


# ---------------------------------------------------------------------------
# Netdata
# ---------------------------------------------------------------------------

_NETDATA_STATUS_MAP = {
    "critical":      ("down",    "critical"),
    "warning":       ("warning", "warning"),
    "clear":         ("up",      "info"),
    "undefined":     ("unknown", "warning"),
    "uninitialized": ("unknown", "warning"),
}


def _is_netdata(data: dict[str, Any]) -> bool:
    # alarm + chart + hostname is unique to Netdata alarm webhooks
    return "alarm" in data and "chart" in data and "hostname" in data


def _parse_netdata(data: dict[str, Any]) -> NormalizedAlert:
    raw_status = str(data.get("status", "")).lower()
    status, severity = _NETDATA_STATUS_MAP.get(raw_status, ("unknown", "warning"))

    hostname = data.get("hostname", "unknown-host")
    alarm = data.get("alarm", "unknown-alarm")
    chart = data.get("chart", "")

    service_name = f"{hostname}: {alarm}"

    value = data.get("value")
    units = data.get("units", "")
    info = data.get("info", "")

    if value is not None:
        value_str = f"{value}{units}"
        message = f"{alarm} on {hostname}: {value_str} — {info}" if info else f"{alarm} on {hostname}: {value_str}"
    else:
        message = f"{alarm} on {hostname} is {raw_status.upper()}" if raw_status else f"{alarm} on {hostname}"

    details = {
        "hostname": hostname,
        "chart": chart,
        "family": data.get("family"),
        "old_status": data.get("old_status"),
        "value": value,
        "old_value": data.get("old_value"),
        "units": units or None,
        "duration_seconds": data.get("duration"),
        "priority": data.get("priority"),
        "roles": data.get("roles"),
    }
    details = {k: v for k, v in details.items() if v is not None}

    return NormalizedAlert(
        source="netdata",
        status=status,
        severity=severity,
        service_name=service_name,
        message=message,
        details=details,
    )


# ---------------------------------------------------------------------------
# Generic (fallback)
# ---------------------------------------------------------------------------

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

    # Store everything else as extra context for the AI.
    # Strip keys that commonly hold credentials — the generic parser is the
    # widest injection surface and should never forward secrets to the AI prompt.
    _SENSITIVE_KEYS = {
        "password", "passwd", "token", "secret", "key", "api_key",
        "auth", "authorization", "access_token", "refresh_token",
        "private_key", "credential", "credentials",
    }
    excluded = {"status", "state", "alertstate", "service", "name",
                "host", "source", "message", "msg", "description", "text"}
    details = {
        k: v for k, v in data.items()
        if k not in excluded and k.lower() not in _SENSITIVE_KEYS
    }

    return NormalizedAlert(
        source="generic",
        status=status,
        severity=severity,
        service_name=str(service_name),
        message=str(message),
        details=details,
    )


# ---------------------------------------------------------------------------
# Zabbix
# ---------------------------------------------------------------------------

_ZABBIX_SEVERITY_MAP = {
    "not classified": "info",
    "information":    "info",
    "warning":        "warning",
    "average":        "warning",
    "high":           "critical",
    "disaster":       "critical",
}


def _is_zabbix(data: dict[str, Any]) -> bool:
    # Zabbix standard webhook payload always includes both of these
    return "trigger_name" in data and "trigger_severity" in data


def _parse_zabbix(data: dict[str, Any]) -> NormalizedAlert:
    raw_status = str(data.get("trigger_status", data.get("event_status", ""))).upper()
    if raw_status in ("PROBLEM",):
        status = "down"
    elif raw_status in ("RESOLVED", "OK", "RECOVERY"):
        status = "up"
    else:
        status = "unknown"

    raw_severity = str(data.get("trigger_severity", "")).lower()
    severity = _ZABBIX_SEVERITY_MAP.get(raw_severity, "warning")
    if status == "up":
        severity = "info"
    elif status == "unknown":
        severity = "warning"

    service_name = data.get("host_name") or data.get("trigger_name") or "Unknown Host"
    message = (
        data.get("event_message")
        or data.get("trigger_description")
        or f"{data.get('trigger_name', 'Alert')} on {service_name}"
    )

    details = {
        "trigger":    data.get("trigger_name"),
        "severity":   data.get("trigger_severity"),
        "host_ip":    data.get("host_ip"),
        "item_name":  data.get("item_name"),
        "item_value": data.get("item_value"),
        "event_id":   data.get("event_id"),
    }
    details = {k: v for k, v in details.items() if v is not None}

    return NormalizedAlert(
        source="zabbix",
        status=status,
        severity=severity,
        service_name=str(service_name),
        message=str(message),
        details=details,
    )


# ---------------------------------------------------------------------------
# Checkmk
# ---------------------------------------------------------------------------

_CHECKMK_STATE_MAP = {
    "CRIT":    ("down",    "critical"),
    "DOWN":    ("down",    "critical"),
    "WARN":    ("warning", "warning"),
    "UNKNOWN": ("unknown", "warning"),
    "OK":      ("up",      "info"),
    "UP":      ("up",      "info"),
}


def _is_checkmk(data: dict[str, Any]) -> bool:
    # Checkmk passes notification context as ALL_CAPS environment-style keys
    return "NOTIFICATIONTYPE" in data and "HOSTNAME" in data


def _parse_checkmk(data: dict[str, Any]) -> NormalizedAlert:
    notif_type = str(data.get("NOTIFICATIONTYPE", "")).upper()

    if "SERVICESTATE" in data:
        # Service check alert
        raw_state = str(data.get("SERVICESTATE", "")).upper()
        service_name = f"{data.get('HOSTNAME', 'Unknown')}: {data.get('SERVICEDESC', 'Service')}"
        message = (
            data.get("SERVICEOUTPUT")
            or f"{data.get('SERVICEDESC', 'Service')} is {raw_state}"
        )
    else:
        # Host check alert
        raw_state = str(data.get("HOSTSTATE", "")).upper()
        service_name = str(data.get("HOSTNAME", "Unknown Host"))
        message = data.get("HOSTOUTPUT") or f"Host {service_name} is {raw_state}"

    if notif_type == "RECOVERY":
        status, severity = "up", "info"
    else:
        status, severity = _CHECKMK_STATE_MAP.get(raw_state, ("unknown", "warning"))

    details = {
        "notification_type": data.get("NOTIFICATIONTYPE"),
        "host":              data.get("HOSTNAME"),
        "host_address":      data.get("HOSTADDRESS"),
        "service":           data.get("SERVICEDESC"),
        "state":             raw_state,
        "contact":           data.get("CONTACTNAME"),
    }
    details = {k: v for k, v in details.items() if v is not None}

    return NormalizedAlert(
        source="checkmk",
        status=status,
        severity=severity,
        service_name=str(service_name),
        message=str(message),
        details=details,
    )


# ---------------------------------------------------------------------------
# What's Up Docker (WUD)
# ---------------------------------------------------------------------------

def _is_wud(data: dict[str, Any]) -> bool:
    return "updateAvailable" in data and "image" in data


def _parse_wud(data: dict[str, Any]) -> NormalizedAlert:
    update_available = bool(data.get("updateAvailable", False))

    image = data.get("image") or {}
    current_tag = (image.get("tag") or {}).get("value", "unknown") if isinstance(image.get("tag"), dict) else str(image.get("tag", "unknown"))
    image_name = image.get("name", "unknown-image") if isinstance(image, dict) else "unknown-image"

    result = data.get("result") or {}
    new_tag = result.get("tag", "") if isinstance(result, dict) else ""

    container_name = data.get("displayName") or data.get("name") or image_name

    if update_available:
        status, severity = "warning", "warning"
        if new_tag:
            message = f"{container_name}: update available {current_tag} → {new_tag}"
        else:
            message = f"{container_name}: update available from {current_tag}"
    else:
        status, severity = "up", "info"
        message = f"{container_name}: up to date ({current_tag})"

    details = {
        "container_id": data.get("id"),
        "image_name":   image_name,
        "current_tag":  current_tag,
        "new_tag":      new_tag or None,
        "registry":     (image.get("registry") or {}).get("name") if isinstance(image.get("registry"), dict) else None,
    }
    details = {k: v for k, v in details.items() if v is not None}

    return NormalizedAlert(
        source="wud",
        status=status,
        severity=severity,
        service_name=str(container_name),
        message=message,
        details=details,
    )


# ---------------------------------------------------------------------------
# Docker Events (forwarded by Portainer and other Docker management tools)
# ---------------------------------------------------------------------------

_DOCKER_ACTION_MAP = {
    "die":     ("down",    "critical"),
    "kill":    ("down",    "critical"),
    "oom":     ("down",    "critical"),
    "stop":    ("warning", "warning"),
    "pause":   ("warning", "warning"),
    "restart": ("warning", "warning"),
    "start":   ("up",      "info"),
    "create":  ("up",      "info"),
    "unpause": ("up",      "info"),
    "destroy": ("unknown", "warning"),
}


def _is_docker_events(data: dict[str, Any]) -> bool:
    # Docker Events API uses capital-T Type, capital-A Action and Actor
    return "Type" in data and "Action" in data and "Actor" in data


def _parse_docker_events(data: dict[str, Any]) -> NormalizedAlert:
    action = str(data.get("Action", "")).lower()
    actor_attrs = (data.get("Actor") or {}).get("Attributes") or {}

    container_name = actor_attrs.get("name") or (data.get("Actor") or {}).get("ID", "")[:12] or "unknown-container"
    image = actor_attrs.get("image", "")
    service_name = f"{container_name} ({image})" if image else container_name

    # health_status events arrive as "health_status: unhealthy" / "health_status: healthy"
    if action.startswith("health_status:"):
        health = action.split(":", 1)[1].strip()
        if health == "unhealthy":
            status, severity = "down", "critical"
        elif health == "healthy":
            status, severity = "up", "info"
        else:
            status, severity = "unknown", "warning"
        message = f"Container {container_name} health status: {health}"
    else:
        status, severity = _DOCKER_ACTION_MAP.get(action, ("unknown", "warning"))
        exit_code = actor_attrs.get("exitCode")
        if action == "die" and exit_code is not None:
            message = f"Container {container_name} exited (exit code {exit_code})"
        else:
            message = f"Container {container_name}: {action}"

    details = {
        "action":      data.get("Action"),
        "type":        data.get("Type"),
        "image":       image or None,
        "exit_code":   actor_attrs.get("exitCode"),
        "scope":       data.get("scope"),
        "container_id": (data.get("Actor") or {}).get("ID", "")[:12] or None,
    }
    details = {k: v for k, v in details.items() if v is not None}

    return NormalizedAlert(
        source="docker_events",
        status=status,
        severity=severity,
        service_name=service_name,
        message=message,
        details=details,
    )


# ---------------------------------------------------------------------------
# Glances (via glances_poller.py sidecar — Glances does not push webhooks)
# ---------------------------------------------------------------------------

_GLANCES_STATE_MAP = {
    "careful":  ("warning", "warning"),
    "warning":  ("warning", "warning"),
    "critical": ("down",    "critical"),
    "ok":       ("up",      "info"),
}


def _is_glances(data: dict[str, Any]) -> bool:
    # Custom keys set by glances_poller.py — unambiguous prefix
    return "glances_host" in data and "glances_type" in data


def _parse_glances(data: dict[str, Any]) -> NormalizedAlert:
    raw_state = str(data.get("glances_state", "")).lower()
    status, severity = _GLANCES_STATE_MAP.get(raw_state, ("unknown", "warning"))

    hostname = data.get("glances_host", "unknown-host")
    metric_type = data.get("glances_type", "unknown")
    value = data.get("glances_value")
    units = {"cpu": "%", "mem": "%", "memswap": "%", "load": "", "fs": "%", "diskio": "B/s", "network": "B/s"}.get(metric_type, "")

    service_name = f"{hostname}: {metric_type}"
    if value is not None:
        message = f"{metric_type} on {hostname}: {value}{units} ({raw_state.upper()})"
    else:
        message = f"{metric_type} alert on {hostname}: {raw_state.upper()}"

    details = {
        "hostname":  hostname,
        "metric":    metric_type,
        "value":     value,
        "min":       data.get("glances_min"),
        "max":       data.get("glances_max"),
        "duration":  data.get("glances_duration"),
        "top":       data.get("glances_top") or None,
    }
    details = {k: v for k, v in details.items() if v is not None}

    return NormalizedAlert(
        source="glances",
        status=status,
        severity=severity,
        service_name=service_name,
        message=message,
        details=details,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse_alert(data: dict[str, Any]) -> NormalizedAlert:
    """
    Detect payload format and return a NormalizedAlert.

    Detection order (most specific first):
       1. Uptime Kuma    — heartbeat + monitor keys
       2. Grafana        — alerts list + groupLabels + orgId
       3. Alertmanager   — alerts list + receiver + groupLabels (no orgId)
       4. Healthchecks   — check_id + slug
       5. Netdata        — alarm + chart + hostname
       6. Zabbix         — trigger_name + trigger_severity
       7. Checkmk        — NOTIFICATIONTYPE + HOSTNAME (ALL_CAPS)
       8. WUD            — updateAvailable + image
       9. Docker Events  — Type + Action + Actor (Portainer, Docker API)
      10. Glances        — glances_host + glances_type (via poller sidecar)
      11. Generic        — fallback for everything else
    """
    if _is_uptime_kuma(data):
        return _parse_uptime_kuma(data)
    if _is_grafana(data):
        return _parse_grafana(data)
    if _is_alertmanager(data):
        return _parse_alertmanager(data)
    if _is_healthchecks(data):
        return _parse_healthchecks(data)
    if _is_netdata(data):
        return _parse_netdata(data)
    if _is_zabbix(data):
        return _parse_zabbix(data)
    if _is_checkmk(data):
        return _parse_checkmk(data)
    if _is_wud(data):
        return _parse_wud(data)
    if _is_docker_events(data):
        return _parse_docker_events(data)
    if _is_glances(data):
        return _parse_glances(data)
    return _parse_generic(data)
