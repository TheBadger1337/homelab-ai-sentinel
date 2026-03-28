"""
Unit tests for imessage_client.py

Note: iMessage requires Apple hardware (Bluebubbles bridge on a Mac).
These tests verify the message builder and HTTP client behavior in isolation —
no real Bluebubbles server or Apple device is required.
"""

from unittest.mock import MagicMock, patch

import pytest
import requests

from app.alert_parser import NormalizedAlert
from app.imessage_client import _build_message, post_alert


def _make_alert(**kwargs):
    defaults = {
        "source": "uptime_kuma",
        "service_name": "Homebridge",
        "status": "down",
        "severity": "critical",
        "message": "Process crashed",
        "details": {},
    }
    defaults.update(kwargs)
    return NormalizedAlert(**defaults)


_AI = {
    "insight": "Homebridge has stopped.",
    "suggested_actions": ["Restart the service", "Check logs"],
}

_AI_EMPTY = {"insight": "No insight.", "suggested_actions": []}


# ---------------------------------------------------------------------------
# _build_message
# ---------------------------------------------------------------------------

def test_build_message_contains_service_and_status():
    msg = _build_message(_make_alert(), _AI)
    assert "Homebridge" in msg
    assert "down" in msg
    assert "CRITICAL" in msg


def test_build_message_contains_insight():
    msg = _build_message(_make_alert(), _AI)
    assert "Homebridge has stopped" in msg


def test_build_message_contains_actions():
    msg = _build_message(_make_alert(), _AI)
    assert "Restart the service" in msg


def test_build_message_no_actions_when_empty():
    msg = _build_message(_make_alert(), _AI_EMPTY)
    assert "•" not in msg


def test_build_message_status_emoji_down():
    msg = _build_message(_make_alert(status="down"), _AI)
    assert "🔴" in msg


def test_build_message_status_emoji_up():
    msg = _build_message(_make_alert(status="up", severity="info"), _AI)
    assert "🟢" in msg


def test_build_message_non_list_actions_skipped():
    ai = {"insight": "ok", "suggested_actions": "not a list"}
    msg = _build_message(_make_alert(), ai)
    assert "•" not in msg


# ---------------------------------------------------------------------------
# post_alert
# ---------------------------------------------------------------------------

def test_post_alert_skips_when_no_url(monkeypatch):
    monkeypatch.delenv("IMESSAGE_URL", raising=False)
    monkeypatch.delenv("IMESSAGE_PASSWORD", raising=False)
    monkeypatch.delenv("IMESSAGE_TO", raising=False)
    with patch("app.imessage_client.requests.post") as mock_post:
        post_alert(_make_alert(), _AI)
    mock_post.assert_not_called()


def test_post_alert_skips_when_missing_password(monkeypatch):
    monkeypatch.setenv("IMESSAGE_URL", "http://mac.local:1234")
    monkeypatch.delenv("IMESSAGE_PASSWORD", raising=False)
    monkeypatch.setenv("IMESSAGE_TO", "+15551234567")
    with patch("app.imessage_client.requests.post") as mock_post:
        post_alert(_make_alert(), _AI)
    mock_post.assert_not_called()


def test_post_alert_skips_when_missing_recipient(monkeypatch):
    monkeypatch.setenv("IMESSAGE_URL", "http://mac.local:1234")
    monkeypatch.setenv("IMESSAGE_PASSWORD", "hunter2")
    monkeypatch.delenv("IMESSAGE_TO", raising=False)
    with patch("app.imessage_client.requests.post") as mock_post:
        post_alert(_make_alert(), _AI)
    mock_post.assert_not_called()


def test_post_alert_posts_when_all_env_set(monkeypatch):
    monkeypatch.setenv("IMESSAGE_URL", "http://mac.local:1234")
    monkeypatch.setenv("IMESSAGE_PASSWORD", "hunter2")
    monkeypatch.setenv("IMESSAGE_TO", "+15551234567")
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    with patch("app.imessage_client.requests.post", return_value=mock_resp) as mock_post:
        post_alert(_make_alert(), _AI)
    mock_post.assert_called_once()


def test_post_alert_url_uses_message_text_endpoint(monkeypatch):
    monkeypatch.setenv("IMESSAGE_URL", "http://mac.local:1234")
    monkeypatch.setenv("IMESSAGE_PASSWORD", "hunter2")
    monkeypatch.setenv("IMESSAGE_TO", "+15551234567")
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    with patch("app.imessage_client.requests.post", return_value=mock_resp) as mock_post:
        post_alert(_make_alert(), _AI)
    url = mock_post.call_args[0][0]
    assert "/api/v1/message/text" in url


def test_post_alert_chat_guid_uses_recipient(monkeypatch):
    monkeypatch.setenv("IMESSAGE_URL", "http://mac.local:1234")
    monkeypatch.setenv("IMESSAGE_PASSWORD", "hunter2")
    monkeypatch.setenv("IMESSAGE_TO", "+15551234567")
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    with patch("app.imessage_client.requests.post", return_value=mock_resp) as mock_post:
        post_alert(_make_alert(), _AI)
    body = mock_post.call_args[1]["json"]
    assert "+15551234567" in body["chatGuid"]
    assert body["chatGuid"].startswith("iMessage;-;")


def test_post_alert_raises_on_http_error(monkeypatch):
    monkeypatch.setenv("IMESSAGE_URL", "http://mac.local:1234")
    monkeypatch.setenv("IMESSAGE_PASSWORD", "hunter2")
    monkeypatch.setenv("IMESSAGE_TO", "+15551234567")
    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = requests.HTTPError("500")
    with patch("app.imessage_client.requests.post", return_value=mock_resp):
        with pytest.raises(requests.HTTPError):
            post_alert(_make_alert(), _AI)


def test_post_alert_skips_on_non_http_url(monkeypatch):
    """SSRF guard: file:// scheme must be rejected before making a request."""
    monkeypatch.setenv("IMESSAGE_URL", "file:///etc/passwd")
    monkeypatch.setenv("IMESSAGE_PASSWORD", "hunter2")
    monkeypatch.setenv("IMESSAGE_TO", "+15551234567")
    with patch("app.imessage_client.requests.post") as mock_post:
        post_alert(_make_alert(), _AI)
    mock_post.assert_not_called()
