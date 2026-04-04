"""
Tests for storm intelligence — correlated alert batching.
"""

import json
import time

import pytest
from unittest.mock import patch, MagicMock, call

from app.alert_db import init_db, log_alert
from app.alert_parser import NormalizedAlert
from app.storm import (
    BufferedAlert,
    StormBuffer,
    build_storm_prompt,
    _process_storm,
    _process_individual,
    get_storm_buffer,
)
import app.llm_client as lc


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    init_db()


def _make_alert(service="nginx", status="down", severity="critical", message="Connection refused"):
    return NormalizedAlert(
        source="uptime_kuma",
        status=status,
        severity=severity,
        service_name=service,
        message=message,
        details={},
    )


def _make_entry(service="nginx", status="down", severity="critical", message="Connection refused"):
    alert = _make_alert(service=service, status=status, severity=severity, message=message)
    return BufferedAlert(alert, pulse=None, runbook="", topology="")


# ---------------------------------------------------------------------------
# StormBuffer — basic operations
# ---------------------------------------------------------------------------

class TestStormBuffer:
    def test_add_returns_false_when_disabled(self, monkeypatch):
        monkeypatch.setenv("STORM_WINDOW", "0")
        buf = StormBuffer()
        assert buf.add(_make_entry()) is False
        assert buf.pending_count() == 0

    def test_add_returns_true_when_enabled(self, monkeypatch):
        monkeypatch.setenv("STORM_WINDOW", "60")
        buf = StormBuffer()
        try:
            assert buf.add(_make_entry()) is True
            assert buf.pending_count() == 1
        finally:
            buf.cancel()

    def test_multiple_adds_accumulate(self, monkeypatch):
        monkeypatch.setenv("STORM_WINDOW", "60")
        buf = StormBuffer()
        try:
            buf.add(_make_entry(service="nginx"))
            buf.add(_make_entry(service="postgres"))
            buf.add(_make_entry(service="redis"))
            assert buf.pending_count() == 3
        finally:
            buf.cancel()

    def test_cancel_clears_buffer(self, monkeypatch):
        monkeypatch.setenv("STORM_WINDOW", "60")
        buf = StormBuffer()
        buf.add(_make_entry())
        buf.add(_make_entry(service="postgres"))
        entries = buf.cancel()
        assert len(entries) == 2
        assert buf.pending_count() == 0

    def test_flush_now_clears_buffer(self, monkeypatch):
        monkeypatch.setenv("STORM_WINDOW", "60")
        monkeypatch.setenv("STORM_THRESHOLD", "10")  # won't trigger storm
        buf = StormBuffer()
        buf.add(_make_entry())
        with patch("app.storm._process_individual") as mock_proc:
            buf.flush_now()
        assert buf.pending_count() == 0
        mock_proc.assert_called_once()

    def test_flush_triggers_storm_at_threshold(self, monkeypatch):
        monkeypatch.setenv("STORM_WINDOW", "60")
        monkeypatch.setenv("STORM_THRESHOLD", "3")
        buf = StormBuffer()
        buf.add(_make_entry(service="nginx"))
        buf.add(_make_entry(service="postgres"))
        buf.add(_make_entry(service="redis"))
        with patch("app.storm._process_storm") as mock_storm:
            buf.flush_now()
        mock_storm.assert_called_once()
        args = mock_storm.call_args[0][0]
        assert len(args) == 3

    def test_flush_individual_below_threshold(self, monkeypatch):
        monkeypatch.setenv("STORM_WINDOW", "60")
        monkeypatch.setenv("STORM_THRESHOLD", "5")
        buf = StormBuffer()
        buf.add(_make_entry(service="nginx"))
        buf.add(_make_entry(service="postgres"))
        with patch("app.storm._process_individual") as mock_indiv:
            buf.flush_now()
        mock_indiv.assert_called_once()
        args = mock_indiv.call_args[0][0]
        assert len(args) == 2

    def test_flush_empty_buffer_is_noop(self, monkeypatch):
        buf = StormBuffer()
        with patch("app.storm._process_storm") as mock_storm, \
             patch("app.storm._process_individual") as mock_indiv:
            buf.flush_now()
        mock_storm.assert_not_called()
        mock_indiv.assert_not_called()

    def test_timer_started_on_first_add(self, monkeypatch):
        monkeypatch.setenv("STORM_WINDOW", "60")
        buf = StormBuffer()
        try:
            assert buf._timer is None
            buf.add(_make_entry())
            assert buf._timer is not None
            assert buf._timer.daemon is True
        finally:
            buf.cancel()

    def test_storm_fallback_on_storm_error(self, monkeypatch):
        """If _process_storm fails, fall back to _process_individual."""
        monkeypatch.setenv("STORM_WINDOW", "60")
        monkeypatch.setenv("STORM_THRESHOLD", "2")
        buf = StormBuffer()
        buf.add(_make_entry(service="nginx"))
        buf.add(_make_entry(service="postgres"))
        with patch("app.storm._process_storm", side_effect=Exception("boom")), \
             patch("app.storm._process_individual") as mock_indiv:
            buf.flush_now()
        mock_indiv.assert_called_once()


