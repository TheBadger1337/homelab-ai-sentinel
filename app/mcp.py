"""
MCP (Model Context Protocol) read-only API for Homelab AI Sentinel.

Exposes lightweight read endpoints so AI assistants (Claude, ChatGPT, etc.)
can query a running Sentinel instance via an MCP server.

Auth: if WEBHOOK_SECRET is set, requires `Authorization: Bearer <secret>`.
      If WEBHOOK_SECRET is unset, endpoints are open (same policy as /health).

Endpoints:
  GET /api/mcp/health    — operational state (always available)
  GET /api/mcp/alerts    — recent alerts (requires DB)
  GET /api/mcp/incidents — open + recently resolved incidents (requires DB)
"""

import logging
import os

from flask import Blueprint, jsonify, request

from .alert_db import db_available

logger = logging.getLogger(__name__)
mcp_bp = Blueprint("mcp", __name__, url_prefix="/api/mcp")


def _check_auth() -> bool:
    """Return True if the request is authorized to use MCP endpoints."""
    secret = os.environ.get("WEBHOOK_SECRET", "").strip()
    if not secret:
        return True  # No secret configured — open access (same as /health)
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        # Constant-time compare to prevent timing attacks
        import hmac
        return hmac.compare_digest(token, secret)
    return False


@mcp_bp.route("/health", methods=["GET"])
def mcp_health():
    """Return Sentinel operational state. Mirrors /health with MCP-friendly shape."""
    if not _check_auth():
        return jsonify({"error": "unauthorized — set Authorization: Bearer <WEBHOOK_SECRET>"}), 401

    from .alert_db import get_db_stats, get_dlq_count, get_security_summary
    from .llm_client import get_rpm_status

    payload: dict = {"status": "ok", "db": "disabled"}

    if db_available():
        try:
            stats = get_db_stats()
            sec = get_security_summary()
            payload["db"] = "connected"
            payload["alerts_total"] = stats.get("total_alerts", 0)
            payload["alerts_notified"] = stats.get("notified_count", 0)
            payload["dlq_pending"] = get_dlq_count()
            payload["security_events_24h"] = sec.get("total_24h", 0)
        except Exception as exc:
            logger.warning("mcp /health db stats error: %s", type(exc).__name__)
            payload["db"] = "error"

    try:
        rpm = get_rpm_status()
        payload["ai_rpm_used"] = rpm.get("used", 0)
        payload["ai_rpm_limit"] = rpm.get("limit", 0)
    except Exception:
        pass

    payload["workers"] = os.environ.get("WEB_CONCURRENCY", "1")
    return jsonify(payload), 200


@mcp_bp.route("/alerts", methods=["GET"])
def mcp_alerts():
    """
    Return recent alerts.

    Query params:
      limit    int  — max results (default 20, max 100)
      severity str  — filter: critical | warning | info
      since    int  — Unix timestamp — only return alerts newer than this
    """
    if not _check_auth():
        return jsonify({"error": "unauthorized"}), 401

    if not db_available():
        return jsonify({"error": "DB not available — set DB_PATH to enable alert history"}), 503

    try:
        from .alert_db import _get_conn
        limit = min(int(request.args.get("limit", 20)), 100)
        severity = request.args.get("severity", "").strip().lower()
        since = request.args.get("since", 0)
        try:
            since = int(since)
        except (TypeError, ValueError):
            since = 0

        with _get_conn() as conn:
            query = "SELECT id, service, severity, source, message, ai_insight, ts FROM alerts WHERE 1=1"
            params: list = []
            if severity:
                query += " AND severity = ?"
                params.append(severity)
            if since:
                query += " AND ts > ?"
                params.append(since)
            query += " ORDER BY ts DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(query, params).fetchall()

        alerts = [
            {
                "id": r[0],
                "service": r[1],
                "severity": r[2],
                "source": r[3],
                "message": r[4],
                "ai_insight": r[5],
                "ts": r[6],
            }
            for r in rows
        ]
        return jsonify({"alerts": alerts, "count": len(alerts)}), 200

    except Exception as exc:
        logger.error("mcp /alerts error: %s", type(exc).__name__)
        return jsonify({"error": "internal error"}), 500


@mcp_bp.route("/incidents", methods=["GET"])
def mcp_incidents():
    """
    Return incidents.

    Query params:
      status  str — open | resolved | all (default: open)
      limit   int — max results (default 10, max 50)
    """
    if not _check_auth():
        return jsonify({"error": "unauthorized"}), 401

    if not db_available():
        return jsonify({"error": "DB not available — set DB_PATH to enable incidents"}), 503

    try:
        from .alert_db import _get_conn
        status = request.args.get("status", "open").strip().lower()
        limit = min(int(request.args.get("limit", 10)), 50)

        with _get_conn() as conn:
            query = "SELECT id, title, status, severity, first_alert_ts, resolved_ts, alert_count, ai_summary FROM incidents WHERE 1=1"
            params: list = []
            if status != "all":
                query += " AND status = ?"
                params.append(status)
            query += " ORDER BY first_alert_ts DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(query, params).fetchall()

        incidents = [
            {
                "id": r[0],
                "title": r[1],
                "status": r[2],
                "severity": r[3],
                "opened_at": r[4],
                "resolved_at": r[5],
                "alert_count": r[6],
                "ai_summary": r[7],
            }
            for r in rows
        ]
        return jsonify({"incidents": incidents, "count": len(incidents)}), 200

    except Exception as exc:
        logger.error("mcp /incidents error: %s", type(exc).__name__)
        return jsonify({"error": "internal error"}), 500
