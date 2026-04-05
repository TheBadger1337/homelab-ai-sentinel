"""
Server-Sent Events (SSE) event bus.

Thread-safe pub/sub for real-time UI updates. The webhook handler publishes
events after processing; connected SSE clients receive them in real time.

Design:
  - Each connected client gets a queue.Queue registered in a subscriber list
  - Publishing puts the event on all subscriber queues (fan-out)
  - Max subscribers: SSE_MAX_CLIENTS (default 10) — homelab, not SaaS
  - Heartbeat: empty comment every 30s to keep connections alive
  - Clients that don't drain their queue within 5 minutes are evicted

Security: SSE endpoint requires UI session auth. Events contain the same
data visible via /api/alerts — no additional exposure.
"""

import json
import logging
import queue
import threading
import time
from typing import Any, Generator

from .utils import _env_int

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_subscribers: list[dict] = []  # [{"q": Queue, "ts": float}]


def subscribe() -> dict | None:
    """
    Register a new SSE subscriber. Returns the subscriber dict (contains
    the queue to read from), or None if at capacity.
    """
    max_clients = _env_int("SSE_MAX_CLIENTS", 10)
    with _lock:
        # Evict stale subscribers before checking capacity
        _evict_stale()
        if len(_subscribers) >= max_clients:
            logger.warning("SSE at capacity (%d) — rejecting new subscriber", max_clients)
            return None
        sub = {"q": queue.Queue(maxsize=100), "ts": time.time()}
        _subscribers.append(sub)
        logger.info("SSE subscriber added (total: %d)", len(_subscribers))
        return sub


def unsubscribe(sub: dict) -> None:
    """Remove a subscriber."""
    with _lock:
        try:
            _subscribers.remove(sub)
        except ValueError:
            pass
        logger.info("SSE subscriber removed (total: %d)", len(_subscribers))


def publish(event_type: str, data: dict[str, Any]) -> None:
    """
    Broadcast an event to all subscribers.

    event_type: "alert" | "incident" | "resolution" | "stats"
    data: JSON-serializable dict
    """
    payload = json.dumps({"type": event_type, "data": data})
    with _lock:
        _evict_stale()
        for sub in _subscribers:
            try:
                sub["q"].put_nowait(payload)
                sub["ts"] = time.time()
            except queue.Full:
                logger.debug("SSE subscriber queue full — dropping event")


def stream(sub: dict) -> Generator[str, None, None]:
    """
    Generator that yields SSE-formatted events for a subscriber.

    Yields:
      - "data: {json}\n\n" for real events
      - ": heartbeat\n\n" every 30s to keep the connection alive

    The generator exits when the subscriber is evicted or after 5 minutes
    of inactivity (no real events, only heartbeats).
    """
    heartbeat_interval = 30
    max_idle = 300  # 5 minutes
    last_event = time.time()

    try:
        while True:
            try:
                payload = sub["q"].get(timeout=heartbeat_interval)
                last_event = time.time()
                yield f"data: {payload}\n\n"
            except queue.Empty:
                # No event — send heartbeat
                yield ": heartbeat\n\n"
                if (time.time() - last_event) > max_idle:
                    logger.info("SSE subscriber timed out after %ds idle", max_idle)
                    return
    finally:
        unsubscribe(sub)


def _evict_stale() -> None:
    """Remove subscribers that haven't been active in 5+ minutes. Caller holds _lock."""
    max_idle = 300
    now = time.time()
    stale = [s for s in _subscribers if (now - s["ts"]) > max_idle]
    for s in stale:
        _subscribers.remove(s)
    if stale:
        logger.info("Evicted %d stale SSE subscriber(s)", len(stale))


def subscriber_count() -> int:
    """Return current subscriber count."""
    with _lock:
        return len(_subscribers)
