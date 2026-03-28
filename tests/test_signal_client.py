"""
Unit tests for signal_client.py
"""

from unittest.mock import MagicMock, patch

import pytest
import requests

from app.alert_parser import NormalizedAlert
from app.signal_client import _build_message, post_alert


def _make_alert(**kwargs):
    defaults = {
        "source": "uptime_kuma",
        "service_name": "Nginx",
        "status": "down",
        "severity": "critical",
        "message": "Connection refused",
        "details": {},
    }
    defaults.update(kwargs)
    return NormalizedAlert(**defaults)


_AI = {
    "insight": "Nginx is unreachable.",
    "suggested_actions": ["Check Docker logs", "Restart container"],
}

_AI_EMPTY = {"insight": "No insight available.", "suggested_actions": []}


# ---------------------------------------------------------------------------
# _build_message
# ---------------------------------------------------------------------------

def test_build_message_contains_service_name():
    assert "Nginx" in _build_message(_make_alert(), _AI)


def test_build_message_contains_severity():
    assert "CRITICAL" in _build_message(_make_alert(), _AI)


def test_build_message_contains_status():
    assert "DOWN" in _build_message(_make_alert(), _AI)


def test_build_message_emoji_critical():
    assert "🔴" in _build_message(_make_alert(severity="critical"), _AI)


def test_build_message_emoji_warning():
    assert "🟡" in _build_message(_make_alert(severity="warning", status="warning"), _AI)


def test_build_message_emoji_info():
    assert "🟢" in _build_message(_make_alert(severity="info", status="up"), _AI)


def test_build_message_source_formatted():
    assert "Uptime Kuma" in _build_message(_make_alert(source="uptime_kuma"), _AI)


def test_build_message_contains_alert_message():
    assert "Connection refused" in _build_message(_make_alert(), _AI)


def test_build_message_contains_insight():
    assert "Nginx is unreachable" in _build_message(_make_alert(), _AI)


def test_build_message_contains_actions():
    msg = _build_message(_make_alert(), _AI)
    assert "Check Docker logs" in msg
    assert "Restart container" in msg


def test_build_message_no_actions_when_empty():
    assert "Suggested Actions" not in _build_message(_make_alert(), _AI_EMPTY)


def test_build_message_non_list_actions_skipped():
    ai = {"insight": "ok", "suggested_actions": "not a list"}
    assert "Suggested Actions" not in _build_message(_make_alert(), ai)


def test_build_message_has_footer():
    assert "Homelab AI Sentinel" in _build_message(_make_alert(), _AI)


# ---------------------------------------------------------------------------
# post_alert
# ---------------------------------------------------------------------------

def _signal_env(monkeypatch):
    monkeypatch.setenv("SIGNAL_API_URL", "http://signal-cli-rest-api:8080")
    monkeypatch.setenv("SIGNAL_SENDER", "+15551234567")
    monkeypatch.setenv("SIGNAL_RECIPIENT", "+15559876543")


def test_post_alert_skips_when_no_api_url(monkeypatch):
    monkeypatch.delenv("SIGNAL_API_URL", raising=False)
    monkeypatch.setenv("SIGNAL_SENDER", "+15551234567")
    monkeypatch.setenv("SIGNAL_RECIPIENT", "+15559876543")
    with patch("app.signal_client.requests.post") as mock_post:
        post_alert(_make_alert(), _AI)
    mock_post.assert_not_called()


def test_post_alert_skips_when_no_sender(monkeypatch):
    monkeypatch.setenv("SIGNAL_API_URL", "http://signal-cli-rest-api:8080")
    monkeypatch.delenv("SIGNAL_SENDER", raising=False)
    monkeypatch.setenv("SIGNAL_RECIPIENT", "+15559876543")
    with patch("app.signal_client.requests.post") as mock_post:
        post_alert(_make_alert(), _AI)
    mock_post.assert_not_called()


def test_post_alert_skips_when_no_recipient(monkeypatch):
    monkeypatch.setenv("SIGNAL_API_URL", "http://signal-cli-rest-api:8080")
    monkeypatch.setenv("SIGNAL_SENDER", "+15551234567")
    monkeypatch.delenv("SIGNAL_RECIPIENT", raising=False)
    with patch("app.signal_client.requests.post") as mock_post:
        post_alert(_make_alert(), _AI)
    mock_post.assert_not_called()


def test_post_alert_posts_when_configured(monkeypatch):
    _signal_env(monkeypatch)
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    with patch("app.signal_client.requests.post", return_value=mock_resp) as mock_post:
        post_alert(_make_alert(), _AI)
    mock_post.assert_called_once()


def test_post_alert_url_contains_v2_send(monkeypatch):
    _signal_env(monkeypatch)
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    with patch("app.signal_client.requests.post", return_value=mock_resp) as mock_post:
        post_alert(_make_alert(), _AI)
    url = mock_post.call_args[0][0]
    assert "v2/send" in url
    assert "signal-cli-rest-api:8080" in url


def test_post_alert_payload_structure(monkeypatch):
    _signal_env(monkeypatch)
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    with patch("app.signal_client.requests.post", return_value=mock_resp) as mock_post:
        post_alert(_make_alert(), _AI)
    payload = mock_post.call_args[1]["json"]
    assert payload["number"] == "+15551234567"
    assert "+15559876543" in payload["recipients"]
    assert "message" in payload


def test_post_alert_raises_on_http_error(monkeypatch):
    _signal_env(monkeypatch)
    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = requests.HTTPError("500")
    with patch("app.signal_client.requests.post", return_value=mock_resp):
        with pytest.raises(requests.HTTPError):
            post_alert(_make_alert(), _AI)


def test_post_alert_raises_runtime_error_on_error_body(monkeypatch):
    """signal-cli-rest-api returns 200 with {"error": ...} for some failures."""
    _signal_env(monkeypatch)
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {"error": "Invalid account"}
    with patch("app.signal_client.requests.post", return_value=mock_resp):
        with pytest.raises(RuntimeError, match="signal-cli-rest-api error"):
            post_alert(_make_alert(), _AI)


def test_post_alert_succeeds_on_non_json_200(monkeypatch):
    """Non-JSON 2xx body is treated as success (e.g. plain-text 'ok')."""
    _signal_env(monkeypatch)
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.side_effect = ValueError("not json")
    with patch("app.signal_client.requests.post", return_value=mock_resp):
        post_alert(_make_alert(), _AI)  # must not raise


def test_post_alert_skips_on_non_http_url(monkeypatch):
    """SSRF guard: file:// scheme must be rejected before making a request."""
    monkeypatch.setenv("SIGNAL_API_URL", "file:///etc/passwd")
    monkeypatch.setenv("SIGNAL_SENDER", "+15551234567")
    monkeypatch.setenv("SIGNAL_RECIPIENT", "+15559876543")
    with patch("app.signal_client.requests.post") as mock_post:
        post_alert(_make_alert(), _AI)
    mock_post.assert_not_called()
