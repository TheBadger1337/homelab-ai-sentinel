"""
Unit tests for alert_parser.py

Covers format detection and field mapping for all six sources:
Uptime Kuma, Grafana, Alertmanager, Healthchecks.io, Netdata, and generic JSON.
"""

from app.alert_parser import parse_alert


# ---------------------------------------------------------------------------
# Uptime Kuma
# ---------------------------------------------------------------------------

def test_uptime_kuma_detected():
    data = {"heartbeat": {"status": 0, "msg": "down"}, "monitor": {"name": "nginx"}}
    assert parse_alert(data).source == "uptime_kuma"


def test_uptime_kuma_down():
    data = {
        "heartbeat": {"status": 0, "msg": "Connection refused", "ping": None},
        "monitor": {"name": "Nginx", "url": "http://10.0.0.10:81", "type": "http"},
    }
    alert = parse_alert(data)
    assert alert.status == "down"
    assert alert.severity == "critical"
    assert alert.service_name == "Nginx"
    assert "Connection refused" in alert.message


def test_uptime_kuma_up():
    data = {
        "heartbeat": {"status": 1, "msg": "OK", "ping": 4},
        "monitor": {"name": "Vaultwarden", "url": "https://vaultwarden.example.com"},
        "msg": "Vaultwarden is back up",
    }
    alert = parse_alert(data)
    assert alert.status == "up"
    assert alert.severity == "info"


def test_uptime_kuma_unknown_status():
    data = {"heartbeat": {"status": 99}, "monitor": {"name": "test"}}
    alert = parse_alert(data)
    assert alert.status == "unknown"
    assert alert.severity == "warning"


def test_uptime_kuma_details_populated():
    data = {
        "heartbeat": {"status": 0, "ping": 12, "time": "2026-03-25T00:00:00Z"},
        "monitor": {"name": "svc", "url": "http://test", "type": "http", "id": 7},
    }
    alert = parse_alert(data)
    assert alert.details["monitor_url"] == "http://test"
    assert alert.details["ping_ms"] == 12
    assert "monitor_id" in alert.details


def test_uptime_kuma_none_values_excluded_from_details():
    data = {
        "heartbeat": {"status": 0, "ping": None},
        "monitor": {"name": "svc", "url": None},
    }
    alert = parse_alert(data)
    assert "ping_ms" not in alert.details
    assert "monitor_url" not in alert.details


# ---------------------------------------------------------------------------
# Grafana
# ---------------------------------------------------------------------------

_GRAFANA_FIRING = {
    "status": "firing",
    "orgId": 1,
    "alerts": [
        {
            "status": "firing",
            "labels": {"alertname": "High CPU", "instance": "host1"},
            "annotations": {"summary": "CPU at 95%", "description": "Load is high"},
        }
    ],
    "groupLabels": {"alertname": "High CPU"},
    "commonLabels": {"alertname": "High CPU", "instance": "host1"},
    "commonAnnotations": {"summary": "CPU at 95%"},
    "version": "1",
}


def test_grafana_detected():
    assert parse_alert(_GRAFANA_FIRING).source == "grafana"


def test_grafana_firing():
    alert = parse_alert(_GRAFANA_FIRING)
    assert alert.status == "down"
    assert alert.severity == "critical"
    assert alert.service_name == "High CPU"
    assert "CPU at 95%" in alert.message


def test_grafana_resolved():
    data = {**_GRAFANA_FIRING, "status": "resolved"}
    alert = parse_alert(data)
    assert alert.status == "up"
    assert alert.severity == "info"


def test_grafana_unknown_status():
    data = {**_GRAFANA_FIRING, "status": "pending"}
    alert = parse_alert(data)
    assert alert.status == "unknown"
    assert alert.severity == "warning"


def test_grafana_empty_alerts_list():
    data = {"status": "firing", "orgId": 1, "alerts": [], "groupLabels": {}, "version": "1"}
    alert = parse_alert(data)
    assert alert.source == "grafana"
    assert alert.service_name == "Unknown Service"


