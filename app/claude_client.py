"""
Gemini API integration.

Calls gemini-2.5-flash with alert context and returns a structured
AI Insight + Suggested Actions dict.
"""

import json
import os

import requests

from .alert_parser import NormalizedAlert

_GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.5-flash:generateContent"
)

_SYSTEM_PROMPT = """\
You are a homelab monitoring assistant. Your job is to help homelab operators
quickly understand and respond to monitoring alerts.

When given an alert, produce a concise analysis. Be practical and specific —
assume the operator is comfortable with Linux, Docker, and self-hosted services.

Always respond with valid JSON only — no markdown fences, no extra text.
"""

_USER_TEMPLATE = """\
A monitoring alert has fired in my homelab. Analyze it and respond with JSON.

Alert Details:
  Source:       {source}
  Service:      {service_name}
  Status:       {status}
  Severity:     {severity}
  Message:      {message}
  Extra Context: {details}

Respond with this exact JSON schema:
{{
  "insight": "<2-3 sentence analysis of what this alert likely means and its probable cause>",
  "suggested_actions": [
    "<action 1>",
    "<action 2>",
    "<action 3 — add more if genuinely needed, max 5>"
  ]
}}
"""


def get_ai_insight(alert: NormalizedAlert) -> dict:
    """
    Call Gemini and return {"insight": str, "suggested_actions": list[str]}.
    Falls back to a generic response on any API error.
    """
    token = os.environ.get("GEMINI_TOKEN", "")
    if not token:
        return {
            "insight": "AI analysis unavailable (GEMINI_TOKEN not set).",
            "suggested_actions": [
                "Check the raw alert payload for details.",
                "Review service logs manually.",
            ],
        }

    details_str = json.dumps(alert.details, indent=2) if alert.details else "None"

    prompt = _USER_TEMPLATE.format(
        source=alert.source,
        service_name=alert.service_name,
        status=alert.status,
        severity=alert.severity,
        message=alert.message,
        details=details_str,
    )

    payload = {
        "systemInstruction": {"parts": [{"text": _SYSTEM_PROMPT}]},
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": 1024,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }

    try:
        resp = requests.post(
            _GEMINI_URL,
            params={"key": token},
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        raw = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        # Gemini sometimes wraps JSON in markdown fences despite instructions
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1]
            raw = raw.rsplit("```", 1)[0].strip()
        return json.loads(raw)
    except (json.JSONDecodeError, KeyError, IndexError) as exc:
        return {
            "insight": f"AI analysis unavailable (parse error: {exc}).",
            "suggested_actions": [
                "Check the raw alert payload for details.",
                "Review service logs manually.",
            ],
        }
    except requests.RequestException as exc:
        # Mask the API key in the error message
        safe_msg = str(exc).replace(token, "***")
        return {
            "insight": f"AI analysis unavailable (API error: {safe_msg}).",
            "suggested_actions": [
                "Check the raw alert payload for details.",
                "Review service logs manually.",
            ],
        }
