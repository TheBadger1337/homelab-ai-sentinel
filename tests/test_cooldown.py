"""
Tests for Phase 5: per-service notification cooldown.
"""

import json
import time

import pytest
from unittest.mock import patch, MagicMock

from app.alert_db import get_last_notified_ts, log_alert, init_db, _get_conn
from app.alert_parser import NormalizedAlert


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    init_db()


def _make_alert(service="nginx", status="down", severity="critical", message="Connection refused"):
    return NormalizedAlert(
        source="uptime_kuma",
        status=status,
        severity=severity,
        service_name=service,
        message=message,
        details={},
    )


# ---------------------------------------------------------------------------
# get_last_notified_ts
# ---------------------------------------------------------------------------

def test_no_alerts_returns_none():
    assert get_last_notified_ts("nginx") is None


def test_returns_ts_of_last_notified():
    alert = _make_alert()
    log_alert(alert, None, notified=True)
    ts = get_last_notified_ts("nginx")
    assert ts is not None
    assert abs(ts - time.time()) < 5


def test_ignores_non_notified():
    alert = _make_alert()
    log_alert(alert, None, notified=False)
    assert get_last_notified_ts("nginx") is None


def test_filters_by_service():
    log_alert(_make_alert(service="nginx"), None, notified=True)
    log_alert(_make_alert(service="redis"), None, notified=True)
    ts = get_last_notified_ts("nginx")
    assert ts is not None
    assert get_last_notified_ts("unknown") is None


def test_returns_most_recent():
    alert = _make_alert()
    log_alert(alert, None, notified=True)
    time.sleep(0.01)
    log_alert(alert, None, notified=True)
    ts = get_last_notified_ts("nginx")
    assert ts is not None


def test_db_error_returns_none(monkeypatch):
    with patch("app.alert_db._get_conn", side_effect=Exception("boom")):
        assert get_last_notified_ts("nginx") is None


# ---------------------------------------------------------------------------
# Cooldown integration via webhook
# ---------------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("SENTINEL_MODE", "minimal")
    monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
    from app import create_app
    app = create_app()
    return app.test_client()


def _post_alert(client, service="nginx", status="down"):
    return client.post(
        "/webhook",
        data=json.dumps({
            "heartbeat": {"status": 0},
            "monitor": {"name": service},
            "msg": f"{service} is {status}",
        }),
        content_type="application/json",
    )


def test_cooldown_suppresses_within_window(client, monkeypatch):
    monkeypatch.setenv("COOLDOWN_SECONDS", "300")
    monkeypatch.setenv("DEDUP_TTL_SECONDS", "0")  # disable dedup to isolate cooldown

    # First alert goes through
    r1 = _post_alert(client, service="cooldown-test")
    assert r1.status_code == 200
    assert r1.get_json()["status"] == "processed"

    # Second alert (different message) should be cooled down
    r2 = client.post(
        "/webhook",
        data=json.dumps({
            "heartbeat": {"status": 0},
            "monitor": {"name": "cooldown-test"},
            "msg": "cooldown-test different error message",
        }),
        content_type="application/json",
    )
    assert r2.status_code == 200
    assert r2.get_json()["status"] == "cooled_down"


def test_cooldown_disabled_when_zero(client, monkeypatch):
    monkeypatch.setenv("COOLDOWN_SECONDS", "0")
    monkeypatch.setenv("DEDUP_TTL_SECONDS", "0")

    r1 = _post_alert(client, service="no-cooldown")
    assert r1.get_json()["status"] == "processed"

    r2 = client.post(
        "/webhook",
        data=json.dumps({
            "heartbeat": {"status": 0},
            "monitor": {"name": "no-cooldown"},
            "msg": "no-cooldown different message",
        }),
        content_type="application/json",
    )
    assert r2.get_json()["status"] == "processed"


def test_cooldown_allows_different_services(client, monkeypatch):
    monkeypatch.setenv("COOLDOWN_SECONDS", "300")
    monkeypatch.setenv("DEDUP_TTL_SECONDS", "0")

    r1 = _post_alert(client, service="service-a")
    assert r1.get_json()["status"] == "processed"

    # Different service should not be affected
    r2 = _post_alert(client, service="service-b")
    assert r2.get_json()["status"] == "processed"