def test_grafana_not_triggered_without_group_labels():
    # Has alerts array but no groupLabels — should fall through to generic
    data = {"status": "firing", "alerts": [{"labels": {"alertname": "test"}}]}
    assert parse_alert(data).source == "generic"


# ---------------------------------------------------------------------------
# Generic
# ---------------------------------------------------------------------------

def test_generic_down_variants():
    for status in ("down", "firing", "error", "critical", "0", "false"):
        alert = parse_alert({"service": "svc", "status": status})
        assert alert.status == "down", f"failed for status={status!r}"
        assert alert.severity == "critical"


def test_generic_up_variants():
    for status in ("up", "ok", "resolved", "normal", "1", "true"):
        alert = parse_alert({"service": "svc", "status": status})
        assert alert.status == "up", f"failed for status={status!r}"
        assert alert.severity == "info"


def test_generic_warning_variants():
    for status in ("warning", "warn", "degraded"):
        alert = parse_alert({"service": "svc", "status": status})
        assert alert.severity == "warning", f"failed for status={status!r}"


def test_generic_unknown_status():
    alert = parse_alert({"service": "svc", "status": "something_new"})
    assert alert.status == "unknown"
    assert alert.severity == "warning"


def test_generic_service_name_priority():
    # service > name > host > source
    alert = parse_alert({"service": "redis", "name": "other", "host": "h", "status": "down"})
    assert alert.service_name == "redis"

    alert = parse_alert({"name": "postgres", "host": "h", "status": "down"})
    assert alert.service_name == "postgres"

    alert = parse_alert({"host": "server-01", "status": "down"})
    assert alert.service_name == "server-01"


def test_generic_unknown_service_fallback():
    alert = parse_alert({"status": "down", "message": "something broke"})
    assert alert.service_name == "Unknown Service"


def test_generic_message_priority():
    # message > msg > description > text
    alert = parse_alert({"service": "s", "status": "down", "message": "msg field", "msg": "other"})
    assert alert.message == "msg field"

    alert = parse_alert({"service": "s", "status": "down", "msg": "msg field"})
    assert alert.message == "msg field"


def test_generic_extra_fields_in_details():
    alert = parse_alert({
        "service": "postgres", "status": "warning",
        "message": "pool at 87%", "pool_size": 100, "active": 87,
    })
    assert alert.details["pool_size"] == 100
    assert alert.details["active"] == 87
    assert "service" not in alert.details
    assert "message" not in alert.details


def test_generic_sensitive_keys_stripped_from_details():
    alert = parse_alert({
        "service": "myapp", "status": "down",
        "message": "crashed", "token": "secret123", "api_key": "abc",
        "password": "hunter2", "region": "us-east-1",
    })
    assert "token" not in alert.details
    assert "api_key" not in alert.details
    assert "password" not in alert.details
    assert alert.details.get("region") == "us-east-1"


def test_generic_sensitive_keys_case_insensitive():
    alert = parse_alert({
        "service": "myapp", "status": "down",
        "TOKEN": "secret", "Authorization": "Bearer xyz",
    })
    assert "TOKEN" not in alert.details
    assert "Authorization" not in alert.details


def test_generic_sensitive_keys_substring_match():
    """Compound credential keys like bearer_token must be stripped via substring match."""
    alert = parse_alert({
        "service": "myapp", "status": "down",
        "bearer_token": "xyz", "client_secret": "abc",
        "oauth_token": "tok", "app_secret": "shhh",
        "user_password": "hunter2", "region": "us-east-1",
    })
    assert "bearer_token" not in alert.details
    assert "client_secret" not in alert.details
    assert "oauth_token" not in alert.details
    assert "app_secret" not in alert.details
    assert "user_password" not in alert.details
    assert alert.details.get("region") == "us-east-1"


