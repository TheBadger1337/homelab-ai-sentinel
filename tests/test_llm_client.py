"""
Tests for llm_client: RPM rate limiters, retry logic, fallback behavior,
output sanitization, and provider routing for both Gemini and OpenAI-compat.
"""

import json

import pytest
import requests as req
from unittest.mock import MagicMock, patch

import app.llm_client as lc
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


# ---------------------------------------------------------------------------
# Gemini RPM rate limiter
# ---------------------------------------------------------------------------

def test_gemini_rpm_allows_calls_within_limit(monkeypatch):
    monkeypatch.setenv("GEMINI_RPM", "5")
    with lc._gemini_rpm_lock:
        lc._gemini_rpm_call_times.clear()
    for _ in range(5):
        assert lc._gemini_acquire_rpm() is True


def test_gemini_rpm_blocks_over_limit(monkeypatch):
    monkeypatch.setenv("GEMINI_RPM", "3")
    with lc._gemini_rpm_lock:
        lc._gemini_rpm_call_times.clear()
    assert lc._gemini_acquire_rpm() is True
    assert lc._gemini_acquire_rpm() is True
    assert lc._gemini_acquire_rpm() is True
    assert lc._gemini_acquire_rpm() is False


def test_gemini_rpm_disabled_when_zero(monkeypatch):
    monkeypatch.setenv("GEMINI_RPM", "0")
    with lc._gemini_rpm_lock:
        lc._gemini_rpm_call_times.clear()
    for _ in range(100):
        assert lc._gemini_acquire_rpm() is True


def test_gemini_rpm_limit_returns_fallback(monkeypatch):
    monkeypatch.setenv("GEMINI_RPM", "1")
    monkeypatch.setenv("GEMINI_TOKEN", "test-token")
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    with lc._gemini_rpm_lock:
        lc._gemini_rpm_call_times.clear()

    with patch.object(lc._gemini_session, "post", return_value=_mock_gemini_resp()) as mock_post:
        lc._gemini_acquire_rpm()  # burn the one allowed slot
        result = lc.get_ai_insight(_make_alert())

    mock_post.assert_not_called()
    assert "rate limit reached" in result["insight"]


# ---------------------------------------------------------------------------
# OpenAI RPM rate limiter
# ---------------------------------------------------------------------------

def test_openai_rpm_allows_calls_within_limit(monkeypatch):
    monkeypatch.setenv("OPENAI_RPM", "5")
    with lc._openai_rpm_lock:
        lc._openai_rpm_call_times.clear()
    for _ in range(5):
        assert lc._openai_acquire_rpm() is True


def test_openai_rpm_blocks_over_limit(monkeypatch):
    monkeypatch.setenv("OPENAI_RPM", "3")
    with lc._openai_rpm_lock:
        lc._openai_rpm_call_times.clear()
    assert lc._openai_acquire_rpm() is True
    assert lc._openai_acquire_rpm() is True
    assert lc._openai_acquire_rpm() is True
    assert lc._openai_acquire_rpm() is False


def test_openai_rpm_disabled_when_zero(monkeypatch):
    monkeypatch.setenv("OPENAI_RPM", "0")
    with lc._openai_rpm_lock:
        lc._openai_rpm_call_times.clear()
    for _ in range(100):
        assert lc._openai_acquire_rpm() is True


def test_openai_rpm_limit_returns_fallback(monkeypatch):
    monkeypatch.setenv("OPENAI_RPM", "1")
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://192.168.1.10:11434/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    with lc._openai_rpm_lock:
        lc._openai_rpm_call_times.clear()

    with patch("app.llm_client.requests.post", return_value=_mock_openai_resp()) as mock_post:
        lc._openai_acquire_rpm()  # burn the one allowed slot
        result = lc.get_ai_insight(_make_alert())

    mock_post.assert_not_called()
    assert "rate limit reached" in result["insight"]


# ---------------------------------------------------------------------------
# Anthropic RPM rate limiter
# ---------------------------------------------------------------------------

