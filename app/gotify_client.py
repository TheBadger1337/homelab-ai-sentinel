"""
Gotify notification client.

Self-hosted push notification server for desktop and Android.
https://gotify.net — Docker-native, no external dependencies or cloud accounts.

Priority levels map to Gotify's numeric scale (0–10):
  critical → 8 (HIGH — triggers notification sound on Android)
  warning  → 5 (NORMAL)
  info     → 2 (LOW)
  unknown  → 3

Env vars:
  GOTIFY_URL       — base URL of your Gotify instance, e.g. http://gotify:80
                     or https://gotify.yourdomain.com
  GOTIFY_APP_TOKEN — application token from Gotify dashboard → Apps → + Create App
"""

import logging
import os
from typing import Any

import requests

from .alert_parser import NormalizedAlert

logger = logging.getLogger(__name__)

_PRIORITY = {"critical": 8, "warning": 5, "info": 2, "unknown": 3}


def _build_payload(alert: NormalizedAlert, ai: dict[str, Any]) -> dict[str, Any]:
    insight = ai.get("insight", "")
    if not isinstance(insight, str):
        insight = str(insight)

    actions = ai.get("suggested_actions", [])
    if not isinstance(actions, list):
        actions = []

    title = f"[{alert.severity.upper()}] {alert.service_name} — {alert.status.upper()}"

    lines = [alert.message[:500], "", insight[:500]]
    if actions:
        lines.append("")
        for a in actions[:5]:
            lines.append(f"• {str(a)}")

    return {
        "title": title[:250],
        "message": "\n".join(lines),
        "priority": _PRIORITY.get(alert.severity, 3),
        "extras": {
            "client::display": {"contentType": "text/plain"},
        },
    }


def post_alert(alert: NormalizedAlert, ai: dict[str, Any]) -> None:
    """
    POST the alert to the configured Gotify server.
    Raises requests.HTTPError on non-2xx response.
    Silently skips if GOTIFY_URL or GOTIFY_APP_TOKEN is not set.
    """
    url = os.environ.get("GOTIFY_URL", "")
    token = os.environ.get("GOTIFY_APP_TOKEN", "")
    if not url or not token:
        return

    payload = _build_payload(alert, ai)

    resp = requests.post(
        f"{url.rstrip('/')}/message",
        headers={"X-Gotify-Key": token},
        json=payload,
        timeout=10,
    )
    resp.raise_for_status()
