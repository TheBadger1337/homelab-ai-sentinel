"""
Discord webhook integration.

Posts a formatted embed containing the normalized alert info
plus the AI-generated Insight and Suggested Actions.
"""

import os
import re
from datetime import datetime, timezone
from typing import Any

import requests

from .alert_parser import NormalizedAlert
from .utils import _validate_url

# Discord renders @everyone and @here as visual mentions in embed fields.
# Strip them by inserting a zero-width space so the text is inert.
_MENTION_SUBS = [("@everyone", "@\u200beveryone"), ("@here", "@\u200bhere")]

# Discord renders <@USERID>, <@!USERID> (nickname), and <@&ROLEID> as
# clickable mentions that ping users or roles. Remove them entirely from
# untrusted alert content so a crafted payload cannot mention arbitrary users.
_USER_MENTION_RE = re.compile(r"<@[!&]?[0-9]+>")


def _strip_mentions(text: str) -> str:
    for src, dst in _MENTION_SUBS:
        text = text.replace(src, dst)
    text = _USER_MENTION_RE.sub("", text)
    return text


_COLORS = {
    "critical": 0xED4245,   # red
    "warning":  0xFEE75C,   # yellow
    "info":     0x57F287,   # green
    "unknown":  0x99AAB5,   # grey
}

_STATUS_EMOJI = {
    "down":    "🔴",
    "up":      "🟢",
    "warning": "🟡",
    "unknown": "⚪",
}


def _build_embed(alert: NormalizedAlert, ai: dict[str, Any]) -> dict[str, Any]:
    color = _COLORS.get(alert.severity, _COLORS["unknown"])
    emoji = _STATUS_EMOJI.get(alert.status, "⚪")

    title = f"{emoji} [{alert.severity.upper()}] {alert.service_name} — {alert.status.upper()}"

    fields = [
        {
            "name": "Alert Message",
            "value": _strip_mentions(alert.message[:1024]),
            "inline": False,
        },
        {
            "name": "Source",
            "value": _strip_mentions(alert.source.replace("_", " ").title()),
            "inline": True,
        },
        {
            "name": "Severity",
            "value": alert.severity.capitalize(),
            "inline": True,
        },
    ]

    insight = ai.get("insight", "No insight available.")
    if not isinstance(insight, str):
        insight = str(insight)
    fields.append({
        "name": "🤖 AI Insight",
        "value": _strip_mentions(insight[:1024]),
        "inline": False,
    })

    actions = ai.get("suggested_actions", [])
    if not isinstance(actions, list):
        actions = []
    if actions:
        action_text = "\n".join(f"• {_strip_mentions(a)}" for a in actions[:5])
        fields.append({
            "name": "⚡ Suggested Actions",
            "value": action_text[:1024],
            "inline": False,
        })

    return {
        "title": title[:256],
        "color": color,
        "fields": fields,
        "footer": {"text": "Homelab AI Sentinel"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def post_alert(alert: NormalizedAlert, ai: dict[str, Any]) -> None:
    """
    Post the alert embed to Discord.
    Raises requests.HTTPError on non-2xx response.
    Silently skips if no URL configured.
    Disable via DISCORD_DISABLED=true (handled centrally in notify.py).
    """
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not webhook_url:
        return
    if not _validate_url(webhook_url, "DISCORD_WEBHOOK_URL"):
        return

    embed = _build_embed(alert, ai)
    payload = {"embeds": [embed]}

    resp = requests.post(webhook_url, json=payload, timeout=10)
    resp.raise_for_status()
