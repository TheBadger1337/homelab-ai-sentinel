"""
Unit tests for slack_client.py

Covers _build_message structure and post_alert behavior.
"""

from unittest.mock import MagicMock, patch

import pytest
import requests

from app.alert_parser import NormalizedAlert
from app.slack_client import _build_message, _strip_mentions, post_alert


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
# _build_message structure
# ---------------------------------------------------------------------------

def test_build_message_has_text_fallback():
    msg = _build_message(_make_alert(), _AI)
    assert "text" in msg
    assert len(msg["text"]) <= 150


def test_build_message_has_blocks():
    msg = _build_message(_make_alert(), _AI)
    assert "blocks" in msg
    assert isinstance(msg["blocks"], list)
    assert len(msg["blocks"]) >= 4  # header, 2x section, insight, [actions,] divider, context


def test_build_message_header_block():
    msg = _build_message(_make_alert(), _AI)
    header = msg["blocks"][0]
    assert header["type"] == "header"
    assert header["text"]["type"] == "plain_text"
    assert "CRITICAL" in header["text"]["text"]
    assert "Nginx" in header["text"]["text"]
    assert "DOWN" in header["text"]["text"]


def test_build_message_header_capped_at_150():
    alert = _make_alert(service_name="x" * 200)
    msg = _build_message(alert, _AI)
    assert len(msg["blocks"][0]["text"]["text"]) <= 150


def test_build_message_severity_emoji_critical():
    msg = _build_message(_make_alert(severity="critical"), _AI)
    assert "🔴" in msg["text"]


def test_build_message_severity_emoji_warning():
    msg = _build_message(_make_alert(severity="warning", status="warning"), _AI)
    assert "🟡" in msg["text"]


def test_build_message_severity_emoji_info():
    msg = _build_message(_make_alert(severity="info", status="up"), _AI)
    assert "🟢" in msg["text"]


def test_build_message_source_formatted():
    alert = _make_alert(source="uptime_kuma")
    msg = _build_message(alert, _AI)
    fields_block = msg["blocks"][1]
    all_text = str(fields_block)
    assert "Uptime Kuma" in all_text


def test_build_message_alert_message_present():
    msg = _build_message(_make_alert(), _AI)
    blocks_text = str(msg["blocks"])
    assert "Connection refused" in blocks_text


def test_build_message_insight_present():
    msg = _build_message(_make_alert(), _AI)
    blocks_text = str(msg["blocks"])
    assert "Nginx is unreachable" in blocks_text


def test_build_message_actions_present_when_provided():
    msg = _build_message(_make_alert(), _AI)
    blocks_text = str(msg["blocks"])
    assert "Check Docker logs" in blocks_text
    assert "Restart container" in blocks_text


def test_build_message_no_actions_block_when_empty():
    msg = _build_message(_make_alert(), _AI_EMPTY)
    # Count section blocks — no actions section should be present
    action_blocks = [
        b for b in msg["blocks"]
        if b.get("type") == "section"
        and "Suggested Actions" in str(b.get("text", ""))
    ]
    assert action_blocks == []


def test_build_message_non_list_actions_skipped():
    ai = {"insight": "ok", "suggested_actions": "not a list"}
    msg = _build_message(_make_alert(), ai)
    action_blocks = [
        b for b in msg["blocks"]
        if "Suggested Actions" in str(b)
    ]
    assert action_blocks == []


def test_build_message_ends_with_context_footer():
    msg = _build_message(_make_alert(), _AI)
    last = msg["blocks"][-1]
    assert last["type"] == "context"
    assert "Sentinel" in str(last)


def test_build_message_alert_message_capped():
    alert = _make_alert(message="x" * 2000)
    msg = _build_message(alert, _AI)
    # Find the alert message section block
    msg_block = next(
        b for b in msg["blocks"]
        if b.get("type") == "section"
        and "Alert Message" in str(b.get("text", ""))
    )
    assert len(msg_block["text"]["text"]) <= 1016  # "*Alert Message*\n" (16) + 1000 chars


# ---------------------------------------------------------------------------
# post_alert behavior
# ---------------------------------------------------------------------------

def test_post_alert_skips_when_no_url(monkeypatch):
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    with patch("app.slack_client.requests.post") as mock_post:
        post_alert(_make_alert(), _AI)
    mock_post.assert_not_called()


def test_post_alert_posts_when_url_set(monkeypatch):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/test")
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    with patch("app.slack_client.requests.post", return_value=mock_resp) as mock_post:
        post_alert(_make_alert(), _AI)
    mock_post.assert_called_once()
    _, kwargs = mock_post.call_args
    assert kwargs["json"]["blocks"] is not None


def test_post_alert_raises_on_http_error(monkeypatch):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/test")
    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = requests.HTTPError("403")
    with patch("app.slack_client.requests.post", return_value=mock_resp):
        with pytest.raises(requests.HTTPError):
            post_alert(_make_alert(), _AI)


# ---------------------------------------------------------------------------
# _strip_mentions
# ---------------------------------------------------------------------------

def test_strip_mentions_neutralizes_here():
    result = _strip_mentions("<!here> alert fired")
    assert "<!here>" not in result
    assert "@here" in result


def test_strip_mentions_neutralizes_channel():
    result = _strip_mentions("<!channel> check this")
    assert "<!channel>" not in result
    assert "@channel" in result


def test_strip_mentions_neutralizes_everyone():
    result = _strip_mentions("<!everyone> important")
    assert "<!everyone>" not in result
    assert "@everyone" in result


def test_strip_mentions_clean_text_unchanged():
    text = "No Slack mentions here"
    assert _strip_mentions(text) == text
