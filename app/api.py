"""
REST API blueprint for the Sentinel Web UI.

Provides incident-centric endpoints consumed by the React SPA. Auth is
session-based (cookie), completely independent from the webhook HMAC token.

Security:
  - UI_PASSWORD env var required to enable the UI. If unset, all /api/*
    routes return 403.
  - Session tokens are 32-byte cryptographically random hex strings.
  - Sessions are stored in-memory per-worker with 24h expiry.
  - Cookies are HTTP-only, SameSite=Strict. Secure flag set when HTTPS detected.
  - All responses are JSON. User-generated content is never rendered as HTML.
"""

import hashlib
import json
import logging
import os
import secrets
import threading
import time
from functools import wraps
from typing import Any

from flask import Blueprint, Response, jsonify, request, stream_with_context

from .alert_db import (
    _get_conn,
    add_feedback,
    export_feedback,
    get_all_open_incidents,
    get_db_stats,
    get_dlq_count,
    get_feedback_for_alert,
    get_incident,
    get_security_summary,
    get_ui_config,
    resolve_incident,
    set_ui_config,
)
from .llm_client import get_rpm_status
from .notify import _CLIENTS, _is_configured, _is_disabled
from .pulse import get_pulse
from .topology import _load_topology
from .utils import _env_int, _sentinel_mode
from . import sse

logger = logging.getLogger(__name__)
api_bp = Blueprint("api", __name__, url_prefix="/api")

# ---------------------------------------------------------------------------
# Login rate limiter — prevent brute-force on /api/login
# ---------------------------------------------------------------------------
# Tracks failed attempts per IP. After 5 failures within 15 minutes, the IP
# is locked out for 15 minutes. Successful login resets the counter.

_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_WINDOW = 900  # 15 minutes
_login_attempts: dict[str, list[float]] = {}  # ip -> [timestamps of failures]
_login_lock = threading.Lock()

# JSON string fields stored as TEXT in SQLite — parse them for the API response.
_JSON_FIELDS = ("details", "actions")


def _parse_json_fields(row_dict: dict) -> dict:
    """Parse JSON-encoded TEXT fields in an alert row dict into native objects."""
    for field in _JSON_FIELDS:
        val = row_dict.get(field)
        if isinstance(val, str):
            try:
                row_dict[field] = json.loads(val)
            except (json.JSONDecodeError, ValueError):
                pass  # leave as string if it's not valid JSON
    return row_dict

# ---------------------------------------------------------------------------
# Session store — SQLite-backed, shared across all Gunicorn workers
# ---------------------------------------------------------------------------

_SESSION_TTL = 86400  # 24 hours


def _ui_password() -> str:
    """Return the effective UI password.

    Priority: UI_PASSWORD env var → hashed password in DB (ui_config table).
    The env var is plaintext (compared with secrets.compare_digest).
    The DB value is a bcrypt/scrypt hash (checked via hashlib).
    Returns empty string if no password is configured anywhere.
    """
    env_pw = os.environ.get("UI_PASSWORD", "")
    if env_pw:
        return env_pw
    # Fall through to DB-stored password
    return get_ui_config("ui_password_hash") or ""


def _ui_password_is_hashed() -> bool:
    """Return True if the active password came from the DB (hashed)."""
    return not os.environ.get("UI_PASSWORD", "") and bool(get_ui_config("ui_password_hash"))


def _hash_password(password: str) -> str:
    """Hash a password using scrypt (stdlib, no external deps).

    Returns 'salt_hex:hash_hex' for storage in ui_config.
    """
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    return salt.hex() + ":" + h.hex()


def _verify_hashed_password(password: str, stored: str) -> bool:
    """Verify a password against a scrypt hash from ui_config."""
    try:
        salt_hex, hash_hex = stored.split(":", 1)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
        h = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
        return secrets.compare_digest(h, expected)
    except (ValueError, TypeError):
        return False