# ---------------------------------------------------------------------------
# build_storm_prompt
# ---------------------------------------------------------------------------

class TestStormPrompt:
    def test_basic_format(self, monkeypatch):
        monkeypatch.setenv("STORM_WINDOW", "60")
        entries = [
            _make_entry(service="nginx", message="Connection refused"),
            _make_entry(service="postgres", message="Connection timeout"),
            _make_entry(service="redis", message="LOADING"),
        ]
        prompt = build_storm_prompt(entries)
        assert "60-second window" in prompt
        assert "Alert 1: nginx" in prompt
        assert "Alert 2: postgres" in prompt
        assert "Alert 3: redis" in prompt
        assert "<alert_data>" in prompt
        assert "cascading failure" in prompt

    def test_includes_pulse_when_available(self, monkeypatch):
        monkeypatch.setenv("STORM_WINDOW", "30")
        entry = _make_entry(service="nginx")
        entry.pulse = {"count_1h": 5, "count_24h": 20, "count_7d": 50, "avg_interval": None, "rate_change": "3x above 7-day average"}
        prompt = build_storm_prompt([entry])
        assert "3x above 7-day average" in prompt

    def test_includes_topology_when_available(self, monkeypatch):
        monkeypatch.setenv("STORM_WINDOW", "60")
        entry = _make_entry(service="nginx")
        entry.topology = 'Service "nginx" depends on docker.'
        prompt = build_storm_prompt([entry])
        assert "<topology>" in prompt
        assert "nginx" in prompt

    def test_deduplicates_topology(self, monkeypatch):
        monkeypatch.setenv("STORM_WINDOW", "60")
        topo = 'Service "nginx" depends on docker.'
        entries = [_make_entry(service="nginx"), _make_entry(service="nginx")]
        entries[0].topology = topo
        entries[1].topology = topo
        prompt = build_storm_prompt(entries)
        assert prompt.count("<topology>") == 1

    def test_message_truncated(self, monkeypatch):
        monkeypatch.setenv("STORM_WINDOW", "60")
        entry = _make_entry(service="nginx", message="x" * 500)
        prompt = build_storm_prompt([entry])
        # Message should be capped at 200 chars in the prompt
        assert "x" * 200 in prompt
        assert "x" * 201 not in prompt


# ---------------------------------------------------------------------------
# _process_storm
# ---------------------------------------------------------------------------