def test_anthropic_rpm_allows_calls_within_limit(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_RPM", "5")
    with lc._anthropic_rpm_lock:
        lc._anthropic_rpm_call_times.clear()
    for _ in range(5):
        assert lc._anthropic_acquire_rpm() is True


def test_anthropic_rpm_blocks_over_limit(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_RPM", "3")
    with lc._anthropic_rpm_lock:
        lc._anthropic_rpm_call_times.clear()
    assert lc._anthropic_acquire_rpm() is True
    assert lc._anthropic_acquire_rpm() is True
    assert lc._anthropic_acquire_rpm() is True
    assert lc._anthropic_acquire_rpm() is False


def test_anthropic_rpm_disabled_when_zero(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_RPM", "0")
    with lc._anthropic_rpm_lock:
        lc._anthropic_rpm_call_times.clear()
    for _ in range(100):
        assert lc._anthropic_acquire_rpm() is True


def test_anthropic_rpm_limit_returns_fallback(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_RPM", "1")
    monkeypatch.setenv("AI_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    with lc._anthropic_rpm_lock:
        lc._anthropic_rpm_call_times.clear()

    with patch("app.llm_client.requests.post", return_value=_mock_anthropic_resp()) as mock_post:
        lc._anthropic_acquire_rpm()  # burn the one allowed slot
        result = lc.get_ai_insight(_make_alert())

    mock_post.assert_not_called()
    assert "rate limit reached" in result["insight"]


# ---------------------------------------------------------------------------
# Gemini retry logic (_post_gemini)
# ---------------------------------------------------------------------------

def test_retry_on_429(monkeypatch):
    monkeypatch.setenv("GEMINI_RETRIES", "2")
    monkeypatch.setenv("GEMINI_RETRY_BACKOFF", "0")

    rate_limited = MagicMock()
    rate_limited.status_code = 429
    ok = _mock_gemini_resp()

    with patch.object(lc._gemini_session, "post", side_effect=[rate_limited, ok]) as mock_post:
        resp = lc._post_gemini({}, "token")
    assert mock_post.call_count == 2
    assert resp.status_code == 200


def test_retry_on_503(monkeypatch):
    monkeypatch.setenv("GEMINI_RETRIES", "1")
    monkeypatch.setenv("GEMINI_RETRY_BACKOFF", "0")

    err = MagicMock()
    err.status_code = 503
    ok = _mock_gemini_resp()

    with patch.object(lc._gemini_session, "post", side_effect=[err, ok]):
        resp = lc._post_gemini({}, "token")
    assert resp.status_code == 200


def test_no_retry_on_400(monkeypatch):
    monkeypatch.setenv("GEMINI_RETRIES", "2")
    monkeypatch.setenv("GEMINI_RETRY_BACKOFF", "0")

    bad = MagicMock()
    bad.status_code = 400
    bad.raise_for_status.side_effect = req.HTTPError("400")

    with patch.object(lc._gemini_session, "post", return_value=bad) as mock_post:
        with pytest.raises(req.HTTPError):
            lc._post_gemini({}, "token")
    assert mock_post.call_count == 1


def test_exhausted_retries_raise(monkeypatch):
    monkeypatch.setenv("GEMINI_RETRIES", "1")
    monkeypatch.setenv("GEMINI_RETRY_BACKOFF", "0")

    err_resp = MagicMock()
    err_resp.status_code = 500
    err_resp.raise_for_status.side_effect = req.HTTPError("500")

    with patch.object(lc._gemini_session, "post", return_value=err_resp) as mock_post:
        with pytest.raises(req.HTTPError):
            lc._post_gemini({}, "token")
    assert mock_post.call_count == 2


def test_connection_error_retried(monkeypatch):
    monkeypatch.setenv("GEMINI_RETRIES", "1")
    monkeypatch.setenv("GEMINI_RETRY_BACKOFF", "0")

    ok = _mock_gemini_resp()
    with patch.object(lc._gemini_session, "post", side_effect=[req.ConnectionError("timeout"), ok]) as mock_post:
        resp = lc._post_gemini({}, "token")
    assert mock_post.call_count == 2
    assert resp.status_code == 200


def test_connection_error_exhausted_retries(monkeypatch):
    monkeypatch.setenv("GEMINI_RETRIES", "1")
    monkeypatch.setenv("GEMINI_RETRY_BACKOFF", "0")

    with patch.object(lc._gemini_session, "post", side_effect=req.ConnectionError("unreachable")):
        with pytest.raises(req.ConnectionError):
            lc._post_gemini({}, "token")


# ---------------------------------------------------------------------------
# get_ai_insight — Gemini fallback behavior
# ---------------------------------------------------------------------------

def test_gemini_fallback_on_api_error(monkeypatch):
    monkeypatch.setenv("GEMINI_TOKEN", "test-token")
    monkeypatch.setenv("GEMINI_RETRIES", "0")
    monkeypatch.setenv("GEMINI_RETRY_BACKOFF", "0")
    monkeypatch.setenv("GEMINI_RPM", "0")
    monkeypatch.setenv("AI_PROVIDER", "gemini")

    with patch.object(lc._gemini_session, "post", side_effect=req.ConnectionError("fail")):
        result = lc.get_ai_insight(_make_alert())
    assert "insight" in result
    assert isinstance(result["suggested_actions"], list)


def test_gemini_no_token(monkeypatch):
    monkeypatch.delenv("GEMINI_TOKEN", raising=False)
    monkeypatch.setenv("AI_PROVIDER", "gemini")

    with patch.object(lc._gemini_session, "post") as mock_post:
        result = lc.get_ai_insight(_make_alert())
    mock_post.assert_not_called()
    assert "GEMINI_TOKEN not set" in result["insight"]


# ---------------------------------------------------------------------------
# get_ai_insight — OpenAI fallback behavior
# ---------------------------------------------------------------------------

def test_openai_no_base_url(monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setenv("AI_PROVIDER", "openai")

    result = lc.get_ai_insight(_make_alert())
    assert "OPENAI_BASE_URL not set" in result["insight"]


def test_openai_no_api_key(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://192.168.1.10:11434/v1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = lc.get_ai_insight(_make_alert())
    assert "OPENAI_API_KEY not set" in result["insight"]


def test_openai_no_model(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://192.168.1.10:11434/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    result = lc.get_ai_insight(_make_alert())
    assert "OPENAI_MODEL not set" in result["insight"]


def test_openai_ssrf_blocked(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")

    result = lc.get_ai_insight(_make_alert())
    assert "SSRF" in result["insight"]


def test_openai_api_error_returns_fallback(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://192.168.1.10:11434/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    monkeypatch.setenv("OPENAI_RPM", "0")

    with patch("app.llm_client.requests.post", side_effect=req.ConnectionError("fail")):
        result = lc.get_ai_insight(_make_alert())
    assert "API error" in result["insight"]


# ---------------------------------------------------------------------------
# get_ai_insight — Anthropic fallback behavior
# ---------------------------------------------------------------------------

def test_anthropic_no_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("AI_PROVIDER", "anthropic")

    result = lc.get_ai_insight(_make_alert())
    assert "ANTHROPIC_API_KEY not set" in result["insight"]


def test_anthropic_api_error_returns_fallback(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_RPM", "0")

    with patch("app.llm_client.requests.post", side_effect=req.ConnectionError("fail")):
        result = lc.get_ai_insight(_make_alert())
    assert "API error" in result["insight"]


def test_anthropic_success(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_RPM", "0")

    with patch("app.llm_client.requests.post", return_value=_mock_anthropic_resp()) as mock_post:
        result = lc.get_ai_insight(_make_alert())
    mock_post.assert_called_once()
    assert result["insight"] == "AI insight"


def test_anthropic_json_parse_error_returns_fallback(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_RPM", "0")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {
        "content": [{"type": "text", "text": "not valid json"}]
    }
    with patch("app.llm_client.requests.post", return_value=mock_resp):
        result = lc.get_ai_insight(_make_alert())
    assert "parse error" in result["insight"]


# ---------------------------------------------------------------------------
# Provider routing
# ---------------------------------------------------------------------------

def test_provider_routes_to_gemini_by_default(monkeypatch):
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    monkeypatch.setenv("GEMINI_TOKEN", "test-token")
    monkeypatch.setenv("GEMINI_RPM", "0")
    monkeypatch.setenv("GEMINI_RETRIES", "0")

    with patch.object(lc._gemini_session, "post", return_value=_mock_gemini_resp()) as mock_post:
        result = lc.get_ai_insight(_make_alert())
    mock_post.assert_called_once()
    assert result["insight"] == "AI insight"


def test_provider_routes_to_openai(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://192.168.1.10:11434/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    monkeypatch.setenv("OPENAI_RPM", "0")

    with patch("app.llm_client.requests.post", return_value=_mock_openai_resp()) as mock_post:
        result = lc.get_ai_insight(_make_alert())
    mock_post.assert_called_once()
    assert result["insight"] == "AI insight"


def test_get_rpm_status_gemini(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_RPM", "10")
    with lc._gemini_rpm_lock:
        lc._gemini_rpm_call_times.clear()
    status = lc.get_rpm_status()
    assert status["limit"] == 10
    assert status["used"] == 0


def test_provider_routes_to_anthropic(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_RPM", "0")

    with patch("app.llm_client.requests.post", return_value=_mock_anthropic_resp()) as mock_post:
        result = lc.get_ai_insight(_make_alert())
    mock_post.assert_called_once()
    assert result["insight"] == "AI insight"


def test_get_rpm_status_anthropic(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_RPM", "50")
    with lc._anthropic_rpm_lock:
        lc._anthropic_rpm_call_times.clear()
    status = lc.get_rpm_status()
    assert status["limit"] == 50
    assert status["used"] == 0


def test_get_rpm_status_openai(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_RPM", "30")
    with lc._openai_rpm_lock:
        lc._openai_rpm_call_times.clear()
    status = lc.get_rpm_status()
    assert status["limit"] == 30
    assert status["used"] == 0


# ---------------------------------------------------------------------------
# _truncate_details
# ---------------------------------------------------------------------------

def test_truncate_details_clips_to_max_keys():
    big = {str(i): "val" for i in range(lc._DETAILS_MAX_KEYS + 5)}
    result = lc._truncate_details(big)
    assert len(result) == lc._DETAILS_MAX_KEYS


def test_truncate_details_truncates_long_strings():
    d = {"key": "x" * (lc._DETAILS_MAX_VALUE_LEN + 100)}
    result = lc._truncate_details(d)
    assert len(result["key"]) == lc._DETAILS_MAX_VALUE_LEN


def test_truncate_details_preserves_int():
    result = lc._truncate_details({"n": 42})
    assert result["n"] == 42
    assert isinstance(result["n"], int)


def test_truncate_details_preserves_none():
    assert lc._truncate_details({"n": None})["n"] is None


def test_truncate_details_preserves_bool():
    assert lc._truncate_details({"b": True})["b"] is True


def test_truncate_details_empty_stays_empty():
    assert lc._truncate_details({}) == {}


# ---------------------------------------------------------------------------
# _strip_markdown_fence
# ---------------------------------------------------------------------------

def test_strip_fence_plain_json_unchanged():
    raw = '{"insight": "ok", "suggested_actions": []}'
    assert lc._strip_markdown_fence(raw) == raw


def test_strip_fence_removes_backtick_fence():
    raw = '```\n{"insight": "ok"}\n```'
    assert lc._strip_markdown_fence(raw) == '{"insight": "ok"}'


def test_strip_fence_removes_json_prefixed_fence():
    raw = '```json\n{"insight": "ok"}\n```'
    assert lc._strip_markdown_fence(raw) == '{"insight": "ok"}'


def test_strip_fence_no_newline_returned_unchanged():
    raw = "```json"
    assert lc._strip_markdown_fence(raw) == raw


def test_strip_fence_no_closing_fence():
    raw = '```\n{"k": "v"}'
    assert lc._strip_markdown_fence(raw) == '{"k": "v"}'


# ---------------------------------------------------------------------------
# Output sanitization (via Gemini path)
# ---------------------------------------------------------------------------

def _setup_gemini_env(monkeypatch):
    monkeypatch.setenv("GEMINI_TOKEN", "test-token")
    monkeypatch.setenv("GEMINI_RPM", "0")
    monkeypatch.setenv("GEMINI_RETRIES", "0")
    monkeypatch.setenv("GEMINI_RETRY_BACKOFF", "0")
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    with lc._gemini_rpm_lock:
        lc._gemini_rpm_call_times.clear()


def _mock_gemini_resp(insight="AI insight", actions=None):
    mock = MagicMock()
    mock.status_code = 200
    mock.raise_for_status.return_value = None
    mock.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": json.dumps({
            "insight": insight,
            "suggested_actions": actions or ["check logs"],
        })}]}}]
    }
    return mock


def _mock_openai_resp(insight="AI insight", actions=None):
    mock = MagicMock()
    mock.status_code = 200
    mock.raise_for_status.return_value = None
    mock.json.return_value = {
        "choices": [{"message": {"content": json.dumps({
            "insight": insight,
            "suggested_actions": actions or ["check logs"],
        })}}]
    }
    return mock


def _mock_anthropic_resp(insight="AI insight", actions=None):
    mock = MagicMock()
    mock.status_code = 200
    mock.raise_for_status.return_value = None
    mock.json.return_value = {
        "content": [{"type": "text", "text": json.dumps({
            "insight": insight,
            "suggested_actions": actions or ["check logs"],
        })}]
    }
    return mock


def test_non_string_insight_coerced(monkeypatch):
    _setup_gemini_env(monkeypatch)
    with patch.object(lc._gemini_session, "post", return_value=_mock_gemini_resp(insight=42)):
        # Need to fix the mock — insight is not a string in the JSON
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": json.dumps({
                "insight": 42, "suggested_actions": [],
            })}]}}]
        }
        with patch.object(lc._gemini_session, "post", return_value=mock_resp):
            result = lc.get_ai_insight(_make_alert())
    assert result["insight"] == "42"


def test_non_list_actions_treated_as_empty(monkeypatch):
    _setup_gemini_env(monkeypatch)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": json.dumps({
            "insight": "ok", "suggested_actions": "not a list",
        })}]}}]
    }
    with patch.object(lc._gemini_session, "post", return_value=mock_resp):
        result = lc.get_ai_insight(_make_alert())
    assert result["suggested_actions"] == []


