"""
Unit tests for ntfy_client.py
"""

from unittest.mock import MagicMock, patch

import pytest
import requests

from app.alert_parser import NormalizedAlert
from app.ntfy_client import _build_payload, post_alert


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
# _build_payload
# ---------------------------------------------------------------------------

def test_build_payload_title_contains_service_and_status():
    p = _build_payload(_make_alert(), _AI)
    assert "Nginx" in p["title"]
    assert "DOWN" in p["title"]
    assert "CRITICAL" in p["title"]


def test_build_payload_title_capped():
    alert = _make_alert(service_name="x" * 300)
    p = _build_payload(alert, _AI)
    assert len(p["title"]) <= 250


def test_build_payload_priority_critical():
    p = _build_payload(_make_alert(severity="critical"), _AI)
    assert p["priority"] == "urgent"


def test_build_payload_priority_warning():
    p = _build_payload(_make_alert(severity="warning", status="warning"), _AI)
    assert p["priority"] == "high"


def test_build_payload_priority_info():
    p = _build_payload(_make_alert(severity="info", status="up"), _AI)
    assert p["priority"] == "default"


def test_build_payload_tag_critical():
    p = _build_payload(_make_alert(severity="critical"), _AI)
    assert "rotating_light" in p["tags"]


def test_build_payload_tag_warning():
    p = _build_payload(_make_alert(severity="warning", status="warning"), _AI)
    assert "warning" in p["tags"]


def test_build_payload_tag_info():
    p = _build_payload(_make_alert(severity="info", status="up"), _AI)
    assert "white_check_mark" in p["tags"]


def test_build_payload_message_contains_alert_message():
    p = _build_payload(_make_alert(), _AI)
    assert "Connection refused" in p["message"]


def test_build_payload_message_contains_insight():
    p = _build_payload(_make_alert(), _AI)
    assert "Nginx is unreachable" in p["message"]


def test_build_payload_message_contains_actions():
    p = _build_payload(_make_alert(), _AI)
    assert "Check Docker logs" in p["message"]


def test_build_payload_no_actions_when_empty():
    p = _build_payload(_make_alert(), _AI_EMPTY)
    assert "•" not in p["message"]


def test_build_payload_non_list_actions_skipped():
    ai = {"insight": "ok", "suggested_actions": "not a list"}
    p = _build_payload(_make_alert(), ai)
    assert "•" not in p["message"]


# ---------------------------------------------------------------------------
# post_alert
# ---------------------------------------------------------------------------

def test_post_alert_skips_when_no_url(monkeypatch):
    monkeypatch.delenv("NTFY_URL", raising=False)
    with patch("app.ntfy_client.requests.post") as mock_post:
        post_alert(_make_alert(), _AI)
    mock_post.assert_not_called()


def test_post_alert_posts_when_url_set(monkeypatch):
    monkeypatch.setenv("NTFY_URL", "https://ntfy.sh/test-topic")
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    with patch("app.ntfy_client.requests.post", return_value=mock_resp) as mock_post:
        post_alert(_make_alert(), _AI)
    mock_post.assert_called_once()
    url = mock_post.call_args[0][0]
    assert "ntfy.sh/test-topic" in url


def test_post_alert_raises_on_http_error(monkeypatch):
    monkeypatch.setenv("NTFY_URL", "https://ntfy.sh/test-topic")
    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = requests.HTTPError("403")
    with patch("app.ntfy_client.requests.post", return_value=mock_resp):
        with pytest.raises(requests.HTTPError):
            post_alert(_make_alert(), _AI)
