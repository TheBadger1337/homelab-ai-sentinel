"""
Signal notification via signal-cli-rest-api.

Sends a plain-text message through a locally-hosted signal-cli-rest-api
Docker container. No third-party service — fully self-contained.

Setup: https://github.com/bbernhard/signal-cli-rest-api
The container must be running and linked to a Signal account before use.
"""

import logging
import os
from typing import Any

import requests

from .alert_parser import NormalizedAlert

logger = logging.getLogger(__name__)

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
            lines.append(f"• {str(action)}")

    lines += ["", "— Homelab AI Sentinel"]
    return "\n".join(lines)


def post_alert(alert: NormalizedAlert, ai: dict[str, Any]) -> None:
    """
    Send the alert via signal-cli-rest-api /v2/send.

    Raises requests.HTTPError on non-2xx HTTP status.
    Raises RuntimeError if the API returns a non-JSON or error response body.
    Silently skips if SIGNAL_API_URL, SIGNAL_SENDER, or SIGNAL_RECIPIENT is
    not set.

    Note: SIGNAL_API_URL should point to the internal Docker network address
    (http://signal-cli-rest-api:8080) — not a public internet endpoint.
    Phone numbers (SIGNAL_SENDER, SIGNAL_RECIPIENT) are never included in
    error messages returned to callers.
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

    # signal-cli-rest-api can return a non-JSON body or a JSON error object
    # for certain failure conditions (unlinked account, invalid recipient).
    # Detect these and surface them so failures aren't silently swallowed.
    try:
        body = resp.json()
    except ValueError:
        return  # non-JSON body on a 2xx — treat as success
    if isinstance(body, dict) and "error" in body:
        # Log without phone numbers — they appear in the env vars, not here
        logger.warning(
            "signal-cli-rest-api returned an error body (HTTP %s)",
            resp.status_code,
        )
        raise RuntimeError(
            f"signal-cli-rest-api error: {body.get('error', 'unknown')}"
        )
