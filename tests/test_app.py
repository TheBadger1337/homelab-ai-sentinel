"""
Tests for Flask app-level error handlers and webhook security behaviors.

All API errors must return JSON (never HTML).
All error responses must not contain secrets, tokens, or internal file paths.
"""

import pytest
from unittest.mock import patch
from app import create_app


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# HTTP error handlers — all must return JSON
# ---------------------------------------------------------------------------

def test_404_returns_json(client):
    resp = client.get("/nonexistent")
    assert resp.status_code == 404
    assert resp.is_json
    assert "error" in resp.get_json()


def test_405_returns_json(client):
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


def test_unhandled_exception_returns_json(client):
    resp = client.post(
        "/webhook",
        data=b"not json at all",
        content_type="application/json",
    )
    assert resp.is_json
    assert resp.status_code in (400, 500)


# ---------------------------------------------------------------------------
# Content-Type enforcement
# ---------------------------------------------------------------------------

def test_webhook_rejects_non_json_content_type(client):
    resp = client.post("/webhook", data="hello", content_type="text/plain")
    assert resp.status_code == 415
    assert resp.is_json
    assert "error" in resp.get_json()


def test_webhook_rejects_missing_content_type(client):
    resp = client.post("/webhook", data='{"service":"x"}')
    assert resp.status_code == 415
    assert resp.is_json


# ---------------------------------------------------------------------------
# Request body validation
# ---------------------------------------------------------------------------

def test_webhook_rejects_json_array(client):
    resp = client.post(
        "/webhook",
        json=["not", "a", "dict"],
    )
    assert resp.status_code == 400
    assert resp.is_json
    assert "error" in resp.get_json()


