"""
Gemini API integration.

Calls gemini-2.5-flash with alert context and returns a structured
AI Insight + Suggested Actions dict.
"""

import json
import logging
import os
from typing import Any

import requests

from .alert_parser import NormalizedAlert

logger = logging.getLogger(__name__)

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


_FIELD_MAX = 500  # max chars for any single alert field sent to the AI prompt


def _fallback(reason: str) -> dict[str, Any]:
    return {
        "insight": f"AI analysis unavailable ({reason}).",
        "suggested_actions": [
            "Check the raw alert payload for details.",
            "Review service logs manually.",
        ],
    }


def get_ai_insight(alert: NormalizedAlert) -> dict[str, Any]:
    """
    Call Gemini and return {"insight": str, "suggested_actions": list[str]}.
    Falls back to a generic response on any API error.
    """
    token = os.environ.get("GEMINI_TOKEN", "")
    if not token:
        return _fallback("GEMINI_TOKEN not set")

    # Cap field lengths before inserting into the prompt to limit the
    # prompt injection surface — alert data is untrusted external input.
    details_str = json.dumps(alert.details, indent=2) if alert.details else "None"
    prompt = _USER_TEMPLATE.format(
        source=alert.source[:_FIELD_MAX],
        service_name=alert.service_name[:_FIELD_MAX],
        status=alert.status[:_FIELD_MAX],
        severity=alert.severity[:_FIELD_MAX],
        message=alert.message[:_FIELD_MAX],
        details=details_str[:_FIELD_MAX],
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
    except (json.JSONDecodeError, KeyError, IndexError, AttributeError) as exc:
        logger.warning("AI response parse error: %s", exc)
        return _fallback(f"parse error: {type(exc).__name__}")
    except requests.RequestException as exc:
        safe_msg = str(exc).replace(token, "***")  # mask API key in logs
        logger.warning("Gemini API error: %s", safe_msg)
        return _fallback("API error")
    except Exception as exc:  # noqa: BLE001 — intentional broad catch; must never raise
        logger.exception("Unexpected AI error: %s", exc)
        return _fallback("unexpected error")
