"""
Telegram Bot API integration.

Sends a formatted HTML message via the sendMessage endpoint.
"""

import html
import os
from typing import Any

import requests

from .alert_parser import NormalizedAlert

_SEVERITY_EMOJI = {
    "critical": "🔴",
    "warning":  "🟡",
    "info":     "🟢",
    "unknown":  "⚪",
}

_API_BASE = "https://api.telegram.org"


def _esc(value: str) -> str:
    """Escape a string for safe inclusion in an HTML Telegram message."""
    return html.escape(value)


def _build_message(alert: NormalizedAlert, ai: dict[str, Any]) -> str:
    emoji = _SEVERITY_EMOJI.get(alert.severity, "⚪")
    header = (
        f"{emoji} <b>[{_esc(alert.severity.upper())}] "
        f"{_esc(alert.service_name)} — {_esc(alert.status.upper())}</b>"
    )

    source = _esc(alert.source.replace("_", " ").title())

    insight = ai.get("insight", "No insight available.")
    if not isinstance(insight, str):
        insight = str(insight)

    lines = [
        header,
        f"<b>Source:</b> {source}",
        f"<b>Message:</b> {_esc(alert.message[:500])}",
        "",
        "<b>🤖 AI Insight</b>",
        _esc(insight[:1000]),
    ]

    actions = ai.get("suggested_actions", [])
    if not isinstance(actions, list):
        actions = []
    if actions:
        lines.append("")
        lines.append("<b>⚡ Suggested Actions</b>")
        for action in actions[:5]:
            lines.append(f"• {_esc(str(action))}")

    lines.append("")
    lines.append("<i>Homelab AI Sentinel</i>")

    return "\n".join(lines)


def post_alert(alert: NormalizedAlert, ai: dict[str, Any]) -> None:
    """
    Send the alert to Telegram via Bot API sendMessage.
    Raises requests.HTTPError on non-2xx response.
    Silently skips if TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is not set.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return

    text = _build_message(alert, ai)

    resp = requests.post(
        f"{_API_BASE}/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
        timeout=10,
    )
    resp.raise_for_status()
