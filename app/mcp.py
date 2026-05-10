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
  GET /api/mcp/topology  — monitored service graph (always available)
"""

import hmac
import logging
import os

from flask import Blueprint, jsonify, request

from .alert_db import db_available

logger = logging.getLogger(__name__)
mcp_bp = Blueprint("mcp", __name__, url_prefix="/api/mcp")


def _check_auth() -> bool:
    """Return True if the request is authorized to use MCP endpoints.

    Prefers MCP_TOKEN env var; falls back to WEBHOOK_SECRET with a deprecation
    warning so MCP auth can be rotated independently from webhook auth.
    """
    token = os.environ.get("MCP_TOKEN")
    if token is None:
        fallback = os.environ.get("WEBHOOK_SECRET", "").strip()
        if fallback:
            logger.warning(
                "MCP_TOKEN not set; falling back to WEBHOOK_SECRET — "
                "set MCP_TOKEN separately to decouple MCP and webhook auth"
            )
        token = fallback
    else:
        token = token.strip()

    if not token:
        return True  # No secret configured — open access (same as /health)

    auth_header = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    return hmac.compare_digest(auth_header, token)


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
    except Exception as exc:
        logger.debug("RPM stats unavailable: %s", exc)

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
            query = "SELECT id, service, severity, source, message, insight, ts FROM alerts WHERE 1=1"
            params: list = []
            if severity:
                query += " AND severity = ?"
                params.append(severity)
            if since:
                query += " AND ts > ?"
                params.append(since)
            query += " ORDER BY ts DESC LIMIT ?"
            params.append(limit)
            try:
                rows = conn.execute(query, params).fetchall()
            except Exception as exc:
                logger.exception("mcp /alerts query failed: %s", type(exc).__name__)
                return jsonify({"error": "internal error"}), 500

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
            # Real incidents schema: id, ts_start, ts_end, service, status, severity,
            # root_cause, summary, alert_count, storm_id
            query = (
                "SELECT id, service || ' incident' AS title, status, severity,"
                " ts_start, ts_end, alert_count, summary FROM incidents WHERE 1=1"
            )
            params: list = []
            if status != "all":
                query += " AND status = ?"
                params.append(status)
            query += " ORDER BY ts_start DESC LIMIT ?"
            params.append(limit)
            try:
                rows = conn.execute(query, params).fetchall()
            except Exception as exc:
                logger.exception("mcp /incidents query failed: %s", type(exc).__name__)
                return jsonify({"error": "internal error"}), 500

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


@mcp_bp.route("/topology", methods=["GET"])
def mcp_topology():
    """
    Return the monitored service topology graph.

    Reads topology.yaml (TOPOLOGY_FILE env var or {RUNBOOK_DIR}/topology.yaml).
    Returns services with their dependencies, owners, and runbook references.
    Always available — does not require DB.
    """
    if not _check_auth():
        return jsonify({"error": "unauthorized"}), 401

    try:
        from .topology import _load_topology, _topology_path
        topo = _load_topology()
        if not topo:
            return jsonify({
                "topology": {},
                "source": None,
                "note": "No topology.yaml found. Set TOPOLOGY_FILE or place topology.yaml in RUNBOOK_DIR.",
            }), 200

        return jsonify({
            "topology": topo,
            "source": _topology_path(),
            "service_count": len(topo.get("services", topo) if isinstance(topo, dict) else topo),
        }), 200

    except Exception as exc:
        logger.error("mcp /topology error: %s", type(exc).__name__)
        return jsonify({"error": "internal error"}), 500
