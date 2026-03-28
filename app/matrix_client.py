"""
Matrix notification client.

Sends alerts to a Matrix room via the Client-Server API (no matrix-nio required).
Supports matrix.org or any self-hosted homeserver: Synapse, Dendrite, Conduit.

Messages use m.text with a formatted_body (HTML) so Element and most Matrix
clients render bold headers and bullet lists correctly. Plain text fallback
is always included for clients that don't support formatted_body.

Transaction IDs use uuid4 to satisfy the Matrix idempotency spec — the
server deduplicates retried PUTs with the same txn_id, so the ID must be
unique per message.

Security: API token is passed in an Authorization header, never in the URL,
so it does not appear in server access logs or requests exception strings.

Env vars:
  MATRIX_HOMESERVER   — homeserver URL, e.g. https://matrix.org
                        or http://synapse:8008 for self-hosted
  MATRIX_ACCESS_TOKEN — bot user access token
                        (Element: Settings → Security & Privacy → Access Token)
  MATRIX_ROOM_ID      — room ID in the form !abc123:matrix.org
                        (not the human-readable room alias — use the ID)
"""

import html
import logging
import os
import uuid
from typing import Any
from urllib.parse import quote

import requests

from .alert_parser import NormalizedAlert
from .utils import _validate_url

logger = logging.getLogger(__name__)

_STATUS_EMOJI = {"down": "🔴", "up": "🟢", "warning": "🟡", "unknown": "🔵"}


def _build_message(alert: NormalizedAlert, ai: dict[str, Any]) -> tuple[str, str]:
    """Return (plain_text, html_text) for the Matrix message."""
    icon = _STATUS_EMOJI.get(alert.status, "🔵")

    insight = ai.get("insight", "")
    if not isinstance(insight, str):
        insight = str(insight)

    actions = ai.get("suggested_actions", [])
    if not isinstance(actions, list):
        actions = []

    # Plain text (required — fallback for clients without HTML support)
    header = f"{icon} [{alert.severity.upper()}] {alert.service_name} is {alert.status}"
    plain_lines = [header, "", alert.message[:500], "", insight[:500]]
    if actions:
        plain_lines += ["", "Suggested Actions:"] + [f"• {str(a)}" for a in actions[:5]]
    plain = "\n".join(plain_lines)

    # HTML (rendered by Element and most modern Matrix clients)
    sev = html.escape(alert.severity.upper())
    name = html.escape(alert.service_name)
    status_str = html.escape(alert.status)
    msg = html.escape(alert.message[:500])
    ins = html.escape(insight[:500])

    html_parts = [
        f"<p>{icon} <strong>[{sev}] {name} is {status_str}</strong></p>",
        f"<p>{msg}</p>",
        f"<p><em>{ins}</em></p>",
    ]
    if actions:
        items = "".join(f"<li>{html.escape(str(a))}</li>" for a in actions[:5])
        html_parts.append(f"<p><strong>Suggested Actions:</strong></p><ul>{items}</ul>")

    return plain, "".join(html_parts)


def post_alert(alert: NormalizedAlert, ai: dict[str, Any]) -> None:
    """
    Send the alert to the configured Matrix room.
    Raises requests.HTTPError on non-2xx response.
    Silently skips if any required env var is not set.
    """
    homeserver = os.environ.get("MATRIX_HOMESERVER", "")
    token = os.environ.get("MATRIX_ACCESS_TOKEN", "")
    room_id = os.environ.get("MATRIX_ROOM_ID", "")
    if not homeserver or not token or not room_id:
        return
    if not _validate_url(homeserver, "MATRIX_HOMESERVER"):
        return

    plain, formatted = _build_message(alert, ai)
    # uuid4 ensures uniqueness even if two messages are sent within the same millisecond
    txn_id = f"sentinel_{uuid.uuid4().hex}"

    resp = requests.put(
        f"{homeserver.rstrip('/')}/_matrix/client/v3/rooms/{quote(room_id, safe='')}/"
        f"send/m.room.message/{txn_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "msgtype": "m.text",
            "body": plain,
            "format": "org.matrix.custom.html",
            "formatted_body": formatted,
        },
        timeout=10,
    )
    resp.raise_for_status()
