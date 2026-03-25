"""
Ntfy integration.

Posts a plain-text alert to an ntfy topic via HTTP.
Supports priority levels and emoji tags mapped from alert severity.
"""

import os
from typing import Any

import requests

from .alert_parser import NormalizedAlert

_PRIORITY = {
    "critical": "urgent",
    "warning":  "high",
    "info":     "default",
    "unknown":  "default",
}

_TAGS = {
    "critical": "rotating_light",
    "warning":  "warning",
    "info":     "white_check_mark",
    "unknown":  "question",
}


def _build_payload(alert: NormalizedAlert, ai: dict[str, Any]) -> dict[str, Any]:
    insight = ai.get("insight", "No insight available.")
    if not isinstance(insight, str):
        insight = str(insight)

    actions = ai.get("suggested_actions", [])
    if not isinstance(actions, list):
        actions = []

    title = f"[{alert.severity.upper()}] {alert.service_name} — {alert.status.upper()}"

    lines = [
        alert.message[:500],
        "",
        insight[:500],
    ]
    if actions:
        lines.append("")
        for action in actions[:5]:
            lines.append(f"• {str(action)}")

    return {
        "title": title[:250],
        "message": "\n".join(lines),
        "priority": _PRIORITY.get(alert.severity, "default"),
        "tags": [_TAGS.get(alert.severity, "question")],
    }


def post_alert(alert: NormalizedAlert, ai: dict[str, Any]) -> None:
    """
    POST the alert to the configured ntfy topic.
    Raises requests.HTTPError on non-2xx response.
    Silently skips if NTFY_URL is not set.
    """
    ntfy_url = os.environ.get("NTFY_URL", "")
    if not ntfy_url:
        return

    payload = _build_payload(alert, ai)

    resp = requests.post(
        ntfy_url,
        json=payload,
        timeout=10,
    )
    resp.raise_for_status()
