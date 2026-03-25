"""
Slack incoming-webhook integration.

Posts a Block Kit message containing the normalized alert info
plus the AI-generated Insight and Suggested Actions.
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


def _build_message(alert: NormalizedAlert, ai: dict[str, Any]) -> dict[str, Any]:
    emoji = _SEVERITY_EMOJI.get(alert.severity, "⚪")
    header = (
        f"{emoji} [{alert.severity.upper()}] {alert.service_name} — {alert.status.upper()}"
    )

    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": header[:150], "emoji": True},
        },
        {
            "type": "section",
            "fields": [
                {
                    "type": "mrkdwn",
                    "text": f"*Source:*\n{alert.source.replace('_', ' ').title()}",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*Severity:*\n{alert.severity.capitalize()}",
                },
            ],
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Alert Message*\n{alert.message[:1000]}",
            },
        },
    ]

    insight = ai.get("insight", "No insight available.")
    if not isinstance(insight, str):
        insight = str(insight)
    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f"*🤖 AI Insight*\n{insight[:2000]}",
        },
    })

    actions = ai.get("suggested_actions", [])
    if not isinstance(actions, list):
        actions = []
    if actions:
        action_lines = "\n".join(f"• {a}" for a in actions[:5])
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*⚡ Suggested Actions*\n{action_lines[:2900]}",
            },
        })

    blocks.append({"type": "divider"})
    blocks.append({
        "type": "context",
        "elements": [
            {"type": "mrkdwn", "text": "_Homelab AI Sentinel_"},
        ],
    })

    return {"text": header[:150], "blocks": blocks}


def post_alert(alert: NormalizedAlert, ai: dict[str, Any]) -> None:
    """
    Post the alert to Slack via incoming webhook.
    Raises requests.HTTPError on non-2xx response.
    Silently skips if SLACK_WEBHOOK_URL is not set.
    """
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL", "")
    if not webhook_url:
        return

    payload = _build_message(alert, ai)
    resp = requests.post(webhook_url, json=payload, timeout=10)
    resp.raise_for_status()
