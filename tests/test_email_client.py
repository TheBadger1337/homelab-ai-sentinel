"""
Unit tests for email_client.py
"""

import smtplib
from unittest.mock import MagicMock, patch

import pytest

from app.alert_parser import NormalizedAlert
from app.email_client import _build_subject, _build_plain, _build_html, post_alert


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
# Subject line
# ---------------------------------------------------------------------------

def test_subject_contains_service_and_status():
    s = _build_subject(_make_alert())
    assert "Nginx" in s
    assert "DOWN" in s
    assert "CRITICAL" in s


def test_subject_emoji_critical():
    assert "🔴" in _build_subject(_make_alert(severity="critical"))


def test_subject_emoji_warning():
    assert "🟡" in _build_subject(_make_alert(severity="warning", status="warning"))


# ---------------------------------------------------------------------------
# Plain text body
# ---------------------------------------------------------------------------

def test_plain_contains_message():
    assert "Connection refused" in _build_plain(_make_alert(), _AI)


def test_plain_contains_insight():
    assert "Nginx is unreachable" in _build_plain(_make_alert(), _AI)


def test_plain_contains_actions():
    body = _build_plain(_make_alert(), _AI)
    assert "Check Docker logs" in body
    assert "Restart container" in body


def test_plain_no_actions_when_empty():
    body = _build_plain(_make_alert(), _AI_EMPTY)
    assert "Suggested Actions" not in body


def test_plain_source_formatted():
    body = _build_plain(_make_alert(source="uptime_kuma"), _AI)
    assert "Uptime Kuma" in body


# ---------------------------------------------------------------------------
# HTML body
# ---------------------------------------------------------------------------

def test_html_contains_service_name():
    assert "Nginx" in _build_html(_make_alert(), _AI)


def test_html_contains_insight():
    assert "Nginx is unreachable" in _build_html(_make_alert(), _AI)


def test_html_contains_actions():
    h = _build_html(_make_alert(), _AI)
    assert "Check Docker logs" in h
    assert "Restart container" in h


def test_html_no_actions_block_when_empty():
    h = _build_html(_make_alert(), _AI_EMPTY)
    assert "Suggested Actions" not in h


def test_html_escapes_service_name():
    alert = _make_alert(service_name="<script>xss</script>")
    h = _build_html(alert, _AI)
    assert "<script>" not in h
    assert "&lt;script&gt;" in h


def test_html_escapes_message():
    alert = _make_alert(message="<b>bad</b> & worse")
    h = _build_html(alert, _AI)
    assert "<b>bad</b>" not in h
    assert "&lt;b&gt;" in h


def test_html_escapes_insight():
    ai = {"insight": "<evil/>", "suggested_actions": []}
    h = _build_html(_make_alert(), ai)
    assert "<evil/>" not in h


# ---------------------------------------------------------------------------
# post_alert behavior
# ---------------------------------------------------------------------------

def _smtp_env(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "test@gmail.com")
    monkeypatch.setenv("SMTP_PASSWORD", "apppassword")
    monkeypatch.setenv("SMTP_TO", "alerts@gmail.com")


def test_post_alert_skips_when_no_host(monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.setenv("SMTP_USER", "u")
    monkeypatch.setenv("SMTP_PASSWORD", "p")
    with patch("app.email_client.smtplib.SMTP") as mock_smtp:
        post_alert(_make_alert(), _AI)
    mock_smtp.assert_not_called()


def test_post_alert_skips_when_no_password(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setenv("SMTP_USER", "u")
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    with patch("app.email_client.smtplib.SMTP") as mock_smtp:
        post_alert(_make_alert(), _AI)
    mock_smtp.assert_not_called()


def test_post_alert_sends_when_configured(monkeypatch):
    _smtp_env(monkeypatch)
    mock_smtp_instance = MagicMock()
    with patch("app.email_client.smtplib.SMTP") as mock_smtp:
        mock_smtp.return_value.__enter__.return_value = mock_smtp_instance
        post_alert(_make_alert(), _AI)
    mock_smtp_instance.starttls.assert_called_once()
    mock_smtp_instance.login.assert_called_once_with("test@gmail.com", "apppassword")
    mock_smtp_instance.sendmail.assert_called_once()


def test_post_alert_sends_to_correct_address(monkeypatch):
    _smtp_env(monkeypatch)
    mock_smtp_instance = MagicMock()
    with patch("app.email_client.smtplib.SMTP") as mock_smtp:
        mock_smtp.return_value.__enter__.return_value = mock_smtp_instance
        post_alert(_make_alert(), _AI)
    _, to_addr, _ = mock_smtp_instance.sendmail.call_args[0]
    assert to_addr == "alerts@gmail.com"


def test_post_alert_defaults_to_to_equals_user(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setenv("SMTP_USER", "test@gmail.com")
    monkeypatch.setenv("SMTP_PASSWORD", "apppassword")
    monkeypatch.delenv("SMTP_TO", raising=False)
    mock_smtp_instance = MagicMock()
    with patch("app.email_client.smtplib.SMTP") as mock_smtp:
        mock_smtp.return_value.__enter__.return_value = mock_smtp_instance
        post_alert(_make_alert(), _AI)
    _, to_addr, _ = mock_smtp_instance.sendmail.call_args[0]
    assert to_addr == "test@gmail.com"


def test_post_alert_smtp_auth_error_scrubbed(monkeypatch):
    """SMTPAuthenticationError is re-raised with a sanitized message, never the raw server response."""
    _smtp_env(monkeypatch)
    original = smtplib.SMTPAuthenticationError(535, b"535 Username and Password not accepted")
    mock_smtp_instance = MagicMock()
    mock_smtp_instance.login.side_effect = original

    with patch("app.email_client.smtplib.SMTP") as mock_smtp:
        mock_smtp.return_value.__enter__.return_value = mock_smtp_instance
        with pytest.raises(smtplib.SMTPAuthenticationError) as exc_info:
            post_alert(_make_alert(), _AI)

    raised = exc_info.value
    assert raised.smtp_code == 535
    assert b"Authentication failed" in raised.smtp_error
    assert b"Username" not in raised.smtp_error
