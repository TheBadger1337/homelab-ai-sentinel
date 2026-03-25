"""
iMessage notification client via Bluebubbles REST API.

⚠️  REQUIRES APPLE HARDWARE — iMessage has no official API and no cloud relay.
    A Mac (mini, MacBook, or desktop) must run the Bluebubbles server app 24/7.
    This client cannot be tested on Android, Linux, or Windows hosts without
    a Mac bridge on the network. See README for setup instructions.

    Alternative bridge: AirMessage (https://airmessage.org) uses a different
    REST API pattern and is not supported by this client.

How it works:
  1. Install Bluebubbles (https://bluebubbles.app) on a Mac that stays on
  2. Bluebubbles exposes a local REST API (default port 1234)
  3. This client POSTs to /api/v1/message/text with the configured password
  4. Bluebubbles uses the macOS Messages app to deliver the iMessage

Env vars:
  IMESSAGE_URL      — Bluebubbles server URL, e.g. http://your-mac.local:1234
                      or https://bluebubbles.yourdomain.com if exposed externally
  IMESSAGE_PASSWORD — Bluebubbles server password (set during first launch)
  IMESSAGE_TO       — recipient iMessage address: phone (+15551234567) or Apple ID email
"""

import logging
import os
from typing import Any

import requests

from .alert_parser import NormalizedAlert

logger = logging.getLogger(__name__)

_STATUS_EMOJI = {"down": "🔴", "up": "🟢", "warning": "🟡", "unknown": "🔵"}


def _build_message(alert: NormalizedAlert, ai: dict[str, Any]) -> str:
    icon = _STATUS_EMOJI.get(alert.status, "🔵")

    insight = ai.get("insight", "")
    if not isinstance(insight, str):
        insight = str(insight)

    actions = ai.get("suggested_actions", [])
    if not isinstance(actions, list):
        actions = []

    lines = [
        f"{icon} [{alert.severity.upper()}] {alert.service_name} is {alert.status}",
        "",
        alert.message[:500],
        "",
        insight[:500],
    ]
    if actions:
        lines += ["", "Suggested Actions:"] + [f"• {str(a)}" for a in actions[:5]]

    return "\n".join(lines)


def post_alert(alert: NormalizedAlert, ai: dict[str, Any]) -> None:
    """
    POST the alert to Bluebubbles REST API.
    Raises requests.HTTPError on non-2xx response.
    Silently skips if IMESSAGE_URL, IMESSAGE_PASSWORD, or IMESSAGE_TO is not set.
    """
    url = os.environ.get("IMESSAGE_URL", "")
    password = os.environ.get("IMESSAGE_PASSWORD", "")
    recipient = os.environ.get("IMESSAGE_TO", "")
    if not url or not password or not recipient:
        return

    message = _build_message(alert, ai)

    resp = requests.post(
        f"{url.rstrip('/')}/api/v1/message/text",
        params={"password": password},
        json={
            "chatGuid": f"iMessage;-;{recipient}",
            "message": message,
            "method": "private-api",
            "subject": f"[Sentinel] {alert.service_name} — {alert.status.upper()}",
        },
        timeout=15,
    )
    resp.raise_for_status()
