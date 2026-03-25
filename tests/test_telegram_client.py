"""
Unit tests for telegram_client.py

Covers _build_message content and HTML escaping, and post_alert behavior.
"""

from unittest.mock import MagicMock, patch

import pytest
import requests

from app.alert_parser import NormalizedAlert
from app.telegram_client import _build_message, post_alert


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
# _build_message content
# ---------------------------------------------------------------------------

def test_build_message_contains_service_name():
    msg = _build_message(_make_alert(), _AI)
    assert "Nginx" in msg


def test_build_message_contains_severity():
    msg = _build_message(_make_alert(), _AI)
    assert "CRITICAL" in msg


def test_build_message_contains_status():
    msg = _build_message(_make_alert(), _AI)
    assert "DOWN" in msg


def test_build_message_severity_emoji_critical():
    msg = _build_message(_make_alert(severity="critical"), _AI)
    assert "🔴" in msg


def test_build_message_severity_emoji_warning():
    msg = _build_message(_make_alert(severity="warning", status="warning"), _AI)
    assert "🟡" in msg


def test_build_message_severity_emoji_info():
    msg = _build_message(_make_alert(severity="info", status="up"), _AI)
    assert "🟢" in msg


def test_build_message_source_formatted():
    msg = _build_message(_make_alert(source="uptime_kuma"), _AI)
    assert "Uptime Kuma" in msg


def test_build_message_alert_message_present():
    msg = _build_message(_make_alert(), _AI)
    assert "Connection refused" in msg


def test_build_message_insight_present():
    msg = _build_message(_make_alert(), _AI)
    assert "Nginx is unreachable" in msg


def test_build_message_actions_present():
    msg = _build_message(_make_alert(), _AI)
    assert "Check Docker logs" in msg
    assert "Restart container" in msg


def test_build_message_no_actions_when_empty():
    msg = _build_message(_make_alert(), _AI_EMPTY)
    assert "Suggested Actions" not in msg


def test_build_message_non_list_actions_skipped():
    ai = {"insight": "ok", "suggested_actions": "not a list"}
    msg = _build_message(_make_alert(), ai)
    assert "Suggested Actions" not in msg


def test_build_message_uses_html_bold_tags():
    msg = _build_message(_make_alert(), _AI)
    assert "<b>" in msg


def test_build_message_has_footer():
    msg = _build_message(_make_alert(), _AI)
    assert "Homelab AI Sentinel" in msg
    assert "<i>" in msg


# ---------------------------------------------------------------------------
# HTML escaping — alert fields are untrusted external input
# ---------------------------------------------------------------------------

def test_build_message_escapes_html_in_service_name():
    alert = _make_alert(service_name="<script>alert(1)</script>")
    msg = _build_message(alert, _AI)
    assert "<script>" not in msg
    assert "&lt;script&gt;" in msg


def test_build_message_escapes_html_in_alert_message():
    alert = _make_alert(message="<b>inject</b> & more")
    msg = _build_message(alert, _AI)
    assert "<b>inject</b>" not in msg
    assert "&lt;b&gt;" in msg
    assert "&amp;" in msg


def test_build_message_escapes_html_in_insight():
    ai = {"insight": "<evil>payload</evil>", "suggested_actions": []}
    msg = _build_message(_make_alert(), ai)
    assert "<evil>" not in msg
    assert "&lt;evil&gt;" in msg


# ---------------------------------------------------------------------------
# post_alert behavior
# ---------------------------------------------------------------------------

def test_post_alert_skips_when_no_token(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123456")
    with patch("app.telegram_client.requests.post") as mock_post:
        post_alert(_make_alert(), _AI)
    mock_post.assert_not_called()


def test_post_alert_skips_when_no_chat_id(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1234:ABCdef")
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    with patch("app.telegram_client.requests.post") as mock_post:
        post_alert(_make_alert(), _AI)
    mock_post.assert_not_called()


def test_post_alert_posts_when_both_set(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1234:ABCdef")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "987654321")
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    with patch("app.telegram_client.requests.post", return_value=mock_resp) as mock_post:
        post_alert(_make_alert(), _AI)
    mock_post.assert_called_once()
    _, kwargs = mock_post.call_args
    assert kwargs["json"]["chat_id"] == "987654321"
    assert kwargs["json"]["parse_mode"] == "HTML"


def test_post_alert_url_contains_token(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1234:ABCdef")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "987654321")
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    with patch("app.telegram_client.requests.post", return_value=mock_resp) as mock_post:
        post_alert(_make_alert(), _AI)
    url = mock_post.call_args[0][0]
    assert "bot1234:ABCdef" in url
    assert "sendMessage" in url


def test_post_alert_raises_on_http_error(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1234:ABCdef")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "987654321")
    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = requests.HTTPError("403")
    with patch("app.telegram_client.requests.post", return_value=mock_resp):
        with pytest.raises(requests.HTTPError):
            post_alert(_make_alert(), _AI)