def _ensure_sessions_table() -> None:
    """Create the ui_sessions table if it doesn't exist."""
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ui_sessions (
            token  TEXT PRIMARY KEY,
            expiry REAL NOT NULL
        )
    """)
    conn.commit()


def _create_session() -> str:
    """Create a new session token and store it in SQLite."""
    _ensure_sessions_table()
    token = secrets.token_hex(32)
    expiry = time.time() + _SESSION_TTL
    conn = _get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO ui_sessions (token, expiry) VALUES (?, ?)",
        (token, expiry),
    )
    # Prune expired sessions
    conn.execute("DELETE FROM ui_sessions WHERE expiry < ?", (time.time(),))
    conn.commit()
    return token


def _validate_session(token: str) -> bool:
    """Return True if the session token is valid and not expired."""
    _ensure_sessions_table()
    conn = _get_conn()
    row = conn.execute(
        "SELECT expiry FROM ui_sessions WHERE token = ?", (token,)
    ).fetchone()
    if row is None:
        return False
    if time.time() > row[0]:
        conn.execute("DELETE FROM ui_sessions WHERE token = ?", (token,))
        conn.commit()
        return False
    return True


def _delete_session(token: str) -> None:
    """Remove a session from the store."""
    try:
        conn = _get_conn()
        conn.execute("DELETE FROM ui_sessions WHERE token = ?", (token,))
        conn.commit()
    except Exception as exc:
        logger.warning("Failed to delete session: %s", type(exc).__name__)


def require_ui_auth(f):
    """Decorator: require valid UI session cookie."""
    @wraps(f)
    def decorated(*args, **kwargs):
        password = _ui_password()
        if not password:
            return jsonify({"error": "UI not configured — complete setup or set UI_PASSWORD"}), 403

        token = request.cookies.get("sentinel_session")
        if not token or not _validate_session(token):
            return jsonify({"error": "unauthorized"}), 401

        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------

def _check_login_rate(ip: str) -> bool:
    """Return True if the IP is allowed to attempt login, False if rate-limited."""
    now = time.time()
    cutoff = now - _LOGIN_WINDOW
    with _login_lock:
        attempts = _login_attempts.get(ip, [])
        # Prune old entries
        attempts = [t for t in attempts if t > cutoff]
        _login_attempts[ip] = attempts
        return len(attempts) < _LOGIN_MAX_ATTEMPTS


def _record_login_failure(ip: str) -> None:
    """Record a failed login attempt for rate limiting."""
    with _login_lock:
        _login_attempts.setdefault(ip, []).append(time.time())


def _reset_login_attempts(ip: str) -> None:
    """Clear failed attempts on successful login."""
    with _login_lock:
        _login_attempts.pop(ip, None)


@api_bp.route("/login", methods=["POST"])
def login():
    password = _ui_password()
    if not password:
        return jsonify({"error": "UI not configured — set UI_PASSWORD or complete first-run setup"}), 403

    # Rate limit check — prevent brute-force
    client_ip = request.remote_addr or "unknown"
    if not _check_login_rate(client_ip):
        logger.warning("Login rate limited for %s", client_ip)
        return jsonify({"error": "too many attempts — try again in 15 minutes"}), 429

    data = request.get_json(silent=True)
    if not data or not isinstance(data, dict):
        return jsonify({"error": "invalid request"}), 400

    provided = data.get("password", "")
    if _ui_password_is_hashed():
        # Password stored as scrypt hash in DB
        if not _verify_hashed_password(provided, password):
            _record_login_failure(client_ip)
            return jsonify({"error": "invalid password"}), 401
    else:
        # Plaintext env var comparison
        if not secrets.compare_digest(provided, password):
            _record_login_failure(client_ip)
            return jsonify({"error": "invalid password"}), 401

    _reset_login_attempts(client_ip)
    token = _create_session()
    resp = jsonify({"status": "ok"})
    is_https = request.scheme == "https" or request.headers.get("X-Forwarded-Proto") == "https"
    resp.set_cookie(
        "sentinel_session",
        token,
        httponly=True,
        samesite="Strict",
        secure=is_https,
        max_age=_SESSION_TTL,
        path="/",
    )
    return resp


@api_bp.route("/logout", methods=["POST"])
def logout():
    token = request.cookies.get("sentinel_session")
    if token:
        _delete_session(token)
    resp = jsonify({"status": "ok"})
    resp.delete_cookie("sentinel_session", path="/")
    return resp


@api_bp.route("/session", methods=["GET"])
def check_session():
    """Check if the current session is valid. Used by the SPA on load.

    Returns one of:
      {"authenticated": True}                     — valid session
      {"authenticated": False}                    — no/expired session, show login
      {"authenticated": False, "reason": "needs_setup"} — no password set, show setup form
      {"authenticated": False, "reason": "ui_disabled"} — no DB, UI cannot work
    """
    from .alert_db import db_available
    if not db_available():
        return jsonify({"authenticated": False, "reason": "ui_disabled"}), 200

    password = _ui_password()
    if not password:
        return jsonify({"authenticated": False, "reason": "needs_setup"}), 200

    token = request.cookies.get("sentinel_session")
    if token and _validate_session(token):
        return jsonify({"authenticated": True}), 200
    return jsonify({"authenticated": False}), 200


@api_bp.route("/setup", methods=["POST"])
def setup():
    """First-run password setup — no auth required.

    Only works when:
      1. The DB is available (UI needs it for sessions/data)
      2. No password is configured (neither env var nor DB)

    Once a password is set, this endpoint returns 403. It cannot be called
    again — the only way to reset is to clear the ui_config row in SQLite
    or set UI_PASSWORD in the env.
    """
    from .alert_db import db_available
    if not db_available():
        return jsonify({"error": "DB required for UI setup"}), 503

    if _ui_password():
        return jsonify({"error": "password already configured"}), 403

    data = request.get_json(silent=True)
    if not data or not isinstance(data, dict):
        return jsonify({"error": "invalid request"}), 400

    password = data.get("password", "")
    if not isinstance(password, str) or len(password) < 8:
        return jsonify({"error": "password must be at least 8 characters"}), 400

    if len(password) > 128:
        return jsonify({"error": "password must be 128 characters or fewer"}), 400

    # Hash and store
    hashed = _hash_password(password)
    if not set_ui_config("ui_password_hash", hashed):
        return jsonify({"error": "failed to save password"}), 500

    logger.info("UI password configured via first-run setup")

    # Auto-login — create a session so the user doesn't have to log in again
    token = _create_session()
    resp = jsonify({"status": "ok"})
    is_https = request.scheme == "https" or request.headers.get("X-Forwarded-Proto") == "https"
    resp.set_cookie(
        "sentinel_session",
        token,
        httponly=True,
        samesite="Strict",
        secure=is_https,
        max_age=_SESSION_TTL,
        path="/",
    )
    return resp


@api_bp.route("/change-password", methods=["POST"])
@require_ui_auth
def change_password():
    """Change the UI password. Requires current password for verification.

    Only works for browser-setup passwords (hashed in DB). If UI_PASSWORD
    is set via env var, the operator must change it in the env file.
    """
    # If password is from env var, refuse — operator controls it there
    env_pw = os.environ.get("UI_PASSWORD", "")
    if env_pw:
        return jsonify({"error": "password is set via UI_PASSWORD env var — change it there"}), 400

    data = request.get_json(silent=True)
    if not data or not isinstance(data, dict):
        return jsonify({"error": "invalid request"}), 400

    current = data.get("current_password", "")
    new_pw = data.get("new_password", "")

    if not isinstance(current, str) or not isinstance(new_pw, str):
        return jsonify({"error": "invalid request"}), 400

    # Verify current password
    stored = get_ui_config("ui_password_hash")
    if not stored or not _verify_hashed_password(current, stored):
        return jsonify({"error": "current password is incorrect"}), 403

    if len(new_pw) < 8:
        return jsonify({"error": "new password must be at least 8 characters"}), 400
    if len(new_pw) > 128:
        return jsonify({"error": "new password must be 128 characters or fewer"}), 400

    hashed = _hash_password(new_pw)
    if not set_ui_config("ui_password_hash", hashed):
        return jsonify({"error": "failed to save new password"}), 500

    logger.info("UI password changed via /api/change-password")
    return jsonify({"status": "ok"})


# ---------------------------------------------------------------------------
# Dashboard stats
# ---------------------------------------------------------------------------

@api_bp.route("/stats", methods=["GET"])
@require_ui_auth
def stats():
    db = get_db_stats()
    rpm = get_rpm_status()
    security = get_security_summary()
    dlq = get_dlq_count()
    open_incidents = get_all_open_incidents()

    # Count alerts in last 24h
    alerts_24h = 0
    try:
        conn = _get_conn()
        cutoff = time.time() - 86400
        row = conn.execute("SELECT COUNT(*) FROM alerts WHERE ts >= ?", (cutoff,)).fetchone()
        alerts_24h = row[0] if row else 0
    except Exception as exc:
        logger.warning("Failed to query 24h alert count: %s", type(exc).__name__)

    # Active platforms — configured + not explicitly disabled
    active_platforms = []
    for client in _CLIENTS:
        name = client.__name__.split(".")[-1].replace("_client", "")
        if _is_configured(client) and not _is_disabled(client):
            active_platforms.append(name)

    return jsonify({
        "open_incidents": len(open_incidents),
        "alerts_24h": alerts_24h,
        "active_platforms": active_platforms,
        "mode": _sentinel_mode(),
        "db": db,
        "ai": rpm,
        "security": security,
        "dlq_pending": dlq,
        "sse_clients": sse.subscriber_count(),
    })


# ---------------------------------------------------------------------------
# Incidents
# ---------------------------------------------------------------------------

@api_bp.route("/incidents", methods=["GET"])
@require_ui_auth
def list_incidents():
    page = max(1, request.args.get("page", 1, type=int))
    per_page = min(100, max(1, request.args.get("per_page", 20, type=int)))
    status_filter = request.args.get("status", "").lower()
    service_filter = request.args.get("service", "").lower()
    severity_filter = request.args.get("severity", "").lower()

    try:
        conn = _get_conn()
        conditions = []
        params: list[Any] = []

        if status_filter:
            conditions.append("status = ?")
            params.append(status_filter)
        if service_filter:
            conditions.append("LOWER(service) LIKE ?")
            params.append(f"%{service_filter}%")
        if severity_filter:
            conditions.append("severity = ?")
            params.append(severity_filter)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        total = conn.execute(
            f"SELECT COUNT(*) FROM incidents {where}", params
        ).fetchone()[0]

        offset = (page - 1) * per_page
        rows = conn.execute(
            f"""
            SELECT id, ts_start, ts_end, service, status, severity,
                   root_cause, summary, alert_count, storm_id
            FROM incidents
            {where}
            ORDER BY ts_start DESC
            LIMIT ? OFFSET ?
            """,
            params + [per_page, offset],
        ).fetchall()

        incidents = []
        for r in rows:
            inc = dict(r)
            inc["lifecycle"] = _derive_lifecycle(inc, conn)
            incidents.append(inc)

        return jsonify({
            "incidents": incidents,
            "total": total,
            "page": page,
            "per_page": per_page,
        })
    except Exception as exc:
        logger.warning("List incidents failed: %s", type(exc).__name__)
        return jsonify({"incidents": [], "total": 0, "page": 1, "per_page": per_page})


@api_bp.route("/incidents/<int:incident_id>", methods=["GET"])
@require_ui_auth
def incident_detail(incident_id: int):
    inc = get_incident(incident_id)
    if not inc:
        return jsonify({"error": "not found"}), 404

    try:
        conn = _get_conn()

        # Linked alerts
        alerts = [_parse_json_fields(dict(r)) for r in conn.execute(
            """
            SELECT id, ts, source, service, status, severity, message,
                   details, insight, actions, notified, is_trigger, event_id
            FROM alerts WHERE incident_id = ?
            ORDER BY ts ASC
            """,
            (incident_id,),
        ).fetchall()]

        # Operator notes
        notes = []
        try:
            notes = [dict(r) for r in conn.execute(
                "SELECT id, ts, content FROM incident_notes WHERE incident_id = ? ORDER BY ts ASC",
                (incident_id,),
            ).fetchall()]
        except Exception:
            pass  # table may not exist yet

        # Topology context for the service
        from .topology import get_topology
        topo = get_topology(inc["service"])

        # Similar past incidents (same service, resolved, most recent 5)
        similar = [dict(r) for r in conn.execute(
            """
            SELECT id, ts_start, ts_end, severity, summary, alert_count
            FROM incidents
            WHERE service = ? AND status = 'resolved' AND id != ?
            ORDER BY ts_start DESC
            LIMIT 5
            """,
            (inc["service"], incident_id),
        ).fetchall()]

        inc["lifecycle"] = _derive_lifecycle(inc, conn)

        return jsonify({
            "incident": inc,
            "alerts": alerts,
            "notes": notes,
            "topology": topo,
            "similar": similar,
        })
    except Exception as exc:
        logger.warning("Incident detail failed: %s", type(exc).__name__)
        return jsonify({"error": "internal error"}), 500


@api_bp.route("/incidents/<int:incident_id>/resolve", methods=["POST"])
@require_ui_auth
def resolve_incident_api(incident_id: int):
    inc = get_incident(incident_id)
    if not inc:
        return jsonify({"error": "not found"}), 404
    if inc["status"] == "resolved":
        return jsonify({"error": "already resolved"}), 400

    data = request.get_json(silent=True) or {}
    summary = data.get("summary", "Manually resolved via UI")
    resolve_incident(incident_id, summary=summary)
    sse.publish("incident", {"id": incident_id, "action": "resolved"})
    return jsonify({"status": "ok"})


@api_bp.route("/incidents/<int:incident_id>/notes", methods=["POST"])
@require_ui_auth
def add_note(incident_id: int):
    inc = get_incident(incident_id)
    if not inc:
        return jsonify({"error": "not found"}), 404

    data = request.get_json(silent=True) or {}
    content = str(data.get("content", "")).strip()
    if not content:
        return jsonify({"error": "content required"}), 400
    if len(content) > 2000:
        content = content[:2000]

    try:
        conn = _get_conn()
        cursor = conn.execute(
            "INSERT INTO incident_notes (incident_id, ts, content) VALUES (?, ?, ?)",
            (incident_id, time.time(), content),
        )
        conn.commit()
        note_id = cursor.lastrowid
        return jsonify({"status": "ok", "note_id": note_id})
    except Exception as exc:
        logger.warning("Add note failed: %s", type(exc).__name__)
        return jsonify({"error": "failed to save note"}), 500


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------

@api_bp.route("/alerts", methods=["GET"])
@require_ui_auth
def list_alerts():
    page = max(1, request.args.get("page", 1, type=int))
    per_page = min(100, max(1, request.args.get("per_page", 50, type=int)))
    service_filter = request.args.get("service", "").lower()

    try:
        conn = _get_conn()
        conditions = []
        params: list[Any] = []
        if service_filter:
            conditions.append("LOWER(service) LIKE ?")
            params.append(f"%{service_filter}%")
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        total = conn.execute(f"SELECT COUNT(*) FROM alerts {where}", params).fetchone()[0]
        offset = (page - 1) * per_page
        rows = conn.execute(
            f"""
            SELECT id, ts, source, service, status, severity, message,
                   insight, notified, incident_id, is_trigger, event_id
            FROM alerts {where}
            ORDER BY ts DESC LIMIT ? OFFSET ?
            """,
            params + [per_page, offset],
        ).fetchall()

        return jsonify({
            "alerts": [_parse_json_fields(dict(r)) for r in rows],
            "total": total,
            "page": page,
            "per_page": per_page,
        })
    except Exception as exc:
        logger.warning("List alerts failed: %s", type(exc).__name__)
        return jsonify({"alerts": [], "total": 0, "page": page, "per_page": per_page})


@api_bp.route("/alerts/<int:alert_id>", methods=["GET"])
@require_ui_auth
def alert_detail(alert_id: int):
    try:
        conn = _get_conn()
        row = conn.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404
        return jsonify({"alert": _parse_json_fields(dict(row))})
    except Exception as exc:
        logger.warning("Alert detail failed: %s", type(exc).__name__)
        return jsonify({"error": "internal error"}), 500


@api_bp.route("/alerts/<int:alert_id>", methods=["DELETE"])
@require_ui_auth
def delete_alert(alert_id: int):
    """Delete a single alert by ID."""
    try:
        conn = _get_conn()
        row = conn.execute("SELECT id FROM alerts WHERE id = ?", (alert_id,)).fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404
        conn.execute("DELETE FROM alerts WHERE id = ?", (alert_id,))
        conn.commit()
        logger.info("Alert %d deleted via UI", alert_id)
        return jsonify({"status": "ok", "deleted": 1})
    except Exception as exc:
        logger.warning("Delete alert failed: %s", type(exc).__name__)
        return jsonify({"error": "internal error"}), 500


@api_bp.route("/alerts/delete", methods=["POST"])
@require_ui_auth
def delete_alerts_batch():
    """Batch delete alerts — by filter or all.

    Body:
      {"all": true}                          — delete every alert
      {"service": "nginx"}                   — delete alerts matching service (LIKE)
      {"severity": "info"}                   — delete alerts matching severity
      {"service": "nginx", "severity": "info"} — both filters ANDed
    """
    data = request.get_json(silent=True)
    if not data or not isinstance(data, dict):
        return jsonify({"error": "invalid request"}), 400

    try:
        conn = _get_conn()

        if data.get("all") is True:
            count = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
            conn.execute("DELETE FROM alerts")
            conn.commit()
            logger.info("All %d alerts deleted via UI", count)
            return jsonify({"status": "ok", "deleted": count})

        # Filtered delete
        conditions = []
        params: list[Any] = []
        service = str(data.get("service", "")).strip().lower()
        severity = str(data.get("severity", "")).strip().lower()

        if service:
            conditions.append("LOWER(service) LIKE ?")
            params.append(f"%{service}%")
        if severity:
            conditions.append("severity = ?")
            params.append(severity)

        if not conditions:
            return jsonify({"error": "specify filters or {\"all\": true}"}), 400

        where = f"WHERE {' AND '.join(conditions)}"
        count = conn.execute(f"SELECT COUNT(*) FROM alerts {where}", params).fetchone()[0]
        conn.execute(f"DELETE FROM alerts {where}", params)
        conn.commit()
        logger.info("Deleted %d alerts via UI (filters: %s)", count, data)
        return jsonify({"status": "ok", "deleted": count})
    except Exception as exc:
        logger.warning("Batch delete alerts failed: %s", type(exc).__name__)
        return jsonify({"error": "internal error"}), 500


# ---------------------------------------------------------------------------
# Alert feedback — operator ratings on AI insights
# ---------------------------------------------------------------------------

_VALID_RATINGS = frozenset({"up", "down", "meh"})


@api_bp.route("/alerts/<int:alert_id>/feedback", methods=["POST"])
@require_ui_auth
def submit_feedback(alert_id: int):
    """Store or update operator feedback for an alert.

    Body: {"rating": "up"|"down"|"meh", "comment": "..."}  (comment optional)
    One feedback record per alert — subsequent POSTs overwrite the previous rating.
    """
    data = request.get_json(silent=True)
    if not data or not isinstance(data, dict):
        return jsonify({"error": "invalid request"}), 400

    rating = str(data.get("rating", "")).strip().lower()
    if rating not in _VALID_RATINGS:
        return jsonify({"error": f"rating must be one of: {', '.join(sorted(_VALID_RATINGS))}"}), 400

    comment_raw = data.get("comment")
    comment = str(comment_raw).strip()[:500] if comment_raw is not None else None

    # Verify alert exists
    try:
        conn = _get_conn()
        if not conn.execute("SELECT 1 FROM alerts WHERE id = ?", (alert_id,)).fetchone():
            return jsonify({"error": "not found"}), 404
    except Exception as exc:
        logger.warning("Feedback alert lookup failed: %s", type(exc).__name__)
        return jsonify({"error": "internal error"}), 500

    if add_feedback(alert_id, rating, comment):
        return jsonify({"status": "ok", "alert_id": alert_id, "rating": rating})
    return jsonify({"error": "failed to save feedback"}), 500


@api_bp.route("/alerts/<int:alert_id>/feedback", methods=["GET"])
@require_ui_auth
def get_feedback(alert_id: int):
    """Return the operator feedback for an alert, or null if none submitted."""
    result = get_feedback_for_alert(alert_id)
    return jsonify({"feedback": result})


@api_bp.route("/feedback/export", methods=["GET"])
@require_ui_auth
def feedback_export():
    """Export all feedback records joined with alert and AI data.

    Returns a JSON array suitable for RAG ingestion or model fine-tuning.
    Each record contains alert metadata, AI insight, and operator rating+comment.
    """
    records = export_feedback()
    return jsonify({"feedback": records, "count": len(records)})


# ---------------------------------------------------------------------------
# Topology
# ---------------------------------------------------------------------------

@api_bp.route("/topology", methods=["GET"])
@require_ui_auth
def topology():
    topo = _load_topology()
    if not topo:
        return jsonify({"services": {}, "shared_resources": {}})

    # Enrich with live incident status
    open_incs = get_all_open_incidents()
    incident_map = {}
    for inc in open_incs:
        svc = inc["service"].lower()
        if svc not in incident_map or inc["severity"] == "critical":
            incident_map[svc] = inc

    services = topo.get("services", {})
    enriched = {}
    for name, data in services.items():
        svc = dict(data) if isinstance(data, dict) else {}
        inc = incident_map.get(name.lower())
        svc["has_incident"] = inc is not None
        svc["incident_severity"] = inc["severity"] if inc else None
        svc["incident_id"] = inc["id"] if inc else None
        enriched[name] = svc

    return jsonify({
        "services": enriched,
        "shared_resources": topo.get("shared_resources", {}),
    })


# ---------------------------------------------------------------------------
# Pulse
# ---------------------------------------------------------------------------

@api_bp.route("/pulse/<service>", methods=["GET"])
@require_ui_auth
def pulse(service: str):
    result = get_pulse(service)
    if result is None:
        return jsonify({"pulse": None})
    return jsonify({"pulse": result})


# ---------------------------------------------------------------------------
# Settings (read-only, no secrets)
# ---------------------------------------------------------------------------

@api_bp.route("/settings", methods=["GET"])
@require_ui_auth
def settings():
    platforms = {}
    for client in _CLIENTS:
        name = client.__name__.split(".")[-1].replace("_client", "")
        configured = _is_configured(client)
        disabled = _is_disabled(client)
        platforms[name] = {
            "configured": configured,
            "disabled": disabled,
            "status": "active" if configured and not disabled else "disabled" if disabled else "unconfigured",
        }

    return jsonify({
        "mode": _sentinel_mode(),
        "min_severity": os.environ.get("MIN_SEVERITY", "info"),
        "dedup_ttl": _env_int("DEDUP_TTL_SECONDS", 60),
        "cooldown": _env_int("COOLDOWN_SECONDS", 0),
        "storm_window": _env_int("STORM_WINDOW", 0),
        "storm_threshold": _env_int("STORM_THRESHOLD", 3),
        "retention_days": _env_int("RETENTION_DAYS", 90),
        "ai_provider": os.environ.get("AI_PROVIDER", "gemini").lower(),
        "ai_concurrency": _env_int("AI_CONCURRENCY", 4),
        "platforms": platforms,
    })


# ---------------------------------------------------------------------------
# SSE
# ---------------------------------------------------------------------------

@api_bp.route("/events", methods=["GET"])
@require_ui_auth
def events():
    sub = sse.subscribe()
    if sub is None:
        return jsonify({"error": "too many SSE clients"}), 429

    return Response(
        stream_with_context(sse.stream(sub)),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering
        },
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _derive_lifecycle(inc: dict, conn) -> str:
    """
    Derive incident lifecycle state from data:
      emerging    — open, alert_count == 1, age < 5 min
      active      — open, alert_count > 1 or age > 5 min
      stabilizing — open, no new alerts in last 10 min
      resolved    — status == 'resolved'
    """
    if inc.get("status") == "resolved":
        return "resolved"

    now = time.time()
    age = now - inc.get("ts_start", now)

    # Check most recent alert timestamp for this incident
    try:
        row = conn.execute(
            "SELECT MAX(ts) FROM alerts WHERE incident_id = ?",
            (inc.get("id"),),
        ).fetchone()
        last_alert_ts = row[0] if row and row[0] else inc.get("ts_start", now)
    except Exception:
        last_alert_ts = inc.get("ts_start", now)

    time_since_last = now - last_alert_ts

    if time_since_last > 600:  # 10 minutes quiet
        return "stabilizing"
    if inc.get("alert_count", 1) == 1 and age < 300:  # 5 minutes
        return "emerging"
    return "active"