class TestProcessStorm:
    def _mock_ai(self, insight="Storm analysis", actions=None):
        return {"insight": insight, "suggested_actions": actions or ["check root cause"]}

    def test_creates_synthetic_alert_and_dispatches(self, monkeypatch):
        monkeypatch.setenv("AI_PROVIDER", "gemini")
        monkeypatch.setenv("STORM_WINDOW", "60")
        entries = [
            _make_entry(service="nginx"),
            _make_entry(service="postgres"),
            _make_entry(service="redis"),
        ]
        with patch("app.storm.call_provider", return_value=self._mock_ai()) as mock_ai, \
             patch("app.storm.notify") as mock_notify:
            _process_storm(entries)

        mock_ai.assert_called_once()
        mock_notify.dispatch.assert_called_once()
        storm_alert = mock_notify.dispatch.call_args[0][0]
        assert "3 services" in storm_alert.service_name
        assert storm_alert.status == "storm"
        assert storm_alert.severity == "critical"
        assert "nginx" in storm_alert.message
        assert "postgres" in storm_alert.message
        assert "redis" in storm_alert.message

    def test_logs_each_individual_alert(self, monkeypatch):
        monkeypatch.setenv("AI_PROVIDER", "gemini")
        monkeypatch.setenv("STORM_WINDOW", "60")
        entries = [
            _make_entry(service="nginx"),
            _make_entry(service="postgres"),
        ]
        with patch("app.storm.call_provider", return_value=self._mock_ai()), \
             patch("app.storm.notify"), \
             patch("app.storm.log_alert") as mock_log:
            _process_storm(entries)

        assert mock_log.call_count == 2
        logged_services = {c[0][0].service_name for c in mock_log.call_args_list}
        assert logged_services == {"nginx", "postgres"}

    def test_minimal_mode_skips_ai(self, monkeypatch):
        monkeypatch.setenv("SENTINEL_MODE", "minimal")
        entries = [_make_entry(service="nginx"), _make_entry(service="postgres")]
        with patch("app.storm.call_provider") as mock_ai, \
             patch("app.storm.notify") as mock_notify:
            _process_storm(entries)

        mock_ai.assert_not_called()
        # Each alert dispatched individually in minimal mode
        assert mock_notify.dispatch.call_count == 2


# ---------------------------------------------------------------------------
# _process_individual
# ---------------------------------------------------------------------------