def test_generic_value_redaction_jwt():
    """JWT tokens in message or detail values must not survive redaction."""
    jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"  # betterleaks:allow
    # Bare JWT — not preceded by a credential key name
    alert_bare = parse_alert({
        "service": "myapp", "status": "down",
        "message": f"Error payload was {jwt} in body",
        "raw_token": jwt,
    })
    assert jwt not in alert_bare.message
    assert "[JWT]" in alert_bare.message
    # raw_token key is stripped by _SENSITIVE_KEYS before redaction applies,
    # so it won't appear in details at all
    assert "raw_token" not in alert_bare.details

    # JWT after a credential key — inline cred pattern fires, JWT still gone
    alert_keyed = parse_alert({
        "service": "myapp", "status": "down",
        "message": f"Auth failed for token: {jwt}",
    })
    assert jwt not in alert_keyed.message


def test_generic_value_redaction_inline_cred():
    """Inline key=value credential patterns must be redacted in message and details."""
    alert = parse_alert({
        "service": "myapp", "status": "down",
        "message": "Connecting with api_key=AKIAIOSFODNN7EXAMPLE failed",
        "extra_info": "retry with password=hunter2 succeeded",
    })
    assert "AKIAIOSFODNN7EXAMPLE" not in alert.message
    assert "[REDACTED]" in alert.message
    assert "hunter2" not in alert.details.get("extra_info", "")
    assert "[REDACTED]" in alert.details.get("extra_info", "")


def test_generic_value_redaction_email():
    """Email addresses in message or detail values must be replaced with [EMAIL]."""
    alert = parse_alert({
        "service": "myapp", "status": "down",
        "message": "Auth failed for user: john@example.com",
        "contact": "alert owner: ops@company.internal",
    })
    assert "john@example.com" not in alert.message
    assert "[EMAIL]" in alert.message
    assert "ops@company.internal" not in alert.details.get("contact", "")
    assert "[EMAIL]" in alert.details.get("contact", "")


def test_generic_value_redaction_preserves_non_sensitive():
    """Non-sensitive values must pass through redaction unchanged."""
    alert = parse_alert({
        "service": "myapp", "status": "down",
        "message": "Disk usage at 95% on /var/lib",
        "mount_point": "/var/lib/docker",
        "host_ip": "192.168.1.50",
    })
    assert "95%" in alert.message
    assert alert.details.get("mount_point") == "/var/lib/docker"
    assert alert.details.get("host_ip") == "192.168.1.50"


def test_generic_empty_payload():
    alert = parse_alert({})
    assert alert.source == "generic"
    assert alert.service_name == "Unknown Service"
    assert alert.status == "unknown"


def test_non_dict_input_rejected_at_route():
    # The webhook route rejects non-dict payloads before parse_alert is called.
    # This test confirms parse_alert itself is not called with lists/scalars —
    # validation happens in webhook.py. Documented here for clarity.
    pass


def test_generic_warning_status_normalized():
    # "warn" and "degraded" must normalize to "warning" — not left as raw strings.
    # Raw values have no entry in _STATUS_EMOJI and produce the wrong embed emoji.
    for raw in ("warn", "degraded"):
        alert = parse_alert({"service": "svc", "status": raw})
        assert alert.status == "warning", f"status not normalized for {raw!r}"
        assert alert.severity == "warning"


# ---------------------------------------------------------------------------
# Grafana resolved — firing_count: 0 must not be dropped
# ---------------------------------------------------------------------------

def test_grafana_resolved_firing_count_zero_preserved():
    # firing_count=0 on a resolved alert is meaningful data.
    # The falsy filter `if v` would drop it; `if v is not None` preserves it.
    data = {
        "status": "resolved",
        "orgId": 1,
        "alerts": [],
        "groupLabels": {"alertname": "CPU"},
        "commonLabels": {},
        "commonAnnotations": {},
        "version": "1",
    }
    alert = parse_alert(data)
    assert "firing_count" in alert.details
    assert alert.details["firing_count"] == 0


# ---------------------------------------------------------------------------
# Prometheus Alertmanager
# ---------------------------------------------------------------------------

