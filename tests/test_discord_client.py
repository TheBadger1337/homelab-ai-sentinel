"""
Unit tests for discord_client.py

Tests the embed builder — pure function, no network calls required.
"""

from app.alert_parser import NormalizedAlert
from app.discord_client import _build_embed


def _make_alert(**kwargs):
    defaults = dict(
        source="generic",
        status="down",
        severity="critical",
        service_name="test-service",
        message="Connection refused",
        details={},
    )
    return NormalizedAlert(**{**defaults, **kwargs})


def _make_ai(**kwargs):
    defaults = dict(
        insight="This is the AI insight.",
        suggested_actions=["Check logs", "Restart service"],
    )
    return {**defaults, **kwargs}


# ---------------------------------------------------------------------------
# Embed color mapping
# ---------------------------------------------------------------------------

def test_color_critical():
    embed = _build_embed(_make_alert(severity="critical"), _make_ai())
    assert embed["color"] == 0xED4245


def test_color_warning():
    embed = _build_embed(_make_alert(severity="warning"), _make_ai())
    assert embed["color"] == 0xFEE75C


def test_color_info():
    embed = _build_embed(_make_alert(severity="info", status="up"), _make_ai())
    assert embed["color"] == 0x57F287


def test_color_unknown_falls_back_to_grey():
    embed = _build_embed(_make_alert(severity="unknown"), _make_ai())
    assert embed["color"] == 0x99AAB5


# ---------------------------------------------------------------------------
# Title format and length
# ---------------------------------------------------------------------------

def test_title_contains_service_and_status():
    embed = _build_embed(_make_alert(service_name="nginx", status="down"), _make_ai())
    assert "nginx" in embed["title"]
    assert "DOWN" in embed["title"]


def test_title_never_exceeds_discord_limit():
    long_name = "x" * 300
    embed = _build_embed(_make_alert(service_name=long_name), _make_ai())
    assert len(embed["title"]) <= 256


# ---------------------------------------------------------------------------
# Fields content and length
# ---------------------------------------------------------------------------

def test_alert_message_field_present():
    embed = _build_embed(_make_alert(message="OOM killer triggered"), _make_ai())
    messages = [f["value"] for f in embed["fields"] if f["name"] == "Alert Message"]
    assert messages and "OOM killer triggered" in messages[0]


def test_alert_message_truncated_at_1024():
    long_msg = "x" * 2000
    embed = _build_embed(_make_alert(message=long_msg), _make_ai())
    msg_field = next(f for f in embed["fields"] if f["name"] == "Alert Message")
    assert len(msg_field["value"]) <= 1024


def test_ai_insight_field_present():
    embed = _build_embed(_make_alert(), _make_ai(insight="Test insight text"))
    insight_fields = [f for f in embed["fields"] if "Insight" in f["name"]]
    assert insight_fields
    assert "Test insight text" in insight_fields[0]["value"]


def test_suggested_actions_present():
    embed = _build_embed(_make_alert(), _make_ai(suggested_actions=["Step 1", "Step 2"]))
    action_fields = [f for f in embed["fields"] if "Actions" in f["name"]]
    assert action_fields
    assert "Step 1" in action_fields[0]["value"]


def test_no_actions_field_skipped():
    embed = _build_embed(_make_alert(), _make_ai(suggested_actions=[]))
    action_fields = [f for f in embed["fields"] if "Actions" in f["name"]]
    assert not action_fields


def test_max_five_actions():
    actions = [f"Action {i}" for i in range(10)]
    embed = _build_embed(_make_alert(), _make_ai(suggested_actions=actions))
    action_field = next(f for f in embed["fields"] if "Actions" in f["name"])
    # Only first 5 should appear
    assert "Action 5" not in action_field["value"]
    assert "Action 4" in action_field["value"]


def test_source_field_formatted():
    embed = _build_embed(_make_alert(source="uptime_kuma"), _make_ai())
    source_fields = [f for f in embed["fields"] if f["name"] == "Source"]
    assert source_fields
    assert source_fields[0]["value"] == "Uptime Kuma"


# ---------------------------------------------------------------------------
# Embed structure
# ---------------------------------------------------------------------------

def test_embed_has_required_keys():
    embed = _build_embed(_make_alert(), _make_ai())
    assert "title" in embed
    assert "color" in embed
    assert "fields" in embed
    assert "footer" in embed
    assert "timestamp" in embed


def test_footer_text():
    embed = _build_embed(_make_alert(), _make_ai())
    assert embed["footer"]["text"] == "Homelab AI Sentinel"


# ---------------------------------------------------------------------------
# Malformed AI response handling
# ---------------------------------------------------------------------------

def test_non_list_actions_treated_as_empty():
    # Malformed AI response: suggested_actions is a string, not a list
    embed = _build_embed(_make_alert(), _make_ai(suggested_actions="Check logs"))
    action_fields = [f for f in embed["fields"] if "Actions" in f["name"]]
    assert not action_fields  # treated as no actions — field omitted


def test_non_string_insight_coerced_to_string():
    # Malformed AI response: insight is a number
    embed = _build_embed(_make_alert(), _make_ai(insight=42))
    insight_fields = [f for f in embed["fields"] if "Insight" in f["name"]]
    assert insight_fields
    assert "42" in insight_fields[0]["value"]
