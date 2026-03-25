"""
Tests for Flask app-level error handlers.

All API errors should return JSON, never HTML.
"""

import pytest
from app import create_app


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_404_returns_json(client):
    resp = client.get("/nonexistent")
    assert resp.status_code == 404
    assert resp.is_json
    assert "error" in resp.get_json()


def test_405_returns_json(client):
    # /webhook only accepts POST
    resp = client.get("/webhook")
    assert resp.status_code == 405
    assert resp.is_json
    assert "error" in resp.get_json()


def test_413_returns_json(client):
    large_payload = b'{"x": "' + b"a" * (1024 * 1024 + 100) + b'"}'
    resp = client.post(
        "/webhook",
        data=large_payload,
        content_type="application/json",
    )
    assert resp.status_code == 413
    assert resp.is_json
    data = resp.get_json()
    assert "error" in data
    assert "limit" in data
