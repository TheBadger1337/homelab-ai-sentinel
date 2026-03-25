"""
Unit tests for notify.py dispatcher.

Uses mocks to isolate dispatch logic from individual platform clients.
"""

from unittest.mock import MagicMock, patch

import requests

from app.alert_parser import NormalizedAlert
from app.notify import dispatch, _is_disabled


def _make_alert(**kwargs):
    defaults = {
        "source": "generic",
        "service_name": "redis",
        "status": "down",
        "severity": "critical",
        "message": "Connection refused",
        "details": {},
    }
    defaults.update(kwargs)
    return NormalizedAlert(**defaults)


_AI = {"insight": "Server is down.", "suggested_actions": ["Check logs"]}


def test_dispatch_calls_all_clients():
    alert = _make_alert()
    mock_discord = MagicMock()
    mock_slack = MagicMock()
    mock_discord.__name__ = "app.discord_client"
    mock_slack.__name__ = "app.slack_client"

    with patch("app.notify._CLIENTS", [mock_discord, mock_slack]):
        errors = dispatch(alert, _AI)

    mock_discord.post_alert.assert_called_once_with(alert, _AI)
    mock_slack.post_alert.assert_called_once_with(alert, _AI)
    assert errors == []


def test_dispatch_collects_requests_error_without_blocking():
    alert = _make_alert()
    mock_discord = MagicMock()
    mock_slack = MagicMock()
    mock_discord.__name__ = "app.discord_client"
    mock_slack.__name__ = "app.slack_client"

    mock_discord.post_alert.side_effect = requests.RequestException("timeout")

    with patch("app.notify._CLIENTS", [mock_discord, mock_slack]):
        errors = dispatch(alert, _AI)

    # Slack still called despite Discord failing
    mock_slack.post_alert.assert_called_once_with(alert, _AI)
    assert len(errors) == 1
    # Use any() — parallel dispatch does not guarantee error list order
    assert any("discord" in e for e in errors)


def test_dispatch_collects_unexpected_error_without_blocking():
    alert = _make_alert()
    mock_discord = MagicMock()
    mock_slack = MagicMock()
    mock_discord.__name__ = "app.discord_client"
    mock_slack.__name__ = "app.slack_client"

    mock_discord.post_alert.side_effect = ValueError("unexpected")

    with patch("app.notify._CLIENTS", [mock_discord, mock_slack]):
        errors = dispatch(alert, _AI)

    mock_slack.post_alert.assert_called_once_with(alert, _AI)
    assert len(errors) == 1
    assert any("discord" in e for e in errors)


def test_dispatch_returns_empty_list_on_success():
    alert = _make_alert()
    mock_client = MagicMock()
    mock_client.__name__ = "app.discord_client"

    with patch("app.notify._CLIENTS", [mock_client]):
        errors = dispatch(alert, _AI)

    assert errors == []


def test_disabled_client_is_skipped(monkeypatch):
    """A client with {NAME}_DISABLED=true must not have post_alert called."""
    alert = _make_alert()
    mock_discord = MagicMock()
    mock_discord.__name__ = "app.discord_client"
    monkeypatch.setenv("DISCORD_DISABLED", "true")

    with patch("app.notify._CLIENTS", [mock_discord]):
        errors = dispatch(alert, _AI)

    mock_discord.post_alert.assert_not_called()
    assert errors == []


def test_is_disabled_returns_false_when_not_set(monkeypatch):
    """_is_disabled must return False when the env var is absent."""
    mock_client = MagicMock()
    mock_client.__name__ = "app.telegram_client"
    monkeypatch.delenv("TELEGRAM_DISABLED", raising=False)
    assert _is_disabled(mock_client) is False


def test_dispatch_multiple_failures_all_collected():
    alert = _make_alert()
    mock_discord = MagicMock()
    mock_slack = MagicMock()
    mock_discord.__name__ = "app.discord_client"
    mock_slack.__name__ = "app.slack_client"

    mock_discord.post_alert.side_effect = requests.RequestException("timeout")
    mock_slack.post_alert.side_effect = requests.RequestException("403")

    with patch("app.notify._CLIENTS", [mock_discord, mock_slack]):
        errors = dispatch(alert, _AI)

    assert len(errors) == 2
    assert any("discord" in e for e in errors)
    assert any("slack" in e for e in errors)
