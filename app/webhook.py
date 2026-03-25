"""
Flask blueprint: POST /webhook

Flow:
  1. Parse incoming JSON payload into a NormalizedAlert
  2. Call Claude for AI Insight + Suggested Actions
  3. Post Discord embed
  4. Return JSON response
"""

import logging

import requests
from flask import Blueprint, jsonify, request

from .alert_parser import parse_alert
from .claude_client import get_ai_insight
from .discord_client import post_alert

logger = logging.getLogger(__name__)
webhook_bp = Blueprint("webhook", __name__)


@webhook_bp.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@webhook_bp.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Invalid or missing JSON body"}), 400

    # 1. Normalize
    try:
        alert = parse_alert(data)
    except Exception as exc:
        logger.error("Alert parsing failed: %s", exc)
        return jsonify({"error": "Failed to parse alert payload", "detail": str(exc)}), 500
    logger.info("Alert received: source=%s service=%s status=%s",
                alert.source, alert.service_name, alert.status)

    # 2. AI analysis
    ai = get_ai_insight(alert)
    logger.info("AI insight generated for %s", alert.service_name)

    # 3. Discord
    discord_error = None
    try:
        post_alert(alert, ai)
    except requests.RequestException as exc:
        discord_error = str(exc)
        logger.warning("Discord post failed: %s", discord_error)

    response = {
        "status": "processed",
        "alert": {
            "source": alert.source,
            "service": alert.service_name,
            "alert_status": alert.status,
            "severity": alert.severity,
        },
        "ai_insight": ai.get("insight"),
        "suggested_actions": ai.get("suggested_actions", []),
    }
    if discord_error:
        response["discord_error"] = discord_error

    return jsonify(response), 200