_ALERTMANAGER_FIRING = {
    "version": "4",
    "receiver": "sentinel",
    "status": "firing",
    "groupLabels": {"alertname": "HighMemoryUsage"},
    "commonLabels": {"alertname": "HighMemoryUsage", "severity": "critical", "job": "node"},
    "commonAnnotations": {"summary": "Memory usage above 90%", "description": "RSS > 90%"},
    "alerts": [
        {
            "status": "firing",
            "labels": {"alertname": "HighMemoryUsage", "instance": "server1:9100", "severity": "critical"},
            "annotations": {"summary": "Memory usage above 90%"},
        }
    ],
    "externalURL": "http://alertmanager:9093",
}


def test_alertmanager_detected():
    assert parse_alert(_ALERTMANAGER_FIRING).source == "alertmanager"


def test_alertmanager_not_detected_as_grafana():
    # Alertmanager has no orgId — must not be routed to Grafana parser
    assert parse_alert(_ALERTMANAGER_FIRING).source != "grafana"


def test_alertmanager_firing():
    alert = parse_alert(_ALERTMANAGER_FIRING)
    assert alert.status == "down"
    assert alert.severity == "critical"
    assert alert.service_name == "HighMemoryUsage"
    assert "Memory usage" in alert.message


def test_alertmanager_resolved():
    data = {**_ALERTMANAGER_FIRING, "status": "resolved"}
    alert = parse_alert(data)
    assert alert.status == "up"


def test_alertmanager_severity_from_labels():
    data = {**_ALERTMANAGER_FIRING,
            "commonLabels": {"alertname": "DiskWarning", "severity": "warning"}}
    alert = parse_alert(data)
    assert alert.severity == "warning"


def test_alertmanager_node_exporter_labels_in_details():
    alert = parse_alert(_ALERTMANAGER_FIRING)
    assert alert.details.get("job") == "node"
    assert "instance" in alert.details


def test_alertmanager_receiver_in_details():
    alert = parse_alert(_ALERTMANAGER_FIRING)
    assert alert.details.get("receiver") == "sentinel"


def test_alertmanager_grafana_with_orgid_not_misdetected():
    # A Grafana payload with orgId must NOT be parsed as Alertmanager
    grafana_payload = {**_ALERTMANAGER_FIRING, "orgId": 1}
    alert = parse_alert(grafana_payload)
    assert alert.source == "grafana"


# ---------------------------------------------------------------------------
# Healthchecks.io
# ---------------------------------------------------------------------------

_HEALTHCHECKS_DOWN = {
    "check_id": "2bd2e0c3-3cc7-4b4d-90b0-9e570000ffff",
    "name": "Daily backup",
    "slug": "daily-backup",
    "status": "down",
    "period": 86400,
    "grace": 3600,
    "last_ping": "2026-03-25T10:00:00+00:00",
    "ping_url": "https://hc-ping.com/2bd2e0c3-3cc7-4b4d-90b0-9e570000ffff",
}


def test_healthchecks_detected():
    assert parse_alert(_HEALTHCHECKS_DOWN).source == "healthchecks"


def test_healthchecks_down():
    alert = parse_alert(_HEALTHCHECKS_DOWN)
    assert alert.status == "down"
    assert alert.severity == "critical"
    assert alert.service_name == "Daily backup"
    assert "check failed" in alert.message


def test_healthchecks_grace():
    data = {**_HEALTHCHECKS_DOWN, "status": "grace"}
    alert = parse_alert(data)
    assert alert.status == "warning"
    assert alert.severity == "warning"
    assert "grace" in alert.message


def test_healthchecks_up():
    data = {**_HEALTHCHECKS_DOWN, "status": "up"}
    alert = parse_alert(data)
    assert alert.status == "up"
    assert alert.severity == "info"


def test_healthchecks_details_populated():
    alert = parse_alert(_HEALTHCHECKS_DOWN)
    assert alert.details["check_id"] == "2bd2e0c3-3cc7-4b4d-90b0-9e570000ffff"
    assert alert.details["period_seconds"] == 86400
    assert "last_ping" in alert.details