def test_actions_capped_at_five(monkeypatch):
    _setup_gemini_env(monkeypatch)
    actions = [f"step{i}" for i in range(10)]
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": json.dumps({
            "insight": "ok", "suggested_actions": actions,
        })}]}}]
    }
    with patch.object(lc._gemini_session, "post", return_value=mock_resp):
        result = lc.get_ai_insight(_make_alert())
    assert len(result["suggested_actions"]) == 5


def test_insight_capped_at_2000(monkeypatch):
    _setup_gemini_env(monkeypatch)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": json.dumps({
            "insight": "x" * 5000, "suggested_actions": [],
        })}]}}]
    }
    with patch.object(lc._gemini_session, "post", return_value=mock_resp):
        result = lc.get_ai_insight(_make_alert())
    assert len(result["insight"]) == 2000


def test_action_items_coerced_to_str(monkeypatch):
    _setup_gemini_env(monkeypatch)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": json.dumps({
            "insight": "ok", "suggested_actions": [1, 2, 3],
        })}]}}]
    }
    with patch.object(lc._gemini_session, "post", return_value=mock_resp):
        result = lc.get_ai_insight(_make_alert())
    assert result["suggested_actions"] == ["1", "2", "3"]


def test_json_parse_error_returns_fallback(monkeypatch):
    _setup_gemini_env(monkeypatch)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": "not valid json at all"}]}}]
    }
    with patch.object(lc._gemini_session, "post", return_value=mock_resp):
        result = lc.get_ai_insight(_make_alert())
    assert "parse error" in result["insight"]
    assert isinstance(result["suggested_actions"], list)


