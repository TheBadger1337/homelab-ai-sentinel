"""
Unit tests for matrix_client.py
"""

from unittest.mock import MagicMock, patch

import pytest
import requests

from app.alert_parser import NormalizedAlert
from app.matrix_client import _build_message, post_alert


def _make_alert(**kwargs):
    defaults = {
        "source": "uptime_kuma",
        "service_name": "Vaultwarden",
        "status": "down",
        "severity": "critical",
        "message": "Connection refused",
        "details": {},
    }
    defaults.update(kwargs)
    return NormalizedAlert(**defaults)


_AI = {
    "insight": "Vaultwarden is unreachable.",
    "suggested_actions": ["Check Docker logs", "Restart container"],
}

_AI_EMPTY = {"insight": "No insight.", "suggested_actions": []}


# ---------------------------------------------------------------------------
# _build_message
# ---------------------------------------------------------------------------

def test_build_message_plain_contains_service_and_status():
    plain, _ = _build_message(_make_alert(), _AI)
    assert "Vaultwarden" in plain
    assert "down" in plain
    assert "CRITICAL" in plain


def test_build_message_plain_contains_insight():
    plain, _ = _build_message(_make_alert(), _AI)
    assert "Vaultwarden is unreachable" in plain


def test_build_message_plain_contains_actions():
    plain, _ = _build_message(_make_alert(), _AI)
    assert "Check Docker logs" in plain


def test_build_message_html_contains_bold_header():
    _, html_text = _build_message(_make_alert(), _AI)
    assert "<strong>" in html_text
    assert "Vaultwarden" in html_text


def test_build_message_html_contains_action_list():
    _, html_text = _build_message(_make_alert(), _AI)
    assert "<ul>" in html_text
    assert "<li>" in html_text


def test_build_message_html_escapes_special_chars():
    alert = _make_alert(service_name="<script>alert(1)</script>")
    _, html_text = _build_message(alert, _AI)
    assert "<script>" not in html_text
    assert "&lt;script&gt;" in html_text


def test_build_message_no_action_list_when_empty():
    plain, html_text = _build_message(_make_alert(), _AI_EMPTY)
    assert "<ul>" not in html_text
    assert "•" not in plain


def test_build_message_status_emoji_down():
    plain, _ = _build_message(_make_alert(status="down"), _AI)
    assert "🔴" in plain


def test_build_message_status_emoji_up():
    plain, _ = _build_message(_make_alert(status="up", severity="info"), _AI)
    assert "🟢" in plain


def test_build_message_non_list_actions_skipped():
    ai = {"insight": "ok", "suggested_actions": "not a list"}
    plain, html_text = _build_message(_make_alert(), ai)
    assert "<ul>" not in html_text


# ---------------------------------------------------------------------------
# post_alert
# ---------------------------------------------------------------------------

def test_post_alert_skips_when_no_homeserver(monkeypatch):
    monkeypatch.delenv("MATRIX_HOMESERVER", raising=False)
    monkeypatch.delenv("MATRIX_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("MATRIX_ROOM_ID", raising=False)
    with patch("app.matrix_client.requests.put") as mock_put:
        post_alert(_make_alert(), _AI)
    mock_put.assert_not_called()


def test_post_alert_skips_when_missing_room_id(monkeypatch):
    monkeypatch.setenv("MATRIX_HOMESERVER", "https://matrix.org")
    monkeypatch.setenv("MATRIX_ACCESS_TOKEN", "syt_token")
    monkeypatch.delenv("MATRIX_ROOM_ID", raising=False)
    with patch("app.matrix_client.requests.put") as mock_put:
        post_alert(_make_alert(), _AI)
    mock_put.assert_not_called()


def test_post_alert_uses_put_method(monkeypatch):
    monkeypatch.setenv("MATRIX_HOMESERVER", "https://matrix.org")
    monkeypatch.setenv("MATRIX_ACCESS_TOKEN", "syt_token")
    monkeypatch.setenv("MATRIX_ROOM_ID", "!abc123:matrix.org")
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    with patch("app.matrix_client.requests.put", return_value=mock_resp) as mock_put:
        post_alert(_make_alert(), _AI)
    mock_put.assert_called_once()


def test_post_alert_url_contains_room_id(monkeypatch):
    monkeypatch.setenv("MATRIX_HOMESERVER", "https://matrix.org")
    monkeypatch.setenv("MATRIX_ACCESS_TOKEN", "syt_token")
    monkeypatch.setenv("MATRIX_ROOM_ID", "!abc123:matrix.org")
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    with patch("app.matrix_client.requests.put", return_value=mock_resp) as mock_put:
        post_alert(_make_alert(), _AI)
    url = mock_put.call_args[0][0]
    assert "%21abc123%3Amatrix.org" in url


def test_post_alert_sends_bearer_token(monkeypatch):
    monkeypatch.setenv("MATRIX_HOMESERVER", "https://matrix.org")
    monkeypatch.setenv("MATRIX_ACCESS_TOKEN", "syt_mytoken")
    monkeypatch.setenv("MATRIX_ROOM_ID", "!abc123:matrix.org")
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    with patch("app.matrix_client.requests.put", return_value=mock_resp) as mock_put:
        post_alert(_make_alert(), _AI)
    headers = mock_put.call_args[1]["headers"]
    assert headers["Authorization"] == "Bearer syt_mytoken"


def test_post_alert_payload_has_formatted_body(monkeypatch):
    monkeypatch.setenv("MATRIX_HOMESERVER", "https://matrix.org")
    monkeypatch.setenv("MATRIX_ACCESS_TOKEN", "syt_token")
    monkeypatch.setenv("MATRIX_ROOM_ID", "!abc123:matrix.org")
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    with patch("app.matrix_client.requests.put", return_value=mock_resp) as mock_put:
        post_alert(_make_alert(), _AI)
    body = mock_put.call_args[1]["json"]
    assert body["msgtype"] == "m.text"
    assert "formatted_body" in body
    assert body["format"] == "org.matrix.custom.html"


def test_post_alert_raises_on_http_error(monkeypatch):
    monkeypatch.setenv("MATRIX_HOMESERVER", "https://matrix.org")
    monkeypatch.setenv("MATRIX_ACCESS_TOKEN", "syt_token")
    monkeypatch.setenv("MATRIX_ROOM_ID", "!abc123:matrix.org")
    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = requests.HTTPError("403")
    with patch("app.matrix_client.requests.put", return_value=mock_resp):
        with pytest.raises(requests.HTTPError):
            post_alert(_make_alert(), _AI)
