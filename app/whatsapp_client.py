"""
WhatsApp Cloud API integration.

Sends a plain-text message via the Meta WhatsApp Cloud API.

Requirements:
  - Meta Developer account with a Business Portfolio
  - WhatsApp product added to your Meta app
  - Phone Number ID from the WhatsApp dashboard
  - Access token (temporary 24hr token for testing, or a System User token
    for production use)
  - Recipient number added and verified in the WhatsApp test contacts

Free tier: 1,000 service conversations/month — sufficient for homelab volumes.

Important: WhatsApp Cloud API returns HTTP 200 even for some error conditions
(e.g. expired token, unverified recipient). This client checks the response
body for a Meta error object and raises appropriately so failures are not
silently swallowed.
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

_GRAPH_API_VERSION = "v22.0"


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
            lines.append(f"• {str(action)}")

    lines += ["", "— Homelab AI Sentinel"]
    return "\n".join(lines)


def post_alert(alert: NormalizedAlert, ai: dict[str, Any]) -> None:
    """
    Send the alert via WhatsApp Cloud API.

    Raises requests.HTTPError on non-2xx HTTP status.
    Raises RuntimeError if Meta returns HTTP 200 with an error body
    (a documented Meta API behaviour for auth failures and policy errors).
    Silently skips if WHATSAPP_TOKEN, WHATSAPP_PHONE_ID, or WHATSAPP_TO
    is not set.
    """
    token = os.environ.get("WHATSAPP_TOKEN", "")
    phone_id = os.environ.get("WHATSAPP_PHONE_ID", "")
    to_number = os.environ.get("WHATSAPP_TO", "")
    if not token or not phone_id or not to_number:
        return

    url = f"https://graph.facebook.com/{_GRAPH_API_VERSION}/{phone_id}/messages"
    text = _build_message(alert, ai)

    payload = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "text",
        "text": {
            "preview_url": False,
            "body": text[:4096],  # WhatsApp text message limit
        },
    }

    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=10,
    )
    resp.raise_for_status()

    # Meta Graph API returns HTTP 200 with an error body for certain failures
    # (expired token, unverified recipient, policy violations). A plain
    # raise_for_status() will not catch these — inspect the body explicitly.
    try:
        body = resp.json()
    except ValueError:
        return  # non-JSON 200 — treat as success (unusual but not an error)

    if isinstance(body, dict) and "error" in body:
        err = body["error"]
        code = err.get("code", "unknown")
        message = err.get("message", "unknown error")
        # Do not include the token in this error — it lives in the Authorization
        # header and is not echoed by Meta, but belt-and-suspenders.
        raise RuntimeError(
            f"WhatsApp API error (code {code}): {message}"
        )
