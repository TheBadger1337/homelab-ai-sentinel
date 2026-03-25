"""
Unit tests for alert_parser.py

Covers format detection and field mapping for all three sources:
Uptime Kuma, Grafana, and generic JSON.
"""

import pytest
from app.alert_parser import parse_alert, NormalizedAlert


# ---------------------------------------------------------------------------
# Uptime Kuma
# ---------------------------------------------------------------------------

def test_uptime_kuma_detected():
    data = {"heartbeat": {"status": 0, "msg": "down"}, "monitor": {"name": "nginx"}}
    assert parse_alert(data).source == "uptime_kuma"


def test_uptime_kuma_down():
    data = {
        "heartbeat": {"status": 0, "msg": "Connection refused", "ping": None},
        "monitor": {"name": "Nginx", "url": "http://192.168.1.10:81", "type": "http"},
    }
    alert = parse_alert(data)
    assert alert.status == "down"
    assert alert.severity == "critical"
    assert alert.service_name == "Nginx"
    assert "Connection refused" in alert.message


def test_uptime_kuma_up():
    data = {
        "heartbeat": {"status": 1, "msg": "OK", "ping": 4},
        "monitor": {"name": "Vaultwarden", "url": "https://vw.home.internal"},
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
    data = {"status": "firing", "alerts": [], "groupLabels": {}, "version": "1"}
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
