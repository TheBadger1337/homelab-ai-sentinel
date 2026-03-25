import logging

from flask import Flask, jsonify
from werkzeug.exceptions import HTTPException
from .webhook import webhook_bp

logger = logging.getLogger(__name__)


def create_app() -> Flask:
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