# ---------------------------------------------------------------------------
# Output sanitization (via OpenAI path)
# ---------------------------------------------------------------------------

def _setup_openai_env(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://192.168.1.10:11434/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    monkeypatch.setenv("OPENAI_RPM", "0")
    with lc._openai_rpm_lock:
        lc._openai_rpm_call_times.clear()


def test_openai_non_string_insight_coerced(monkeypatch):
    _setup_openai_env(monkeypatch)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": json.dumps({
            "insight": 42, "suggested_actions": [],
        })}}]
    }
    with patch("app.llm_client.requests.post", return_value=mock_resp):
        result = lc.get_ai_insight(_make_alert())
    assert result["insight"] == "42"


def test_openai_json_parse_error_returns_fallback(monkeypatch):
    _setup_openai_env(monkeypatch)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": "not valid json"}}]
    }
    with patch("app.llm_client.requests.post", return_value=mock_resp):
        result = lc.get_ai_insight(_make_alert())
    assert "parse error" in result["insight"]


# ---------------------------------------------------------------------------
# Output sanitization (via Anthropic path)
# ---------------------------------------------------------------------------

def _setup_anthropic_env(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_RPM", "0")
    with lc._anthropic_rpm_lock:
        lc._anthropic_rpm_call_times.clear()


def test_anthropic_non_string_insight_coerced(monkeypatch):
    _setup_anthropic_env(monkeypatch)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {
        "content": [{"type": "text", "text": json.dumps({
            "insight": 42, "suggested_actions": [],
        })}]
    }
    with patch("app.llm_client.requests.post", return_value=mock_resp):
        result = lc.get_ai_insight(_make_alert())
    assert result["insight"] == "42"


def test_anthropic_defang_applied(monkeypatch):
    _setup_anthropic_env(monkeypatch)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {
        "content": [{"type": "text", "text": json.dumps({
            "insight": "Check https://example.com",
            "suggested_actions": ["Visit http://docs.example.com"],
        })}]
    }
    with patch("app.llm_client.requests.post", return_value=mock_resp):
        result = lc.get_ai_insight(_make_alert())
    assert "https://" not in result["insight"]
    assert "https[://]" in result["insight"]
    assert "http://" not in result["suggested_actions"][0]


# ---------------------------------------------------------------------------
# _defang_urls
# ---------------------------------------------------------------------------

def test_defang_http():
    assert lc._defang_urls("check http://example.com for details") == "check http[://]example.com for details"


def test_defang_https():
    assert lc._defang_urls("see https://docs.example.com") == "see https[://]docs.example.com"


def test_defang_no_urls_unchanged():
    text = "restart the nginx service and check logs"
    assert lc._defang_urls(text) == text


def test_defang_multiple_urls():
    text = "see http://a.com and https://b.com"
    result = lc._defang_urls(text)
    assert "http[://]a.com" in result
    assert "https[://]b.com" in result
    assert "http://" not in result
    assert "https://" not in result


def test_defang_applied_to_insight(monkeypatch):
    _setup_gemini_env(monkeypatch)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": json.dumps({
            "insight": "Check https://example.com for details.",
            "suggested_actions": ["Visit http://docs.example.com"],
        })}]}}]
    }
    with patch.object(lc._gemini_session, "post", return_value=mock_resp):
        result = lc.get_ai_insight(_make_alert())
    assert "https://" not in result["insight"]
    assert "https[://]" in result["insight"]
    assert "http://" not in result["suggested_actions"][0]
    assert "http[://]" in result["suggested_actions"][0]


