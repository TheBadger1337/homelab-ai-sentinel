"""
Unit tests for whatsapp_client.py
"""

from unittest.mock import MagicMock, patch

import pytest
import requests

from app.alert_parser import NormalizedAlert
from app.whatsapp_client import _build_message, post_alert


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

def _wa_env(monkeypatch):
    monkeypatch.setenv("WHATSAPP_TOKEN", "test-token")
    monkeypatch.setenv("WHATSAPP_PHONE_ID", "123456789")
    monkeypatch.setenv("WHATSAPP_TO", "15551234567")


def test_post_alert_skips_when_no_token(monkeypatch):
    monkeypatch.delenv("WHATSAPP_TOKEN", raising=False)
    monkeypatch.setenv("WHATSAPP_PHONE_ID", "123")
    monkeypatch.setenv("WHATSAPP_TO", "15551234567")
    with patch("app.whatsapp_client.requests.post") as mock_post:
        post_alert(_make_alert(), _AI)
    mock_post.assert_not_called()


def test_post_alert_skips_when_no_phone_id(monkeypatch):
    monkeypatch.setenv("WHATSAPP_TOKEN", "token")
    monkeypatch.delenv("WHATSAPP_PHONE_ID", raising=False)
    monkeypatch.setenv("WHATSAPP_TO", "15551234567")
    with patch("app.whatsapp_client.requests.post") as mock_post:
        post_alert(_make_alert(), _AI)
    mock_post.assert_not_called()


def test_post_alert_skips_when_no_to(monkeypatch):
    monkeypatch.setenv("WHATSAPP_TOKEN", "token")
    monkeypatch.setenv("WHATSAPP_PHONE_ID", "123")
    monkeypatch.delenv("WHATSAPP_TO", raising=False)
    with patch("app.whatsapp_client.requests.post") as mock_post:
        post_alert(_make_alert(), _AI)
    mock_post.assert_not_called()


def test_post_alert_posts_when_configured(monkeypatch):
    _wa_env(monkeypatch)
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    with patch("app.whatsapp_client.requests.post", return_value=mock_resp) as mock_post:
        post_alert(_make_alert(), _AI)
    mock_post.assert_called_once()


def test_post_alert_url_contains_phone_id(monkeypatch):
    _wa_env(monkeypatch)
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    with patch("app.whatsapp_client.requests.post", return_value=mock_resp) as mock_post:
        post_alert(_make_alert(), _AI)
    url = mock_post.call_args[0][0]
    assert "123456789" in url
    assert "messages" in url


def test_post_alert_authorization_header(monkeypatch):
    _wa_env(monkeypatch)
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    with patch("app.whatsapp_client.requests.post", return_value=mock_resp) as mock_post:
        post_alert(_make_alert(), _AI)
    headers = mock_post.call_args[1]["headers"]
    assert headers["Authorization"] == "Bearer test-token"


def test_post_alert_payload_structure(monkeypatch):
    _wa_env(monkeypatch)
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    with patch("app.whatsapp_client.requests.post", return_value=mock_resp) as mock_post:
        post_alert(_make_alert(), _AI)
    payload = mock_post.call_args[1]["json"]
    assert payload["messaging_product"] == "whatsapp"
    assert payload["to"] == "15551234567"
    assert payload["type"] == "text"
    assert "body" in payload["text"]


def test_post_alert_raises_on_http_error(monkeypatch):
    _wa_env(monkeypatch)
    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = requests.HTTPError("403")
    with patch("app.whatsapp_client.requests.post", return_value=mock_resp):
        with pytest.raises(requests.HTTPError):
            post_alert(_make_alert(), _AI)


def test_post_alert_raises_runtime_error_on_error_body(monkeypatch):
    """Meta returns HTTP 200 with an error body for auth/policy failures."""
    _wa_env(monkeypatch)
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {
        "error": {"code": 190, "message": "Invalid OAuth access token"}
    }
    with patch("app.whatsapp_client.requests.post", return_value=mock_resp):
        with pytest.raises(RuntimeError, match="WhatsApp API error"):
            post_alert(_make_alert(), _AI)


def test_post_alert_succeeds_on_non_json_200(monkeypatch):
    """Non-JSON 2xx response is treated as success."""
    _wa_env(monkeypatch)
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.side_effect = ValueError("not json")
    with patch("app.whatsapp_client.requests.post", return_value=mock_resp):
        post_alert(_make_alert(), _AI)  # must not raise
