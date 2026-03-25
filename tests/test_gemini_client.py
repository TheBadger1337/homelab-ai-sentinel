"""
Tests for gemini_client: RPM rate limiter, retry logic, and fallback behavior.
"""

import pytest
from unittest.mock import MagicMock, patch

import app.gemini_client as gc
from app.alert_parser import NormalizedAlert


def _make_alert(service="nginx", status="down", severity="critical", message="Connection refused"):
    return NormalizedAlert(
        source="uptime_kuma",
        status=status,
        severity=severity,
        service_name=service,
        message=message,
        details={},
    )


def _mock_ok_response(insight="AI insight", actions=None):
    """Return a mock requests.Response for a successful Gemini call."""
    import json
    mock = MagicMock()
    mock.status_code = 200
    mock.json.return_value = {
        "candidates": [{
            "content": {
                "parts": [{"text": json.dumps({
                    "insight": insight,
                    "suggested_actions": actions or ["check logs"],
                })}]
            }
        }]
    }
    return mock


# ---------------------------------------------------------------------------
# RPM rate limiter
# ---------------------------------------------------------------------------

def test_rpm_limiter_allows_calls_within_limit(monkeypatch):
    """Calls within the RPM limit are allowed."""
    monkeypatch.setenv("GEMINI_RPM", "5")
    monkeypatch.setenv("GEMINI_TOKEN", "test-token")
    # Clear the limiter state
    with gc._rpm_lock:
        gc._rpm_call_times.clear()

    for _ in range(5):
        assert gc._acquire_rpm_slot() is True


def test_rpm_limiter_blocks_over_limit(monkeypatch):
    """The (limit + 1)th call is blocked."""
    monkeypatch.setenv("GEMINI_RPM", "3")
    with gc._rpm_lock:
        gc._rpm_call_times.clear()

    assert gc._acquire_rpm_slot() is True
    assert gc._acquire_rpm_slot() is True
    assert gc._acquire_rpm_slot() is True
    assert gc._acquire_rpm_slot() is False  # 4th call blocked


def test_rpm_limiter_disabled_when_zero(monkeypatch):
    """GEMINI_RPM=0 disables the limiter — all calls are allowed."""
    monkeypatch.setenv("GEMINI_RPM", "0")
    with gc._rpm_lock:
        gc._rpm_call_times.clear()

    for _ in range(100):
        assert gc._acquire_rpm_slot() is True


def test_rpm_limit_returns_fallback(monkeypatch):
    """get_ai_insight returns fallback when RPM limit is reached."""
    monkeypatch.setenv("GEMINI_RPM", "1")
    monkeypatch.setenv("GEMINI_TOKEN", "test-token")
    with gc._rpm_lock:
        gc._rpm_call_times.clear()

    with patch.object(gc._session, "post", return_value=_mock_ok_response()) as mock_post:
        gc._acquire_rpm_slot()  # burn the one allowed slot
        result = gc.get_ai_insight(_make_alert())

    # Fallback should be returned without calling the API
    mock_post.assert_not_called()
    assert "rate limit reached" in result["insight"]


# ---------------------------------------------------------------------------
# Retry logic (_post_gemini)
# ---------------------------------------------------------------------------

def test_retry_on_429(monkeypatch):
    """A 429 response is retried up to GEMINI_RETRIES times."""
    monkeypatch.setenv("GEMINI_RETRIES", "2")
    monkeypatch.setenv("GEMINI_RETRY_BACKOFF", "0")  # no real sleep in tests

    rate_limited = MagicMock()
    rate_limited.status_code = 429

    ok = _mock_ok_response()
    ok.status_code = 200

    with patch.object(gc._session, "post", side_effect=[rate_limited, ok]) as mock_post:
        resp = gc._post_gemini({}, "token")

    assert mock_post.call_count == 2
    assert resp.status_code == 200