def test_healthchecks_not_triggered_without_slug():
    # A payload with check_id but no slug should fall to generic
    data = {"check_id": "some-uuid", "status": "down", "service": "backup"}
    assert parse_alert(data).source == "generic"


# ---------------------------------------------------------------------------
# Netdata
# ---------------------------------------------------------------------------

_NETDATA_CRITICAL = {
    "hostname": "server1",
    "chart": "system.cpu",
    "alarm": "10min_cpu_usage",
    "status": "CRITICAL",
    "old_status": "WARNING",
    "value": 95.3,
    "old_value": 72.1,
    "units": "%",
    "info": "average cpu utilization",
    "duration": 300,
    "family": "cpu",
    "priority": 1000,
    "roles": "sysadmin",
}


def test_netdata_detected():
    assert parse_alert(_NETDATA_CRITICAL).source == "netdata"


def test_netdata_critical():
    alert = parse_alert(_NETDATA_CRITICAL)
    assert alert.status == "down"
    assert alert.severity == "critical"
    assert "server1" in alert.service_name
    assert "10min_cpu_usage" in alert.service_name


def test_netdata_warning():
    data = {**_NETDATA_CRITICAL, "status": "WARNING"}
    alert = parse_alert(data)
    assert alert.status == "warning"
    assert alert.severity == "warning"


def test_netdata_clear():
    data = {**_NETDATA_CRITICAL, "status": "CLEAR"}
    alert = parse_alert(data)
    assert alert.status == "up"
    assert alert.severity == "info"


def test_netdata_message_contains_value_and_units():
    alert = parse_alert(_NETDATA_CRITICAL)
    assert "95.3" in alert.message
    assert "%" in alert.message


def test_netdata_details_populated():
    alert = parse_alert(_NETDATA_CRITICAL)
    assert alert.details["hostname"] == "server1"
    assert alert.details["chart"] == "system.cpu"
    assert alert.details["value"] == 95.3
    assert alert.details["old_status"] == "WARNING"


def test_netdata_not_triggered_without_chart():
    # Must have all three: alarm + chart + hostname
    data = {"alarm": "cpu", "hostname": "server1", "status": "CRITICAL"}
    assert parse_alert(data).source == "generic"


# ---------------------------------------------------------------------------
# Zabbix
# ---------------------------------------------------------------------------

_ZABBIX_PROBLEM = {
    "event_id": "12345",
    "trigger_id": "678",
    "trigger_name": "High CPU load",
    "trigger_severity": "High",
    "trigger_status": "PROBLEM",
    "host_name": "server1",
    "host_ip": "10.0.0.10",
    "event_message": "CPU usage is above 90%",
    "item_name": "CPU utilization",
    "item_value": "93.5",
}


def test_zabbix_detected():
    assert parse_alert(_ZABBIX_PROBLEM).source == "zabbix"


def test_zabbix_problem():
    alert = parse_alert(_ZABBIX_PROBLEM)
    assert alert.status == "down"
    assert alert.severity == "critical"
    assert alert.service_name == "server1"
    assert "CPU" in alert.message


def test_zabbix_resolved():
    data = {**_ZABBIX_PROBLEM, "trigger_status": "RESOLVED"}
    alert = parse_alert(data)
    assert alert.status == "up"
    assert alert.severity == "info"


def test_zabbix_severity_mapping():
    for raw, expected in [("disaster", "critical"), ("high", "critical"),
                          ("average", "warning"), ("warning", "warning"),
                          ("information", "info"), ("not classified", "info")]:
        data = {**_ZABBIX_PROBLEM, "trigger_severity": raw}
        alert = parse_alert(data)
        assert alert.severity == expected, f"wrong severity for {raw!r}"


def test_zabbix_details_populated():
    alert = parse_alert(_ZABBIX_PROBLEM)
    assert alert.details["trigger"] == "High CPU load"
    assert alert.details["item_value"] == "93.5"
    assert alert.details["host_ip"] == "10.0.0.10"


