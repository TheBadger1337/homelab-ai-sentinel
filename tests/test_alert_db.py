"""
Unit tests for alert_db.py — SQLite alert log.
"""

import time

import pytest

import app.alert_db as adb
from app.alert_parser import NormalizedAlert


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """Each test gets its own SQLite file — prevents cross-test contamination."""
    db_file = str(tmp_path / "test_sentinel.db")
    monkeypatch.setenv("DB_PATH", db_file)
    # Force reconnect to the new path
    adb._local.conn = None
    adb.init_db()
    yield
    if getattr(adb._local, "conn", None) is not None:
        try:
            adb._local.conn.close()
        except Exception:
            pass
        adb._local.conn = None


def _make_alert(**kwargs) -> NormalizedAlert:
    defaults = dict(
        source="generic",
        status="down",
        severity="critical",
        service_name="nginx",
        message="Connection refused",
        details={},
    )
    defaults.update(kwargs)
    return NormalizedAlert(**defaults)


# ---------------------------------------------------------------------------
# init_db
# ---------------------------------------------------------------------------

def test_init_db_creates_table():
    conn = adb._get_conn()
    result = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='alerts'"
    ).fetchone()
    assert result is not None


def test_init_db_creates_index():
    conn = adb._get_conn()
    result = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_service_ts'"
    ).fetchone()
    assert result is not None


def test_init_db_is_idempotent():
    # Calling init_db twice must not raise or duplicate anything
    adb.init_db()
    conn = adb._get_conn()
    count = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='alerts'"
    ).fetchone()[0]
    assert count == 1


# ---------------------------------------------------------------------------
# log_alert
# ---------------------------------------------------------------------------

def test_log_alert_stores_record():
    alert = _make_alert()
    ai = {"insight": "nginx crashed", "suggested_actions": ["check logs"]}
    adb.log_alert(alert, ai, notified=True)

    conn = adb._get_conn()
    row = conn.execute("SELECT * FROM alerts").fetchone()
    assert row["service"] == "nginx"
    assert row["status"] == "down"
    assert row["severity"] == "critical"
    assert row["message"] == "Connection refused"
    assert row["insight"] == "nginx crashed"
    assert row["notified"] == 1


def test_log_alert_notified_false():
    alert = _make_alert(severity="info")
    adb.log_alert(alert, None, notified=False)

    conn = adb._get_conn()
    row = conn.execute("SELECT notified, insight FROM alerts").fetchone()
    assert row["notified"] == 0
    assert row["insight"] is None


def test_log_alert_with_details():
    alert = _make_alert(details={"cpu": 95, "host": "server1"})
    adb.log_alert(alert, None, notified=True)

    import json
    conn = adb._get_conn()
    row = conn.execute("SELECT details FROM alerts").fetchone()
    stored = json.loads(row["details"])
    assert stored["cpu"] == 95
    assert stored["host"] == "server1"


def test_log_alert_no_ai_result():
    alert = _make_alert()
    adb.log_alert(alert, None, notified=True)

    conn = adb._get_conn()
    row = conn.execute("SELECT insight, actions FROM alerts").fetchone()
    assert row["insight"] is None
    assert row["actions"] is None


def test_log_alert_does_not_raise_on_db_failure(monkeypatch):
    monkeypatch.setattr(adb, "_get_conn", lambda: (_ for _ in ()).throw(Exception("db error")))
    # Must not raise
    adb.log_alert(_make_alert(), None, notified=True)


# ---------------------------------------------------------------------------
# get_recent_alerts
# ---------------------------------------------------------------------------

def test_get_recent_alerts_returns_most_recent_first():
    now = time.time()
    conn = adb._get_conn()
    for i in range(3):
        conn.execute(
            "INSERT INTO alerts (ts, source, service, status, severity, message, notified) "
            "VALUES (?, 'generic', 'nginx', 'down', 'critical', ?, 1)",
            (now - (3 - i) * 60, f"error {i}"),
        )
    conn.commit()

    results = adb.get_recent_alerts("nginx")
    assert len(results) == 3
    assert results[0]["message"] == "error 2"  # most recent first
    assert results[2]["message"] == "error 0"


def test_get_recent_alerts_default_limit_five(monkeypatch):
    monkeypatch.delenv("ALERT_HISTORY_LIMIT", raising=False)
    monkeypatch.delenv("ALERT_HISTORY_HOURS", raising=False)
    conn = adb._get_conn()
    now = time.time()
    for i in range(7):
        conn.execute(
            "INSERT INTO alerts (ts, source, service, status, severity, message, notified) "
            "VALUES (?, 'generic', 'nginx', 'down', 'critical', 'msg', 1)",
            (now - i * 10,),
        )
    conn.commit()

    results = adb.get_recent_alerts("nginx")
    assert len(results) == 5


def test_get_recent_alerts_configurable_limit(monkeypatch):
    monkeypatch.setenv("ALERT_HISTORY_LIMIT", "3")
    monkeypatch.delenv("ALERT_HISTORY_HOURS", raising=False)
    conn = adb._get_conn()
    now = time.time()
    for i in range(6):
        conn.execute(
            "INSERT INTO alerts (ts, source, service, status, severity, message, notified) "
            "VALUES (?, 'generic', 'nginx', 'down', 'critical', 'msg', 1)",
            (now - i * 10,),
        )
    conn.commit()

    results = adb.get_recent_alerts("nginx")
    assert len(results) == 3


def test_get_recent_alerts_time_window(monkeypatch):
    monkeypatch.setenv("ALERT_HISTORY_HOURS", "1")
    conn = adb._get_conn()
    now = time.time()
    # One alert within the window, one outside
    conn.execute(
        "INSERT INTO alerts (ts, source, service, status, severity, message, notified) "
        "VALUES (?, 'generic', 'nginx', 'down', 'critical', 'recent', 1)",
        (now - 1800,),  # 30 minutes ago
    )
    conn.execute(
        "INSERT INTO alerts (ts, source, service, status, severity, message, notified) "
        "VALUES (?, 'generic', 'nginx', 'down', 'critical', 'old', 1)",
        (now - 7200,),  # 2 hours ago — outside 1h window
    )
    conn.commit()

    results = adb.get_recent_alerts("nginx")
    assert len(results) == 1
    assert results[0]["message"] == "recent"


def test_get_recent_alerts_empty_for_unknown_service():
    results = adb.get_recent_alerts("nonexistent-service")
    assert results == []


def test_get_recent_alerts_only_returns_matching_service():
    conn = adb._get_conn()
    now = time.time()
    conn.execute(
        "INSERT INTO alerts (ts, source, service, status, severity, message, notified) "
        "VALUES (?, 'generic', 'nginx', 'down', 'critical', 'nginx msg', 1)",
        (now,),
    )
    conn.execute(
        "INSERT INTO alerts (ts, source, service, status, severity, message, notified) "
        "VALUES (?, 'generic', 'postgres', 'down', 'critical', 'pg msg', 1)",
        (now,),
    )
    conn.commit()

    results = adb.get_recent_alerts("nginx")
    assert all(r["message"] == "nginx msg" for r in results)


def test_get_recent_alerts_returns_empty_on_db_failure(monkeypatch):
    monkeypatch.setattr(adb, "_get_conn", lambda: (_ for _ in ()).throw(Exception("db error")))
    results = adb.get_recent_alerts("nginx")
    assert results == []