def test_webhook_rejects_empty_body(client):
    resp = client.post(
        "/webhook",
        data=b"",
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert resp.is_json


# ---------------------------------------------------------------------------
# WEBHOOK_SECRET authentication
# ---------------------------------------------------------------------------

def test_webhook_rejects_request_when_secret_set_and_missing(client, monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", "correct-secret")
    resp = client.post("/webhook", json={"service": "x", "status": "down", "message": "y"})
    assert resp.status_code == 401
    assert resp.is_json
    assert resp.get_json()["error"] == "unauthorized"


def test_webhook_rejects_wrong_secret(client, monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", "correct-secret")
    resp = client.post(
        "/webhook",
        json={"service": "x", "status": "down", "message": "y"},
        headers={"X-Webhook-Token": "wrong-secret"},
    )
    assert resp.status_code == 401


def test_webhook_accepts_correct_secret(client, monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", "correct-secret")
    with patch("app.webhook.get_ai_insight") as mock_ai, \
         patch("app.notify.dispatch") as mock_dispatch:
        mock_ai.return_value = {"insight": "ok", "suggested_actions": []}
        mock_dispatch.return_value = []
        resp = client.post(
            "/webhook",
            json={"service": "x", "status": "down", "message": "y"},
            headers={"X-Webhook-Token": "correct-secret"},
        )
    assert resp.status_code == 200


def test_webhook_allows_all_when_no_secret_set(client, monkeypatch):
    monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
    with patch("app.webhook.get_ai_insight") as mock_ai, \
         patch("app.notify.dispatch") as mock_dispatch:
        mock_ai.return_value = {"insight": "ok", "suggested_actions": []}
        mock_dispatch.return_value = []
        resp = client.post(
            "/webhook",
            json={"service": "x", "status": "down", "message": "y"},
        )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Error responses must not leak internal details or secrets
# ---------------------------------------------------------------------------

def test_error_response_has_no_detail_field(client):
    """Parse errors must not expose internal exception text."""
    resp = client.post("/webhook", json={})
    assert "detail" not in (resp.get_json() or {})


def test_404_response_contains_only_error_key(client):
    """Error responses should be minimal — no stack traces or file paths."""
    data = client.get("/doesnotexist").get_json()
    assert set(data.keys()) == {"error"}


# ---------------------------------------------------------------------------
# Alert deduplication (token budget protection)
# ---------------------------------------------------------------------------

def test_duplicate_alert_is_suppressed(client, monkeypatch):
    """Second identical alert within TTL window returns deduplicated, no AI call."""
    monkeypatch.setenv("DEDUP_TTL_SECONDS", "60")
    monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
    payload = {"service": "nginx", "status": "down", "message": "Connection refused"}

    with patch("app.webhook.get_ai_insight") as mock_ai, \
         patch("app.notify.dispatch") as mock_dispatch:
        mock_ai.return_value = {"insight": "ok", "suggested_actions": []}
        mock_dispatch.return_value = []

        # First request — processed normally
        resp1 = client.post("/webhook", json=payload)
        assert resp1.status_code == 200
        assert resp1.get_json()["status"] == "processed"
        assert mock_ai.call_count == 1

        # Second identical request — deduplicated, AI not called again
        resp2 = client.post("/webhook", json=payload)
        assert resp2.status_code == 200
        assert resp2.get_json()["status"] == "deduplicated"
        assert mock_ai.call_count == 1  # still 1 — not called again


def test_different_alert_not_suppressed(client, monkeypatch):
    """Different service or status always processes normally."""
    monkeypatch.setenv("DEDUP_TTL_SECONDS", "60")
    monkeypatch.delenv("WEBHOOK_SECRET", raising=False)

    with patch("app.webhook.get_ai_insight") as mock_ai, \
         patch("app.notify.dispatch") as mock_dispatch:
        mock_ai.return_value = {"insight": "ok", "suggested_actions": []}
        mock_dispatch.return_value = []

        client.post("/webhook", json={"service": "nginx", "status": "down", "message": "x"})
        client.post("/webhook", json={"service": "redis", "status": "down", "message": "x"})

        assert mock_ai.call_count == 2


def test_dedup_disabled_when_ttl_zero(client, monkeypatch):
    """DEDUP_TTL_SECONDS=0 disables deduplication entirely."""
    monkeypatch.setenv("DEDUP_TTL_SECONDS", "0")
    monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
    payload = {"service": "nginx", "status": "down", "message": "Connection refused"}

    with patch("app.webhook.get_ai_insight") as mock_ai, \
         patch("app.notify.dispatch") as mock_dispatch:
        mock_ai.return_value = {"insight": "ok", "suggested_actions": []}
        mock_dispatch.return_value = []

        client.post("/webhook", json=payload)
        client.post("/webhook", json=payload)

        assert mock_ai.call_count == 2  # both processed, no suppression


# ---------------------------------------------------------------------------
# Webhook rate limiter
# ---------------------------------------------------------------------------

def test_rate_limit_blocks_when_exceeded(client, monkeypatch):
    """WEBHOOK_RATE_LIMIT=1 allows the first request and 429s the second."""
    monkeypatch.setenv("WEBHOOK_RATE_LIMIT", "1")
    monkeypatch.setenv("WEBHOOK_RATE_WINDOW", "60")
    monkeypatch.delenv("WEBHOOK_SECRET", raising=False)

    # Simulate the DB-backed rate checker: first call passes, second is blocked.
    results = iter([False, True])

    with patch("app.webhook.check_and_record_rate", side_effect=lambda l, w: next(results)), \
         patch("app.webhook.get_ai_insight") as mock_ai, \
         patch("app.notify.dispatch") as mock_dispatch:
        mock_ai.return_value = {"insight": "ok", "suggested_actions": []}
        mock_dispatch.return_value = []

        resp1 = client.post("/webhook", json={"service": "nginx", "status": "down", "message": "x"})
        resp2 = client.post("/webhook", json={"service": "redis", "status": "down", "message": "y"})

    assert resp1.status_code == 200
    assert resp2.status_code == 429
    assert resp2.get_json()["error"] == "too many requests"


def test_rate_limit_disabled_when_zero(client, monkeypatch):
    """WEBHOOK_RATE_LIMIT=0 (default) disables rate limiting."""
    monkeypatch.setenv("WEBHOOK_RATE_LIMIT", "0")
    monkeypatch.delenv("WEBHOOK_SECRET", raising=False)

    with patch("app.webhook.get_ai_insight") as mock_ai, \
         patch("app.notify.dispatch") as mock_dispatch:
        mock_ai.return_value = {"insight": "ok", "suggested_actions": []}
        mock_dispatch.return_value = []

        for _ in range(5):
            resp = client.post("/webhook", json={"service": "x", "status": "down", "message": "y"})
            assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------

def test_health_endpoint_returns_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.is_json
    data = resp.get_json()
    assert data["status"] == "ok"
    assert "db" in data
    assert "ai" in data
    assert "security" in data
    assert "workers" in data


def test_health_endpoint_db_stats_present(client):
    data = client.get("/health").get_json()
    assert "total_alerts" in data["db"]
    assert "notified_count" in data["db"]
    assert "last_alert_ts" in data["db"]


def test_health_endpoint_ai_rpm_present(client):
    data = client.get("/health").get_json()
    assert "limit" in data["ai"]
    assert "used" in data["ai"]


def test_health_requires_auth_when_secret_set(client, monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", "secret123")
    resp = client.get("/health")
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "unauthorized"


def test_health_rejects_wrong_token(client, monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", "secret123")
    resp = client.get("/health", headers={"X-Webhook-Token": "wrong"})
    assert resp.status_code == 401


def test_health_accepts_correct_token(client, monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", "secret123")
    resp = client.get("/health", headers={"X-Webhook-Token": "secret123"})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"


def test_health_open_when_no_secret(client, monkeypatch):
    monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
    resp = client.get("/health")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# SENTINEL_MODE
# ---------------------------------------------------------------------------

def _process_alert(client, monkeypatch, mode, mock_ai=None, mock_dispatch=None):
    """Helper: POST a generic alert in a given mode, return the response."""
    import app.webhook as wh
    monkeypatch.setenv("SENTINEL_MODE", mode)
    monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
    # Clear dedup cache so each call is treated as a fresh alert
    with wh._dedup_lock:
        wh._dedup_cache.clear()
    payload = {"service": "nginx", "status": "down", "message": "Connection refused"}
    with patch("app.webhook.get_ai_insight") as mai, \
         patch("app.notify.dispatch") as md:
        mai.return_value = mock_ai or {"insight": "AI ok", "suggested_actions": ["check logs"]}
        md.return_value = mock_dispatch or []
        resp = client.post("/webhook", json=payload)
        return resp, mai, md


def test_minimal_mode_skips_ai_call(client, monkeypatch):
    resp, mock_ai, _ = _process_alert(client, monkeypatch, "minimal")
    assert resp.status_code == 200
    mock_ai.assert_not_called()


def test_minimal_mode_response_has_no_ai_fields(client, monkeypatch):
    resp, _, _ = _process_alert(client, monkeypatch, "minimal")
    data = resp.get_json()
    assert data["mode"] == "minimal"
    assert "ai_insight" not in data
    assert "suggested_actions" not in data


def test_reactive_mode_calls_ai(client, monkeypatch):
    resp, mock_ai, _ = _process_alert(client, monkeypatch, "reactive")
    assert resp.status_code == 200
    mock_ai.assert_called_once()


def test_reactive_mode_passes_empty_history(client, monkeypatch):
    """reactive mode must not inject alert history into the AI call."""
    import app.webhook as wh
    monkeypatch.setenv("SENTINEL_MODE", "reactive")
    monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
    with wh._dedup_lock:
        wh._dedup_cache.clear()
    with patch("app.webhook.get_ai_insight") as mock_ai, \
         patch("app.notify.dispatch", return_value=[]):
        mock_ai.return_value = {"insight": "ok", "suggested_actions": []}
        client.post("/webhook", json={"service": "nginx", "status": "down", "message": "x"})
    _, kwargs = mock_ai.call_args
    assert kwargs.get("history") == []


def test_predictive_mode_calls_ai_with_history(client, monkeypatch):
    """predictive mode must query history and pass it to the AI call."""
    import app.webhook as wh
    monkeypatch.setenv("SENTINEL_MODE", "predictive")
    monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
    with wh._dedup_lock:
        wh._dedup_cache.clear()
    fake_history = [{"ts": 1, "status": "down", "severity": "critical", "message": "prev"}]
    with patch("app.webhook.get_recent_alerts", return_value=fake_history) as mock_hist, \
         patch("app.webhook.get_ai_insight") as mock_ai, \
         patch("app.notify.dispatch", return_value=[]):
        mock_ai.return_value = {"insight": "ok", "suggested_actions": []}
        client.post("/webhook", json={"service": "nginx", "status": "down", "message": "x"})
    mock_hist.assert_called_once()
    _, kwargs = mock_ai.call_args
    assert kwargs.get("history") == fake_history


def test_mode_included_in_response(client, monkeypatch):
    for mode in ("minimal", "reactive", "predictive"):
        resp, _, _ = _process_alert(client, monkeypatch, mode)
        assert resp.get_json()["mode"] == mode


def test_unknown_mode_falls_back_to_predictive(client, monkeypatch):
    resp, mock_ai, _ = _process_alert(client, monkeypatch, "turbo-ultra-mode")
    assert resp.status_code == 200
    # Falls back to predictive — AI is called
    mock_ai.assert_called_once()
    assert resp.get_json()["mode"] == "predictive"


# ---------------------------------------------------------------------------
# Dedup cache pruning
# ---------------------------------------------------------------------------

def test_dedup_cache_prunes_expired_entries(monkeypatch):
    """Stale entries older than TTL are removed from the cache on each call."""
    import time
    import app.webhook as wh
    from app.alert_parser import NormalizedAlert

    monkeypatch.setenv("DEDUP_TTL_SECONDS", "60")
    monkeypatch.delenv("WEBHOOK_SECRET", raising=False)

    alert = NormalizedAlert(
        source="generic", service_name="prunetest", status="down",
        severity="critical", message="x", details={},
    )

    # Seed the cache with a stale entry (120s old — well beyond the 60s TTL)
    with wh._dedup_lock:
        wh._dedup_cache.clear()
        wh._dedup_cache["stale-key"] = time.monotonic() - 120

    # Processing a new alert triggers pruning
    wh._is_duplicate(alert)

    with wh._dedup_lock:
        assert "stale-key" not in wh._dedup_cache