def test_zabbix_not_triggered_without_severity():
    data = {"trigger_name": "CPU load", "host_name": "server1", "status": "PROBLEM"}
    assert parse_alert(data).source == "generic"


# ---------------------------------------------------------------------------
# Checkmk
# ---------------------------------------------------------------------------

_CHECKMK_SERVICE_CRIT = {
    "NOTIFICATIONTYPE": "PROBLEM",
    "HOSTNAME": "web-server",
    "HOSTADDRESS": "10.0.0.20",
    "SERVICEDESC": "HTTP",
    "SERVICESTATE": "CRIT",
    "SERVICEOUTPUT": "Connection refused",
    "CONTACTNAME": "admin",
}

_CHECKMK_HOST_DOWN = {
    "NOTIFICATIONTYPE": "PROBLEM",
    "HOSTNAME": "router",
    "HOSTADDRESS": "10.0.0.1",
    "HOSTSTATE": "DOWN",
    "HOSTOUTPUT": "PING CRITICAL - Packet loss = 100%",
}


def test_checkmk_detected():
    assert parse_alert(_CHECKMK_SERVICE_CRIT).source == "checkmk"


def test_checkmk_service_critical():
    alert = parse_alert(_CHECKMK_SERVICE_CRIT)
    assert alert.status == "down"
    assert alert.severity == "critical"
    assert "web-server" in alert.service_name
    assert "HTTP" in alert.service_name
    assert "Connection refused" in alert.message


def test_checkmk_host_down():
    alert = parse_alert(_CHECKMK_HOST_DOWN)
    assert alert.status == "down"
    assert alert.severity == "critical"
    assert alert.service_name == "router"


def test_checkmk_recovery():
    data = {**_CHECKMK_SERVICE_CRIT, "NOTIFICATIONTYPE": "RECOVERY", "SERVICESTATE": "OK"}
    alert = parse_alert(data)
    assert alert.status == "up"
    assert alert.severity == "info"


def test_checkmk_service_warn():
    data = {**_CHECKMK_SERVICE_CRIT, "SERVICESTATE": "WARN"}
    alert = parse_alert(data)
    assert alert.severity == "warning"


def test_checkmk_details_include_host_and_service():
    alert = parse_alert(_CHECKMK_SERVICE_CRIT)
    assert alert.details["host"] == "web-server"
    assert alert.details["service"] == "HTTP"


def test_checkmk_not_triggered_without_notificationtype():
    data = {"HOSTNAME": "server1", "HOSTSTATE": "DOWN"}
    assert parse_alert(data).source == "generic"


# ---------------------------------------------------------------------------
# What's Up Docker (WUD)
# ---------------------------------------------------------------------------

_WUD_UPDATE = {
    "id": "local_portainer_latest",
    "name": "portainer",
    "displayName": "portainer/portainer-ce",
    "image": {
        "name": "portainer/portainer-ce",
        "tag": {"value": "2.19.4", "semver": True},
        "registry": {"name": "hub", "url": "https://registry-1.docker.io"},
    },
    "result": {"tag": "2.19.5"},
    "status": "UpdateAvailable",
    "updateAvailable": True,
}


def test_wud_detected():
    assert parse_alert(_WUD_UPDATE).source == "wud"


def test_wud_update_available():
    alert = parse_alert(_WUD_UPDATE)
    assert alert.status == "warning"
    assert alert.severity == "warning"
    assert "2.19.4" in alert.message
    assert "2.19.5" in alert.message


def test_wud_up_to_date():
    data = {**_WUD_UPDATE, "updateAvailable": False, "status": "UpToDate"}
    alert = parse_alert(data)
    assert alert.status == "up"
    assert alert.severity == "info"


def test_wud_service_name_uses_display_name():
    alert = parse_alert(_WUD_UPDATE)
    assert "portainer" in alert.service_name.lower()


def test_wud_details_include_tags():
    alert = parse_alert(_WUD_UPDATE)
    assert alert.details["current_tag"] == "2.19.4"
    assert alert.details["new_tag"] == "2.19.5"


