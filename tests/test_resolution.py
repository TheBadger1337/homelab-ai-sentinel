"""
Tests for Phase 6: resolution verification.
"""

import json
import time

import pytest
from unittest.mock import patch, MagicMock

from app.alert_db import get_outage_window, log_alert, init_db
from app.alert_parser import NormalizedAlert
import app.llm_client as lc


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
# get_outage_window
# ---------------------------------------------------------------------------

def test_outage_window_empty_when_no_alerts():
    assert get_outage_window("nginx") == []


def test_outage_window_returns_alerts_since_last_recovery():
    # Simulate: recovery -> down -> down -> (query)
    log_alert(_make_alert(status="up", severity="info", message="recovered"), None, notified=True)
    time.sleep(0.01)
    log_alert(_make_alert(status="down", message="fail 1"), None, notified=True)
    time.sleep(0.01)
    log_alert(_make_alert(status="down", message="fail 2"), None, notified=True)

    window = get_outage_window("nginx")
    assert len(window) == 2
    # Most recent first
    assert "fail 2" in window[0]["message"]
    assert "fail 1" in window[1]["message"]


def test_outage_window_returns_all_when_no_prior_recovery():
    log_alert(_make_alert(status="down", message="fail 1"), None, notified=True)
    time.sleep(0.01)
    log_alert(_make_alert(status="down", message="fail 2"), None, notified=True)

    window = get_outage_window("nginx")
    assert len(window) == 2


def test_outage_window_filters_by_service():
    log_alert(_make_alert(service="redis", status="down", message="redis fail"), None, notified=True)
    log_alert(_make_alert(service="nginx", status="down", message="nginx fail"), None, notified=True)

    window = get_outage_window("nginx")
    assert len(window) == 1
    assert "nginx fail" in window[0]["message"]


def test_outage_window_capped_at_20():
    for i in range(25):
        log_alert(_make_alert(status="down", message=f"fail {i}"), None, notified=True)

    window = get_outage_window("nginx")
    assert len(window) == 20


def test_outage_window_db_error_returns_empty():
    with patch("app.alert_db._get_conn", side_effect=Exception("boom")):
        assert get_outage_window("nginx") == []


# ---------------------------------------------------------------------------
# Resolution prompt template
# ---------------------------------------------------------------------------

def test_resolution_prompt_uses_recovery_template():
    alert = _make_alert(status="up", severity="info", message="nginx is back up")
    prompt = lc._build_prompt(alert, resolution=True)
    assert "RECOVERED" in prompt
    assert "post-mortem" in prompt


def test_normal_prompt_does_not_use_recovery_template():
    alert = _make_alert()
    prompt = lc._build_prompt(alert, resolution=False)
    assert "RECOVERED" not in prompt
    assert "post-mortem" not in prompt


def test_resolution_prompt_includes_history():
    alert = _make_alert(status="up", severity="info", message="recovered")
    history = [{"ts": time.time() - 300, "status": "down", "severity": "critical", "message": "Connection refused"}]
    prompt = lc._build_prompt(alert, history=history, resolution=True)
    assert "<alert_history>" in prompt
    assert "Connection refused" in prompt


# ---------------------------------------------------------------------------
# Resolution webhook integration
# ---------------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("SENTINEL_MODE", "predictive")
    monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("DEDUP_TTL_SECONDS", "0")
    monkeypatch.setenv("COOLDOWN_SECONDS", "0")
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_TOKEN", "test-token")
    monkeypatch.setenv("GEMINI_RPM", "0")
    monkeypatch.setenv("GEMINI_RETRIES", "0")
    with lc._gemini_rpm_lock:
        lc._gemini_rpm_call_times.clear()
    from app import create_app
    app = create_app()
    return app.test_client()


def _mock_gemini_resp(insight="AI insight", actions=None):
    mock = MagicMock()
    mock.status_code = 200
    mock.raise_for_status.return_value = None
    mock.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": json.dumps({
            "insight": insight,
            "suggested_actions": actions or ["check logs"],
        })}]}}]
    }
    return mock


def test_recovery_triggers_resolution_prompt(client, monkeypatch):
    # First: log a down alert
    log_alert(_make_alert(status="down", message="Connection refused"), None, notified=True)

    # Then: send a recovery alert
    with patch.object(lc._gemini_session, "post", return_value=_mock_gemini_resp("Service recovered after 5 minutes")) as mock_post:
        resp = client.post(
            "/webhook",
            data=json.dumps({
                "heartbeat": {"status": 1},
                "monitor": {"name": "nginx"},
                "msg": "nginx is up",
            }),
            content_type="application/json",
        )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "processed"
    # Verify the AI was called
    mock_post.assert_called_once()
