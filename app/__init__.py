import logging
import os

from flask import Flask, jsonify
from werkzeug.exceptions import HTTPException
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


def create_app() -> Flask:
    _configure_logging()
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024  # 1 MB
    app.register_blueprint(webhook_bp)

    @app.errorhandler(404)
    def not_found(_: HTTPException) -> tuple:
        return jsonify({"error": "not found"}), 404

    @app.errorhandler(405)
    def method_not_allowed(_: HTTPException) -> tuple:
        return jsonify({"error": "method not allowed"}), 405

    @app.errorhandler(413)
    def payload_too_large(_: HTTPException) -> tuple:
        return jsonify({"error": "payload too large", "limit": "1MB"}), 413

    @app.errorhandler(Exception)
    def unhandled_exception(exc: Exception) -> tuple:
        logger.exception("Unhandled exception: %s", exc)
        return jsonify({"error": "internal server error"}), 500

    return app
