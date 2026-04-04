"""
Tests for app/pulse.py — Homelab Pulse frequency stats.
"""

import time
from unittest.mock import patch

import pytest

from app.alert_db import init_db, log_alert
from app.alert_parser import NormalizedAlert
from app.pulse import get_pulse, format_pulse


def _make_alert(service="nginx", status="down", severity="critical", message="fail"):
    return NormalizedAlert(
        source="generic",
        service_name=service,
        status=status,
        severity=severity,
        message=message,
        details={},
    )


@pytest.fixture(autouse=True)
def _use_tmp_db(tmp_path, monkeypatch):
    """Point the DB to a temporary directory for each test."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("DB_PATH", db_path)
    import app.alert_db as db_mod
    if hasattr(db_mod._local, "conn"):
        del db_mod._local.conn
    init_db()
    yield
    if hasattr(db_mod._local, "conn"):
        try:
            db_mod._local.conn.close()
        except Exception:
            pass
        del db_mod._local.conn


# ---------------------------------------------------------------------------
# get_pulse
# ---------------------------------------------------------------------------

def test_no_history_returns_none():
    assert get_pulse("nonexistent-service") is None


def test_single_alert_returns_counts():
    alert = _make_alert()
    log_alert(alert, None, notified=True)
    pulse = get_pulse("nginx")
    assert pulse is not None
    assert pulse["count_1h"] == 1
    assert pulse["count_24h"] == 1
    assert pulse["count_7d"] == 1
    assert pulse["avg_interval"] is None  # need >= 2 alerts for interval


def test_multiple_alerts_compute_interval():
    alert = _make_alert()
    log_alert(alert, None, notified=True)
    log_alert(alert, None, notified=True)
    log_alert(alert, None, notified=True)
    pulse = get_pulse("nginx")
    assert pulse is not None
    assert pulse["count_1h"] == 3
    assert pulse["count_24h"] == 3
    assert pulse["avg_interval"] is not None
    assert pulse["avg_interval"] >= 0


def test_different_services_isolated():
    log_alert(_make_alert(service="nginx"), None, notified=True)
    log_alert(_make_alert(service="redis"), None, notified=True)
    log_alert(_make_alert(service="redis"), None, notified=True)
    nginx_pulse = get_pulse("nginx")
    redis_pulse = get_pulse("redis")
    assert nginx_pulse["count_1h"] == 1
    assert redis_pulse["count_1h"] == 2


def test_rate_change_high_frequency():
    """Simulate many alerts to trigger rate_change detection."""
    alert = _make_alert()
    # Log 10 alerts — all within the last hour, so 24h count = 10.
    # 7d count = 10, daily avg = 10/7 ≈ 1.4. Ratio = 10/1.4 ≈ 7x.
    for _ in range(10):
        log_alert(alert, None, notified=True)
    pulse = get_pulse("nginx")
    assert pulse is not None
    assert pulse["rate_change"] is not None
    assert "above" in pulse["rate_change"]


def test_db_error_returns_none(monkeypatch):
    """Pulse must return None on DB failure, never raise."""
    with patch("app.pulse._get_conn", side_effect=Exception("db gone")):
        assert get_pulse("nginx") is None


# ---------------------------------------------------------------------------
# format_pulse
# ---------------------------------------------------------------------------

def test_format_pulse_none():
    assert format_pulse(None) == ""


def test_format_pulse_empty_dict():
    assert format_pulse({}) == ""


def test_format_pulse_basic():
    pulse = {
        "count_1h": 3,
        "count_24h": 10,
        "count_7d": 25,
        "avg_interval": None,
        "rate_change": None,
    }
    result = format_pulse(pulse)
    assert "3 in the last hour" in result
    assert "10 in 24h" in result
    assert "25 in 7 days" in result


def test_format_pulse_with_interval_seconds():
    pulse = {
        "count_1h": 5,
        "count_24h": 5,
        "count_7d": 5,
        "avg_interval": 30.0,
        "rate_change": None,
    }
    result = format_pulse(pulse)
    assert "30s between alerts" in result


def test_format_pulse_with_interval_minutes():
    pulse = {
        "count_1h": 2,
        "count_24h": 2,
        "count_7d": 2,
        "avg_interval": 300.0,
        "rate_change": None,
    }
    result = format_pulse(pulse)
    assert "5m between alerts" in result


def test_format_pulse_with_interval_hours():
    pulse = {
        "count_1h": 1,
        "count_24h": 2,
        "count_7d": 2,
        "avg_interval": 7200.0,
        "rate_change": None,
    }
    result = format_pulse(pulse)
    assert "2.0h between alerts" in result


def test_format_pulse_with_rate_change():
    pulse = {
        "count_1h": 10,
        "count_24h": 10,
        "count_7d": 10,
        "avg_interval": None,
        "rate_change": "7x above 7-day average",
    }
    result = format_pulse(pulse)
    assert "7x above 7-day average" in result