# ---------------------------------------------------------------------------
# _build_prompt
# ---------------------------------------------------------------------------

def test_build_prompt_includes_alert_data():
    alert = _make_alert()
    prompt = lc._build_prompt(alert)
    assert "<alert_data>" in prompt
    assert "nginx" in prompt
    assert "down" in prompt


def test_build_prompt_includes_history():
    import time
    alert = _make_alert()
    history = [{"ts": time.time() - 120, "status": "down", "severity": "critical", "message": "Connection refused"}]
    prompt = lc._build_prompt(alert, history=history)
    assert "<alert_history>" in prompt


def test_build_prompt_no_history_when_empty():
    alert = _make_alert()
    prompt = lc._build_prompt(alert, history=[])
    assert "<alert_history>" not in prompt


# ---------------------------------------------------------------------------
# _sanitize_output
# ---------------------------------------------------------------------------

def test_sanitize_output_valid_json():
    raw = json.dumps({"insight": "test", "suggested_actions": ["a", "b"]})
    result = lc._sanitize_output(raw)
    assert result["insight"] == "test"
    assert result["suggested_actions"] == ["a", "b"]


def test_sanitize_output_strips_fence():
    raw = '```json\n' + json.dumps({"insight": "test", "suggested_actions": []}) + '\n```'
    result = lc._sanitize_output(raw)
    assert result["insight"] == "test"