class TestProcessIndividual:
    def _mock_ai_result(self):
        return {"insight": "test insight", "suggested_actions": ["action 1"]}

    def test_processes_each_alert(self, monkeypatch):
        monkeypatch.setenv("SENTINEL_MODE", "reactive")
        monkeypatch.setenv("AI_PROVIDER", "gemini")
        entries = [_make_entry(service="nginx"), _make_entry(service="postgres")]
        with patch("app.storm.get_ai_insight", return_value=self._mock_ai_result()) as mock_ai, \
             patch("app.storm.notify") as mock_notify:
            _process_individual(entries)

        assert mock_ai.call_count == 2
        assert mock_notify.dispatch.call_count == 2

    def test_minimal_mode_no_ai(self, monkeypatch):
        monkeypatch.setenv("SENTINEL_MODE", "minimal")
        entries = [_make_entry(service="nginx")]
        with patch("app.storm.get_ai_insight") as mock_ai, \
             patch("app.storm.notify"):
            _process_individual(entries)

        mock_ai.assert_not_called()

    def test_logs_each_alert_as_notified(self, monkeypatch):
        monkeypatch.setenv("SENTINEL_MODE", "reactive")
        entries = [_make_entry(service="nginx")]
        with patch("app.storm.get_ai_insight", return_value=self._mock_ai_result()), \
             patch("app.storm.notify"), \
             patch("app.storm.log_alert") as mock_log:
            _process_individual(entries)

        mock_log.assert_called_once()
        assert mock_log.call_args[1]["notified"] is True or mock_log.call_args[0][2] is True

    def test_continues_on_per_alert_failure(self, monkeypatch):
        monkeypatch.setenv("SENTINEL_MODE", "reactive")
        entries = [
            _make_entry(service="nginx"),
            _make_entry(service="postgres"),
        ]
        call_count = {"n": 0}
        def side_effect(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise Exception("AI failed")
            return self._mock_ai_result()

        with patch("app.storm.get_ai_insight", side_effect=side_effect), \
             patch("app.storm.notify"):
            _process_individual(entries)  # should not raise

        # Both alerts should be processed (second one succeeds)


# ---------------------------------------------------------------------------
# Webhook integration
# ---------------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("SENTINEL_MODE", "predictive")
    monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("DEDUP_TTL_SECONDS", "0")
    monkeypatch.setenv("COOLDOWN_SECONDS", "0")
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_TOKEN", "test-token")
    monkeypatch.setenv("GEMINI_RPM", "0")
    monkeypatch.setenv("GEMINI_RETRIES", "0")
    with lc._gemini_rpm_lock:
        lc._gemini_rpm_call_times.clear()
    from app import create_app
    app = create_app()
    return app.test_client()


def _uptime_kuma_payload(service="nginx", status=0, msg="Connection refused"):
    return {
        "heartbeat": {"status": status},
        "monitor": {"name": service},
        "msg": msg,
    }


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


class TestWebhookStormIntegration:
    def test_alert_buffered_when_storm_enabled(self, client, monkeypatch):
        monkeypatch.setenv("STORM_WINDOW", "60")
        buf = get_storm_buffer()
        buf.cancel()  # clear any prior state

        resp = client.post(
            "/webhook",
            data=json.dumps(_uptime_kuma_payload()),
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "buffered"
        buf.cancel()  # cleanup

    def test_normal_processing_when_storm_disabled(self, client, monkeypatch):
        monkeypatch.setenv("STORM_WINDOW", "0")
        with patch.object(lc._gemini_session, "post", return_value=_mock_gemini_resp()):
            resp = client.post(
                "/webhook",
                data=json.dumps(_uptime_kuma_payload()),
                content_type="application/json",
            )
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "processed"

    def test_recovery_bypasses_storm_buffer(self, client, monkeypatch):
        monkeypatch.setenv("STORM_WINDOW", "60")
        buf = get_storm_buffer()
        buf.cancel()

        with patch.object(lc._gemini_session, "post", return_value=_mock_gemini_resp()):
            resp = client.post(
                "/webhook",
                data=json.dumps(_uptime_kuma_payload(status=1, msg="nginx is up")),
                content_type="application/json",
            )
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "processed"
        assert buf.pending_count() == 0
        buf.cancel()

    def test_multiple_alerts_accumulate_in_buffer(self, client, monkeypatch):
        monkeypatch.setenv("STORM_WINDOW", "60")
        buf = get_storm_buffer()
        buf.cancel()

        for svc in ["nginx", "postgres", "redis"]:
            resp = client.post(
                "/webhook",
                data=json.dumps(_uptime_kuma_payload(service=svc)),
                content_type="application/json",
            )
            assert resp.get_json()["status"] == "buffered"

        assert buf.pending_count() == 3
        buf.cancel()


# ---------------------------------------------------------------------------
# Storm flush DB connection cleanup
# ---------------------------------------------------------------------------

class TestStormFlushDBCleanup:
    """Verify close_thread_conn is called after flush to prevent leaks."""

    def test_flush_calls_close_thread_conn_on_storm(self, monkeypatch):
        monkeypatch.setenv("STORM_WINDOW", "60")
        monkeypatch.setenv("STORM_THRESHOLD", "2")
        buf = StormBuffer()
        for svc in ["nginx", "postgres"]:
            buf.add(_make_entry(service=svc))

        with patch("app.storm.call_provider", return_value={"insight": "storm", "suggested_actions": []}), \
             patch("app.storm.notify") as mock_notify, \
             patch("app.storm.log_alert"), \
             patch("app.storm.close_thread_conn") as mock_close:
            mock_notify.dispatch = MagicMock()
            buf.flush_now()

        mock_close.assert_called_once()

    def test_flush_calls_close_thread_conn_on_individual(self, monkeypatch):
        monkeypatch.setenv("STORM_WINDOW", "60")
        monkeypatch.setenv("STORM_THRESHOLD", "5")
        buf = StormBuffer()
        buf.add(_make_entry(service="nginx"))

        with patch("app.storm.get_ai_insight", return_value={"insight": "ok", "suggested_actions": []}), \
             patch("app.storm.notify") as mock_notify, \
             patch("app.storm.log_alert"), \
             patch("app.storm.close_thread_conn") as mock_close:
            mock_notify.dispatch = MagicMock()
            buf.flush_now()

        mock_close.assert_called_once()

    def test_flush_calls_close_thread_conn_even_on_error(self, monkeypatch):
        monkeypatch.setenv("STORM_WINDOW", "60")
        monkeypatch.setenv("STORM_THRESHOLD", "2")
        buf = StormBuffer()
        for svc in ["nginx", "postgres"]:
            buf.add(_make_entry(service=svc))

        with patch("app.storm.call_provider", side_effect=RuntimeError("AI down")), \
             patch("app.storm.get_ai_insight", side_effect=RuntimeError("also down")), \
             patch("app.storm.notify") as mock_notify, \
             patch("app.storm.log_alert"), \
             patch("app.storm.close_thread_conn") as mock_close:
            mock_notify.dispatch = MagicMock()
            buf.flush_now()

        # close_thread_conn must be called even when processing fails
        mock_close.assert_called_once()
