"""
Discord webhook integration.

Posts a formatted embed containing the normalized alert info
plus the AI-generated Insight and Suggested Actions.
"""

import os
from datetime import datetime, timezone

import requests

from .alert_parser import NormalizedAlert

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


def _build_embed(alert: NormalizedAlert, ai: dict) -> dict:
    color = _COLORS.get(alert.severity, _COLORS["unknown"])
    emoji = _STATUS_EMOJI.get(alert.status, "⚪")

    title = f"{emoji} [{alert.severity.upper()}] {alert.service_name} — {alert.status.upper()}"

    fields = [
        {
            "name": "Alert Message",
            "value": alert.message[:1024],
            "inline": False,
        },
        {
            "name": "Source",
            "value": alert.source.replace("_", " ").title(),
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
        "value": insight[:1024],
        "inline": False,
    })

    actions = ai.get("suggested_actions", [])
    if not isinstance(actions, list):
        actions = []
    if actions:
        action_text = "\n".join(f"• {a}" for a in actions[:5])
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


def post_alert(alert: NormalizedAlert, ai: dict) -> None:
    """
    Post the alert embed to Discord.
    Raises requests.HTTPError on non-2xx response.
    Silently skips if DISCORD_DISABLED=true or no URL configured.
    """
    if os.environ.get("DISCORD_DISABLED", "").lower() == "true":
        return

    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not webhook_url:
        return

    embed = _build_embed(alert, ai)
    payload = {"embeds": [embed]}

    resp = requests.post(webhook_url, json=payload, timeout=10)
    resp.raise_for_status()