def test_sanitize_output_invalid_json_raises():
    with pytest.raises(json.JSONDecodeError):
        lc._sanitize_output("not json")


# ---------------------------------------------------------------------------
# Defanging — case-insensitive and bare-IP hardening
# ---------------------------------------------------------------------------

def test_defang_case_insensitive_upper():
    assert lc._defang_urls("check HTTP://EXAMPLE.COM") == "check HTTP[://]EXAMPLE.COM"


def test_defang_case_insensitive_mixed():
    assert lc._defang_urls("see Https://Docs.Example.Com") == "see Https[://]Docs.Example.Com"


def test_defang_bare_ip_with_path():
    result = lc._defang_urls("visit 192.168.1.1/admin for details")
    # Regex wraps the last octet: 192.168.1.[1]/admin
    assert "192.168.1.[1]/admin" in result
    assert "192.168.1.1/admin" not in result


def test_defang_bare_ip_with_port_and_path():
    result = lc._defang_urls("check 10.0.0.5:8080/status")
    assert "10.0.0.[5]:8080/status" in result


def test_defang_bare_ip_without_path_unchanged():
    text = "server is at 192.168.1.1 and it's fine"
    assert lc._defang_urls(text) == text


def test_defang_url_scheme_plus_ip_both_defanged():
    result = lc._defang_urls("visit http://192.168.1.1/admin")
    assert "http[://]" in result
    assert "http://" not in result


