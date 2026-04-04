"""
Watchdog heartbeat — periodic ping to an external monitoring endpoint.

If Sentinel hangs, crashes, or loses AI connectivity, the heartbeat stops
and your external monitoring tool (Healthchecks.io, Uptime Kuma, etc.)
alerts you that Sentinel itself is down.

Configuration:
  WATCHDOG_URL       — URL to GET/POST on each heartbeat (required to enable)
  WATCHDOG_INTERVAL  — seconds between heartbeats (default: 300 = 5 minutes)

The watchdog runs as a daemon thread started in create_app(). It exits
silently when the main process terminates. Heartbeat failures are logged
at WARNING level but never interrupt the alert pipeline.
"""

import logging
import os
import threading
import time

import requests

from .utils import _env_int, _validate_url

logger = logging.getLogger(__name__)

_watchdog_thread: threading.Thread | None = None


def _heartbeat_loop(url: str, interval: int) -> None:
    """Send periodic heartbeats. Runs forever in a daemon thread."""
    while True:
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            logger.debug("Watchdog heartbeat OK: %s", resp.status_code)
        except requests.RequestException as exc:
            logger.warning("Watchdog heartbeat failed: %s", type(exc).__name__)
        except Exception:
            logger.warning("Watchdog unexpected error", exc_info=True)
        time.sleep(interval)


def start_watchdog() -> None:
    """
    Start the watchdog heartbeat thread if WATCHDOG_URL is configured.
    Called once from create_app(). Safe to call multiple times — only the
    first call starts the thread.
    """
    global _watchdog_thread
    if _watchdog_thread is not None:
        return  # already running

    url = os.environ.get("WATCHDOG_URL", "").strip()
    if not url:
        return  # watchdog disabled

    if not _validate_url(url, "WATCHDOG_URL"):
        logger.warning("WATCHDOG_URL failed validation — watchdog disabled")
        return

    interval = _env_int("WATCHDOG_INTERVAL", 300)
    if interval < 10:
        logger.warning("WATCHDOG_INTERVAL=%d is too low — using 10s minimum", interval)
        interval = 10

    _watchdog_thread = threading.Thread(
        target=_heartbeat_loop,
        args=(url, interval),
        daemon=True,
        name="sentinel-watchdog",
    )
    _watchdog_thread.start()
    logger.info("Watchdog started: url=%s interval=%ds", url, interval)
