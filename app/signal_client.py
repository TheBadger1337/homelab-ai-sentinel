"""
Signal notification via signal-cli-rest-api.

Sends a plain-text message through a locally-hosted signal-cli-rest-api
Docker container. No third-party service — fully self-contained.

Setup: https://github.com/bbernhard/signal-cli-rest-api
The container must be running and linked to a Signal account before use.
"""

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


def _build_message(alert: NormalizedAlert, ai: dict[str, Any]) -> str:
    emoji = _SEVERITY_EMOJI.get(alert.severity, "⚪")
    header = f"{emoji} [{alert.severity.upper()}] {alert.service_name} — {alert.status.upper()}"

    source = alert.source.replace("_", " ").title()

    insight = ai.get("insight", "No insight available.")
    if not isinstance(insight, str):
        insight = str(insight)

    actions = ai.get("suggested_actions", [])
    if not isinstance(actions, list):
        actions = []

    lines = [
        header,
        f"Source: {source}",
        f"Message: {alert.message[:500]}",
        "",
        "🤖 AI Insight",
        insight[:1000],
    ]

    if actions:
        lines.append("")
        lines.append("⚡ Suggested Actions")
        for action in actions[:5]:
            lines.append(f"• {action}")

    lines += ["", "— Homelab AI Sentinel"]
    return "\n".join(lines)


def post_alert(alert: NormalizedAlert, ai: dict[str, Any]) -> None:
    """
    Send the alert via signal-cli-rest-api /v2/send.
    Raises requests.HTTPError on non-2xx response.
    Silently skips if SIGNAL_API_URL, SIGNAL_SENDER, or SIGNAL_RECIPIENT is not set.
    """
    api_url = os.environ.get("SIGNAL_API_URL", "")
    sender = os.environ.get("SIGNAL_SENDER", "")
    recipient = os.environ.get("SIGNAL_RECIPIENT", "")
    if not api_url or not sender or not recipient:
        return

    text = _build_message(alert, ai)

    resp = requests.post(
        f"{api_url}/v2/send",
        json={
            "message": text,
            "number": sender,
            "recipients": [recipient],
        },
        timeout=10,
    )
    resp.raise_for_status()
