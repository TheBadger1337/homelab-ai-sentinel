from flask import Flask
from .webhook import webhook_bp


def create_app():
    app = Flask(__name__)
    app.register_blueprint(webhook_bp)
    return app
