import logging
import os
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException
from .alert_db import init_db
from .api import api_bp
from .mcp import mcp_bp
from .config_validator import validate_config
from .housekeeper import start_housekeeper
from .watchdog import start_watchdog
from .webhook import webhook_bp

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    """
    Configure logging level and format for Sentinel.

    In normal mode: INFO — shows alert received, AI result, dispatch errors.
    In debug mode:  DEBUG — also shows full parsed alert fields, AI response
                    text, per-platform dispatch results, and disabled skips.

    Enable with: SENTINEL_DEBUG=true in .secrets.env
    Never enable in production — debug output includes full alert payloads
    which may contain service URLs, hostnames, and metric values.
    """
    debug = os.environ.get("SENTINEL_DEBUG", "").lower() == "true"
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Ensure all app.* loggers inherit the configured level
    logging.getLogger("app").setLevel(level)
    if debug:
        logging.getLogger("app").debug("SENTINEL_DEBUG enabled — verbose logging active")


def _log_db_dependent_warnings() -> None:
    """Warn about each DB-dependent feature that the operator configured but
    won't work because the DB is off. No silent degradation — every feature
    that was configured but is silently inactive gets called out by name."""
    # (env_var, human name, what happens without DB)
    checks = [
        ("WEBHOOK_RATE_LIMIT", "Rate limiting", "all requests allowed through"),
        ("COOLDOWN_SECONDS", "Per-service cooldown", "no cooldown between alerts"),
        ("ESCALATION_THRESHOLD", "Severity escalation", "warnings never auto-escalate"),
        ("UI_PASSWORD", "Web UI", "requires DB for sessions, incidents, and alerts"),
    ]
    for var, name, impact in checks:
        val = os.environ.get(var, "")
        if var == "UI_PASSWORD":
            # Any non-empty value means it was configured
            if val:
                logger.warning("  %s configured but DB is off — %s", name, impact)
        else:
            # Numeric features: only warn if explicitly set to > 0
            try:
                if int(val) > 0:
                    logger.warning("  %s=%s configured but DB is off — %s", name, val, impact)
            except (ValueError, TypeError):
                pass