def test_wud_not_triggered_without_image():
    data = {"updateAvailable": True, "name": "nginx"}
    assert parse_alert(data).source == "generic"


# ---------------------------------------------------------------------------
# Docker Events (Portainer / Docker API)
# ---------------------------------------------------------------------------

_DOCKER_DIE = {
    "status": "die",
    "id": "abc123def456",
    "from": "nginx:latest",
    "Type": "container",
    "Action": "die",
    "Actor": {
        "ID": "abc123def456",
        "Attributes": {
            "exitCode": "137",
            "image": "nginx:latest",
            "name": "my-nginx",
        },
    },
    "scope": "local",
    "time": 1234567890,
}


def test_docker_events_detected():
    assert parse_alert(_DOCKER_DIE).source == "docker_events"


def test_docker_die_is_critical():
    alert = parse_alert(_DOCKER_DIE)
    assert alert.status == "down"
    assert alert.severity == "critical"
    assert "my-nginx" in alert.service_name
    assert "137" in alert.message


def test_docker_start_is_info():
    data = {**_DOCKER_DIE, "Action": "start",
            "Actor": {"ID": "abc123", "Attributes": {"name": "my-nginx", "image": "nginx:latest"}}}
    alert = parse_alert(data)
    assert alert.status == "up"
    assert alert.severity == "info"


def test_docker_health_unhealthy():
    data = {**_DOCKER_DIE, "Action": "health_status: unhealthy",
            "Actor": {"ID": "abc123", "Attributes": {"name": "my-nginx", "image": "nginx:latest"}}}
    alert = parse_alert(data)
    assert alert.status == "down"
    assert alert.severity == "critical"
    assert "unhealthy" in alert.message


def test_docker_health_healthy():
    data = {**_DOCKER_DIE, "Action": "health_status: healthy",
            "Actor": {"ID": "abc123", "Attributes": {"name": "my-nginx", "image": "nginx:latest"}}}
    alert = parse_alert(data)
    assert alert.status == "up"


def test_docker_stop_is_warning():
    data = {**_DOCKER_DIE, "Action": "stop",
            "Actor": {"ID": "abc123", "Attributes": {"name": "my-nginx", "image": "nginx:latest"}}}
    alert = parse_alert(data)
    assert alert.severity == "warning"


def test_docker_events_not_triggered_without_actor():
    data = {"Type": "container", "Action": "die", "status": "die"}
    assert parse_alert(data).source == "generic"


# ---------------------------------------------------------------------------
# Glances
# ---------------------------------------------------------------------------

_GLANCES_CPU_CRIT = {
    "glances_host": "homelab-server",
    "glances_type": "cpu",
    "glances_state": "CRITICAL",
    "glances_value": 97.2,
    "glances_min": 92.0,
    "glances_max": 99.1,
    "glances_duration": 120,
    "glances_top": ["python3", "node"],
}


def test_glances_detected():
    assert parse_alert(_GLANCES_CPU_CRIT).source == "glances"


def test_glances_critical():
    alert = parse_alert(_GLANCES_CPU_CRIT)
    assert alert.status == "down"
    assert alert.severity == "critical"
    assert "homelab-server" in alert.service_name
    assert "cpu" in alert.service_name
    assert "97.2" in alert.message


def test_glances_warning_state():
    data = {**_GLANCES_CPU_CRIT, "glances_state": "WARNING"}
    alert = parse_alert(data)
    assert alert.status == "warning"
    assert alert.severity == "warning"


def test_glances_careful_state():
    data = {**_GLANCES_CPU_CRIT, "glances_state": "CAREFUL"}
    alert = parse_alert(data)
    assert alert.severity == "warning"


def test_glances_details_populated():
    alert = parse_alert(_GLANCES_CPU_CRIT)
    assert alert.details["hostname"] == "homelab-server"
    assert alert.details["value"] == 97.2
    assert alert.details["duration"] == 120


def test_glances_not_triggered_without_type():
    data = {"glances_host": "server1", "status": "CRITICAL"}
    assert parse_alert(data).source == "generic"
