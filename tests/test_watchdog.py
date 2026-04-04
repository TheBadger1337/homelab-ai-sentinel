"""
Tests for Phase 7: watchdog heartbeat.
"""

import threading
import time

import pytest
from unittest.mock import patch, MagicMock

from app.watchdog import start_watchdog, _heartbeat_loop, _watchdog_thread
import app.watchdog as wd


@pytest.fixture(autouse=True)
def _reset_watchdog():
    """Reset the global watchdog thread state between tests."""
    wd._watchdog_thread = None
    yield
    wd._watchdog_thread = None


def test_watchdog_disabled_when_no_url(monkeypatch):
    monkeypatch.delenv("WATCHDOG_URL", raising=False)
    start_watchdog()
    assert wd._watchdog_thread is None


def test_watchdog_disabled_on_empty_url(monkeypatch):
    monkeypatch.setenv("WATCHDOG_URL", "")
    start_watchdog()
    assert wd._watchdog_thread is None


def test_watchdog_disabled_on_invalid_url(monkeypatch):
    monkeypatch.setenv("WATCHDOG_URL", "http://localhost/ping")
    start_watchdog()
    assert wd._watchdog_thread is None


def test_watchdog_starts_on_valid_url(monkeypatch):
    monkeypatch.setenv("WATCHDOG_URL", "http://192.168.1.10:8080/ping")
    monkeypatch.setenv("WATCHDOG_INTERVAL", "300")

    with patch("app.watchdog.threading.Thread") as MockThread:
        mock_thread = MagicMock()
        MockThread.return_value = mock_thread
        start_watchdog()

        MockThread.assert_called_once()
        mock_thread.start.assert_called_once()
        assert wd._watchdog_thread is mock_thread


def test_watchdog_only_starts_once(monkeypatch):
    monkeypatch.setenv("WATCHDOG_URL", "http://192.168.1.10:8080/ping")

    with patch("app.watchdog.threading.Thread") as MockThread:
        mock_thread = MagicMock()
        MockThread.return_value = mock_thread
        start_watchdog()
        start_watchdog()  # second call is a no-op

        assert MockThread.call_count == 1


def test_watchdog_minimum_interval(monkeypatch):
    monkeypatch.setenv("WATCHDOG_URL", "http://192.168.1.10:8080/ping")
    monkeypatch.setenv("WATCHDOG_INTERVAL", "1")  # too low

    with patch("app.watchdog.threading.Thread") as MockThread:
        mock_thread = MagicMock()
        MockThread.return_value = mock_thread
        start_watchdog()

        # Should have been called with interval=10 (minimum)
        call_args = MockThread.call_args
        assert call_args.kwargs["args"][1] == 10


def test_heartbeat_loop_calls_url():
    """Test that the heartbeat loop makes GET requests."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200

    call_count = 0

    def side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            raise KeyboardInterrupt  # break out of the loop
        return mock_resp

    with patch("app.watchdog.requests.get", side_effect=side_effect):
        with patch("app.watchdog.time.sleep", side_effect=lambda _: None):
            try:
                _heartbeat_loop("http://192.168.1.10:8080/ping", 300)
            except KeyboardInterrupt:
                pass

    assert call_count >= 1


def test_heartbeat_loop_survives_request_error():
    """Heartbeat errors are logged but don't crash the loop."""
    import requests as req

    call_count = 0

    def side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise req.ConnectionError("unreachable")
        if call_count >= 2:
            raise KeyboardInterrupt
        return MagicMock(status_code=200)

    with patch("app.watchdog.requests.get", side_effect=side_effect):
        with patch("app.watchdog.time.sleep", side_effect=lambda _: None):
            try:
                _heartbeat_loop("http://192.168.1.10:8080/ping", 300)
            except KeyboardInterrupt:
                pass

    assert call_count >= 2  # survived the error and tried again


def test_watchdog_thread_is_daemon(monkeypatch):
    monkeypatch.setenv("WATCHDOG_URL", "http://192.168.1.10:8080/ping")

    with patch("app.watchdog.threading.Thread") as MockThread:
        mock_thread = MagicMock()
        MockThread.return_value = mock_thread
        start_watchdog()

        call_kwargs = MockThread.call_args.kwargs
        assert call_kwargs["daemon"] is True
        assert call_kwargs["name"] == "sentinel-watchdog"
