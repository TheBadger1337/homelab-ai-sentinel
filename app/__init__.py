from flask import Flask
from .webhook import webhook_bp


def create_app():
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024  # 1 MB
    app.register_blueprint(webhook_bp)
    return app
