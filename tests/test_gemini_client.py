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


# ---------------------------------------------------------------------------
# _truncate_details
# ---------------------------------------------------------------------------

def test_truncate_details_clips_to_max_keys():
    big = {str(i): "val" for i in range(gc._DETAILS_MAX_KEYS + 5)}
    result = gc._truncate_details(big)
    assert len(result) == gc._DETAILS_MAX_KEYS


def test_truncate_details_truncates_long_strings():
    d = {"key": "x" * (gc._DETAILS_MAX_VALUE_LEN + 100)}
    result = gc._truncate_details(d)
    assert len(result["key"]) == gc._DETAILS_MAX_VALUE_LEN


def test_truncate_details_preserves_int():
    result = gc._truncate_details({"n": 42})
    assert result["n"] == 42
    assert isinstance(result["n"], int)


def test_truncate_details_preserves_none():
    assert gc._truncate_details({"n": None})["n"] is None


def test_truncate_details_preserves_bool():
    assert gc._truncate_details({"b": True})["b"] is True


def test_truncate_details_empty_stays_empty():
    assert gc._truncate_details({}) == {}


# ---------------------------------------------------------------------------
# _strip_markdown_fence
# ---------------------------------------------------------------------------

def test_strip_fence_plain_json_unchanged():
    raw = '{"insight": "ok", "suggested_actions": []}'
    assert gc._strip_markdown_fence(raw) == raw


def test_strip_fence_removes_backtick_fence():
    raw = '```\n{"insight": "ok"}\n```'
    assert gc._strip_markdown_fence(raw) == '{"insight": "ok"}'


def test_strip_fence_removes_json_prefixed_fence():
    raw = '```json\n{"insight": "ok"}\n```'
    assert gc._strip_markdown_fence(raw) == '{"insight": "ok"}'


def test_strip_fence_no_newline_returned_unchanged():
    raw = "```json"  # starts with fence but has no newline
    assert gc._strip_markdown_fence(raw) == raw


def test_strip_fence_no_closing_fence():
    raw = '```\n{"k": "v"}'  # no closing ```
    assert gc._strip_markdown_fence(raw) == '{"k": "v"}'


# ---------------------------------------------------------------------------
# get_ai_insight — output sanitization
# ---------------------------------------------------------------------------

def _setup_insight_env(monkeypatch):
    monkeypatch.setenv("GEMINI_TOKEN", "test-token")
    monkeypatch.setenv("GEMINI_RPM", "0")
    monkeypatch.setenv("GEMINI_RETRIES", "0")
    monkeypatch.setenv("GEMINI_RETRY_BACKOFF", "0")
    with gc._rpm_lock:
        gc._rpm_call_times.clear()


def _make_gemini_resp(data: dict):
    """Mock a valid Gemini API response wrapping the given dict as JSON text."""
    import json as _json
    mock = MagicMock()
    mock.status_code = 200
    mock.raise_for_status.return_value = None
    mock.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": _json.dumps(data)}]}}]
    }
    return mock


def test_get_ai_insight_non_string_insight_coerced(monkeypatch):
    _setup_insight_env(monkeypatch)
    with patch.object(gc._session, "post", return_value=_make_gemini_resp({"insight": 42, "suggested_actions": []})):
        result = gc.get_ai_insight(_make_alert())
    assert result["insight"] == "42"


def test_get_ai_insight_non_list_actions_treated_as_empty(monkeypatch):
    _setup_insight_env(monkeypatch)
    with patch.object(gc._session, "post", return_value=_make_gemini_resp({"insight": "ok", "suggested_actions": "not a list"})):
        result = gc.get_ai_insight(_make_alert())
    assert result["suggested_actions"] == []


def test_get_ai_insight_actions_capped_at_five(monkeypatch):
    _setup_insight_env(monkeypatch)
    actions = [f"step{i}" for i in range(10)]
    with patch.object(gc._session, "post", return_value=_make_gemini_resp({"insight": "ok", "suggested_actions": actions})):
        result = gc.get_ai_insight(_make_alert())
    assert len(result["suggested_actions"]) == 5


def test_get_ai_insight_insight_capped_at_2000(monkeypatch):
    _setup_insight_env(monkeypatch)
    with patch.object(gc._session, "post", return_value=_make_gemini_resp({"insight": "x" * 5000, "suggested_actions": []})):
        result = gc.get_ai_insight(_make_alert())
    assert len(result["insight"]) == 2000


def test_get_ai_insight_action_items_coerced_to_str(monkeypatch):
    _setup_insight_env(monkeypatch)
    with patch.object(gc._session, "post", return_value=_make_gemini_resp({"insight": "ok", "suggested_actions": [1, 2, 3]})):
        result = gc.get_ai_insight(_make_alert())
    assert result["suggested_actions"] == ["1", "2", "3"]


# ---------------------------------------------------------------------------
# _defang_urls
# ---------------------------------------------------------------------------

def test_defang_http():
    assert gc._defang_urls("check http://example.com for details") == "check http[://]example.com for details"


def test_defang_https():
    assert gc._defang_urls("see https://docs.example.com") == "see https[://]docs.example.com"


def test_defang_no_urls_unchanged():
    text = "restart the nginx service and check logs"
    assert gc._defang_urls(text) == text


def test_defang_multiple_urls():
    text = "see http://a.com and https://b.com"
    result = gc._defang_urls(text)
    assert "http[://]a.com" in result
    assert "https[://]b.com" in result
    assert "http://" not in result
    assert "https://" not in result


def test_defang_applied_to_insight(monkeypatch):
    _setup_insight_env(monkeypatch)
    with patch.object(gc._session, "post", return_value=_make_gemini_resp({
        "insight": "Check https://example.com for details.",
        "suggested_actions": ["Visit http://docs.example.com"],
    })):
        result = gc.get_ai_insight(_make_alert())
    assert "https://" not in result["insight"]
    assert "https[://]" in result["insight"]
    assert "http://" not in result["suggested_actions"][0]
    assert "http[://]" in result["suggested_actions"][0]


def test_get_ai_insight_json_parse_error_returns_fallback(monkeypatch):
    _setup_insight_env(monkeypatch)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": "not valid json at all"}]}}]
    }
    with patch.object(gc._session, "post", return_value=mock_resp):
        result = gc.get_ai_insight(_make_alert())
    assert "parse error" in result["insight"]
    assert isinstance(result["suggested_actions"], list)