def create_app() -> Flask:
    _configure_logging()

    # Validate all configuration up front — catches typos, partial configs,
    # bad URLs, and invalid values immediately instead of at dispatch time.
    validate_config()

    # Static folder for the React SPA (built into /app/static in Docker)
    static_dir = Path(__file__).resolve().parent.parent / "static"
    app = Flask(__name__, static_folder=str(static_dir), static_url_path="/static")
    app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024  # 1 MB
    app.register_blueprint(webhook_bp)
    app.register_blueprint(mcp_bp)
    init_db()

    start_watchdog()

    # -----------------------------------------------------------------
    # Feature gate: DB
    # The DB is a single opt-in that enables 9 dependent features.
    # When the DB is off (DB_DISABLED=true or init failure), ALL of
    # them are automatically disabled. No need to turn off 9 things.
    # -----------------------------------------------------------------
    from .alert_db import db_available
    _db_ok = db_available()

    if _db_ok:
        logger.info(
            "DB enabled — the following features are active: "
            "alert logging, dedup L2 (cross-worker), rate limiting, "
            "cooldown, escalation, dead letter queue, incidents, correlation"
        )
    else:
        logger.info(
            "DB is off — running in stateless mode (parse → dispatch only). "
            "Disabled: alert logging, dedup L2, rate limiting, cooldown, "
            "escalation, DLQ retry, incidents, correlation, housekeeper. "
            "Set DB_PATH to a writable path to enable, or DB_DISABLED=true "
            "to silence this message."
        )
        _log_db_dependent_warnings()

    # -----------------------------------------------------------------
    # Feature gate: Web UI (requires DB)
    # The Web UI is a separate opt-in on top of the DB. It adds:
    # session auth, /api/* endpoints, SSE real-time stream, SPA frontend.
    #
    # The API blueprint is registered whenever the DB is available —
    # even without UI_PASSWORD — so the /api/setup endpoint is reachable
    # for first-run password configuration via the browser.
    # All other /api/* endpoints require auth (require_ui_auth decorator).
    # -----------------------------------------------------------------
    has_password = bool(os.environ.get("UI_PASSWORD"))
    ui_enabled = _db_ok  # API routes available when DB is on
    if _db_ok:
        app.register_blueprint(api_bp)
        if has_password:
            logger.info("Web UI enabled — /api/* routes registered")
        else:
            logger.info(
                "Web UI available — visit the UI to set a password, "
                "or set UI_PASSWORD in .secrets.env"
            )
    elif has_password:
        ui_enabled = False
        logger.warning(
            "Web UI requires a working DB — UI_PASSWORD is set but DB is "
            "unavailable. The UI stores sessions, incidents, and alert "
            "history in SQLite. Enable DB first, then set UI_PASSWORD."
        )
    else:
        logger.info("Web UI disabled — requires DB (set DB_PATH)")

    # Storm recovery — process any orphaned storm buffer entries from a previous crash
    if _db_ok:
        from .storm import recover_orphaned_entries
        recover_orphaned_entries()

    # Housekeeper — background thread for DB pruning, DLQ retry, WAL checkpoint
    if _db_ok:
        start_housekeeper()
    else:
        logger.info("Housekeeper not started (requires DB)")

    # -----------------------------------------------------------------
    # Security headers — applied to every response
    # -----------------------------------------------------------------
    @app.after_request
    def _security_headers(response):
        # Prevent clickjacking — Sentinel should never be framed
        response.headers["X-Frame-Options"] = "DENY"
        # Prevent MIME sniffing (e.g., JSON treated as HTML)
        response.headers["X-Content-Type-Options"] = "nosniff"
        # Disable referrer leakage — internal URLs stay internal
        response.headers["Referrer-Policy"] = "no-referrer"
        # Permissions-Policy — disable browser features Sentinel doesn't use
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=()"
        )
        # CSP — only serve our own scripts/styles/fonts, block everything else.
        # 'unsafe-inline' for style-src is required by Tailwind's runtime and
        # React Flow's inline styles. No inline scripts — React is bundled.
        if ui_enabled:
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self'; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; "
                "font-src 'self'; "
                "connect-src 'self'; "
                "frame-ancestors 'none'; "
                "base-uri 'self'; "
                "form-action 'self'"
            )
        return response

    # SPA-aware 404 handler — only serves the frontend when UI is enabled.
    # When UI is disabled, all 404s return JSON regardless of Accept header.
    @app.errorhandler(404)
    def not_found_or_spa(_: HTTPException):
        if not ui_enabled:
            return jsonify({"error": "not found"}), 404
        # Serve SPA only when a browser explicitly requests HTML.
        # API clients, test clients, and curl get JSON 404 by default.
        accepts_html = request.accept_mimetypes.best_match(
            ["text/html", "application/json"]
        ) == "text/html"
        if not accepts_html:
            return jsonify({"error": "not found"}), 404
        index = static_dir / "index.html"
        rel = request.path.lstrip("/")
        if rel:
            try:
                target = (static_dir / rel).resolve()
                target.relative_to(static_dir.resolve())  # raises ValueError if outside static_dir
                if target.is_file():
                    return send_from_directory(str(static_dir), rel)
            except ValueError:
                pass  # path traversal attempt — fall through to SPA fallback
        if index.is_file():
            return send_from_directory(str(static_dir), "index.html")
        return jsonify({"error": "not found"}), 404

    @app.errorhandler(405)
    def method_not_allowed(_: HTTPException) -> tuple:
        return jsonify({"error": "method not allowed"}), 405

    @app.errorhandler(413)
    def payload_too_large(_: HTTPException) -> tuple:
        return jsonify({"error": "payload too large", "limit": "1MB"}), 413

    @app.errorhandler(Exception)
    def unhandled_exception(exc: Exception) -> tuple:
        # Log the exception type only — not str(exc), which for requests.HTTPError
        # includes the full request URL (may contain API tokens in the path).
        logger.exception("Unhandled exception: %s", type(exc).__name__)
        return jsonify({"error": "internal server error"}), 500

    return app