def test_retry_on_503(monkeypatch):
    """A 503 is retried."""
    monkeypatch.setenv("GEMINI_RETRIES", "1")
    monkeypatch.setenv("GEMINI_RETRY_BACKOFF", "0")

    err = MagicMock()
    err.status_code = 503
    ok = _mock_ok_response()

    with patch.object(gc._session, "post", side_effect=[err, ok]):
        resp = gc._post_gemini({}, "token")

    assert resp.status_code == 200


def test_no_retry_on_400(monkeypatch):
    """A 400 (bad request) is not retried — it raises immediately."""
    import requests as req
    monkeypatch.setenv("GEMINI_RETRIES", "2")
    monkeypatch.setenv("GEMINI_RETRY_BACKOFF", "0")

    bad = MagicMock()
    bad.status_code = 400
    bad.raise_for_status.side_effect = req.HTTPError("400")

    with patch.object(gc._session, "post", return_value=bad) as mock_post:
        with pytest.raises(req.HTTPError):
            gc._post_gemini({}, "token")

    assert mock_post.call_count == 1  # no retry


def test_exhausted_retries_raise(monkeypatch):
    """When all retries are used, the final error is raised."""
    import requests as req
    monkeypatch.setenv("GEMINI_RETRIES", "1")
    monkeypatch.setenv("GEMINI_RETRY_BACKOFF", "0")

    err_resp = MagicMock()
    err_resp.status_code = 500
    err_resp.raise_for_status.side_effect = req.HTTPError("500")

    with patch.object(gc._session, "post", return_value=err_resp) as mock_post:
        with pytest.raises(req.HTTPError):
            gc._post_gemini({}, "token")

    assert mock_post.call_count == 2  # initial + 1 retry


def test_connection_error_retried(monkeypatch):
    """ConnectionError is retried."""
    import requests as req
    monkeypatch.setenv("GEMINI_RETRIES", "1")
    monkeypatch.setenv("GEMINI_RETRY_BACKOFF", "0")

    ok = _mock_ok_response()

    with patch.object(gc._session, "post", side_effect=[req.ConnectionError("timeout"), ok]) as mock_post:
        resp = gc._post_gemini({}, "token")

    assert mock_post.call_count == 2
    assert resp.status_code == 200


def test_connection_error_exhausted_retries(monkeypatch):
    """ConnectionError re-raises after all retries are used."""
    import requests as req
    monkeypatch.setenv("GEMINI_RETRIES", "1")
    monkeypatch.setenv("GEMINI_RETRY_BACKOFF", "0")

    with patch.object(gc._session, "post", side_effect=req.ConnectionError("unreachable")):
        with pytest.raises(req.ConnectionError):
            gc._post_gemini({}, "token")


# ---------------------------------------------------------------------------
# get_ai_insight fallback behaviour
# ---------------------------------------------------------------------------

def test_get_ai_insight_returns_fallback_on_api_error(monkeypatch):
    """API errors produce a canned fallback response, never raise."""
    import requests as req
    monkeypatch.setenv("GEMINI_TOKEN", "test-token")
    monkeypatch.setenv("GEMINI_RETRIES", "0")
    monkeypatch.setenv("GEMINI_RETRY_BACKOFF", "0")
    monkeypatch.setenv("GEMINI_RPM", "0")

    with patch.object(gc._session, "post", side_effect=req.ConnectionError("fail")):
        result = gc.get_ai_insight(_make_alert())

    assert "insight" in result
    assert isinstance(result["suggested_actions"], list)


def test_get_ai_insight_no_token(monkeypatch):
    """Missing GEMINI_TOKEN returns fallback immediately without HTTP call."""
    monkeypatch.delenv("GEMINI_TOKEN", raising=False)

    with patch.object(gc._session, "post") as mock_post:
        result = gc.get_ai_insight(_make_alert())

    mock_post.assert_not_called()
    assert "GEMINI_TOKEN not set" in result["insight"]