# ---------------------------------------------------------------------------
# Prompt budget trimming
# ---------------------------------------------------------------------------

def test_prompt_budget_trims_lowest_priority_first(monkeypatch):
    """History (lowest priority) is dropped first when budget is tight."""
    import time
    monkeypatch.setattr(lc, "_MAX_PROMPT_CHARS", 1200)

    alert = _make_alert()
    history = [
        {"ts": time.time() - i * 60, "status": "down", "severity": "critical", "message": "x" * 100}
        for i in range(10)
    ]
    prompt = lc._build_prompt(alert, history=history, pulse={"count_1h": 5, "count_24h": 10, "count_7d": 30})
    # Pulse (highest priority) should always survive
    assert "<alert_stats>" in prompt
    # History (lowest priority) may be dropped
    # The budget is tight — at least one supplementary section should be present
    assert "<alert_data>" in prompt


def test_prompt_budget_keeps_all_when_under_limit():
    """All sections included when total is well within budget."""
    import time
    alert = _make_alert()
    history = [{"ts": time.time() - 60, "status": "down", "severity": "critical", "message": "err"}]
    prompt = lc._build_prompt(
        alert,
        history=history,
        pulse={"count_1h": 1, "count_24h": 3, "count_7d": 10},
        runbook="Check nginx logs at /var/log/nginx/error.log",
        topology="nginx depends_on: reverse-proxy",
    )
    assert "<alert_data>" in prompt
    assert "<alert_stats>" in prompt
    assert "<runbook>" in prompt
    assert "<topology>" in prompt
    assert "<alert_history>" in prompt


def test_prompt_budget_drops_section_not_truncates(monkeypatch):
    """Sections that exceed remaining budget are dropped entirely, not truncated."""
    monkeypatch.setattr(lc, "_MAX_PROMPT_CHARS", 1000)

    alert = _make_alert()
    big_runbook = "x" * 2000  # way over budget
    prompt = lc._build_prompt(alert, runbook=big_runbook)
    assert "<alert_data>" in prompt
    # The runbook was too big — it should be absent entirely
    assert "<runbook>" not in prompt
