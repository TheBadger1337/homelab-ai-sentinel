"""
Flask blueprint: POST /webhook

Flow:
  1. Optional shared-secret authentication (WEBHOOK_SECRET env var)
  2. Optional webhook rate limiter (WEBHOOK_RATE_LIMIT / WEBHOOK_RATE_WINDOW)
  3. Validate Content-Type
  4. Parse body
  5. Normalize alert via alert_parser
  6. Deduplication check — suppress repeat alerts within the TTL window
  7. Call AI provider for Insight + Suggested Actions
  8. Dispatch to all configured notification platforms in parallel
  9. Return JSON response

Security notes
==============
WEBHOOK_SECRET
  If set, all POST /webhook requests must include a matching X-Webhook-Token
  header. Uses hmac.compare_digest for timing-safe comparison. Strongly
  recommended for any deployment reachable outside localhost — without it,
  anyone who discovers the endpoint can trigger AI API calls at your expense.

Alert deduplication (token budget protection)
  Identical alerts (same service + status + message) within the TTL window are
  silently acknowledged without calling the AI API. This protects against:
    - A flapping service firing the same alert dozens of times per minute
    - An attacker who discovered the endpoint and is flooding it before
      WEBHOOK_SECRET is set
    - Legitimate monitoring tools that retry on non-200 responses
  The TTL is configurable via DEDUP_TTL_SECONDS (default: 60). Set to 0 to
  disable deduplication entirely.
  Note: the dedup cache is in-memory per gunicorn worker. With 2 workers, a
  burst of 2 identical simultaneous requests could both be processed. This
  is acceptable — the goal is rate reduction, not perfect exactly-once delivery.

Error responses
  Never include internal exception text, file paths, or any value from
  .secrets.env. All 4xx/5xx responses are JSON with a static "error" string.
"""

import hashlib
import hmac
import logging
import os
import time
from collections import deque
from threading import Lock

from flask import Blueprint, jsonify, request

from .alert_parser import NormalizedAlert, parse_alert
from .gemini_client import get_ai_insight
from . import notify
from .utils import _env_int

logger = logging.getLogger(__name__)
webhook_bp = Blueprint("webhook", __name__)


# ---------------------------------------------------------------------------
# Alert deduplication
# ---------------------------------------------------------------------------

_dedup_cache: dict[str, float] = {}
_dedup_lock = Lock()
_DEDUP_MAX_SIZE = 10_000  # max entries; evicts oldest when exceeded

# ---------------------------------------------------------------------------
# Webhook rate limiter
# ---------------------------------------------------------------------------
# Sliding-window counter independent of deduplication.
# WEBHOOK_RATE_LIMIT=0 (default) disables the limiter — suitable for LAN-only
# deployments protected by WEBHOOK_SECRET. Enable for internet-facing setups:
#   WEBHOOK_RATE_LIMIT=60    # allow 60 requests per window
#   WEBHOOK_RATE_WINDOW=60   # 60-second window → 1 req/s average burst-safe

_rate_timestamps: deque[float] = deque()
_rate_lock = Lock()


def _dedup_key(alert: NormalizedAlert) -> str:
    """
    Stable hash of the alert's identity fields.
    Two alerts with the same service, status, and message are considered
    duplicates regardless of source format or extra context fields.
    """
    raw = f"{alert.service_name}:{alert.status}:{alert.message}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _is_duplicate(alert: NormalizedAlert) -> bool:
    """
    Return True if an identical alert was processed within the TTL window.
    Prunes expired entries on each call to prevent unbounded cache growth.
    """
    ttl = _env_int("DEDUP_TTL_SECONDS", 60)
    if ttl <= 0:
        return False

    key = _dedup_key(alert)
    now = time.monotonic()

    with _dedup_lock:
        last_seen = _dedup_cache.get(key)
        if last_seen is not None and (now - last_seen) < ttl:
            return True

        # Delete-then-reinsert maintains insertion-order = time-order.
        # Updating an existing key in-place (dict[key] = val) preserves the
        # original insertion position, which would break the O(k) pruning walk.
        _dedup_cache.pop(key, None)
        _dedup_cache[key] = now

        # Prune expired entries from the front — O(k) where k = expired count.
        # Valid because dict insertion order == time order after delete+reinsert.
        # Stop at the first non-expired entry — everything after it is newer.
        while _dedup_cache:
            oldest_key, oldest_time = next(iter(_dedup_cache.items()))
            if now - oldest_time >= ttl:
                del _dedup_cache[oldest_key]
            else:
                break

        # Hard cap: if still over limit after TTL pruning (unique alert flood),
        # evict the oldest entry. Trades dedup accuracy for bounded memory.
        if len(_dedup_cache) > _DEDUP_MAX_SIZE:
            del _dedup_cache[next(iter(_dedup_cache))]

    return False


def _check_rate_limit() -> bool:
    """
    Return True if the request should be rejected (rate limit exceeded).
    Uses a sliding window counter. Thread-safe.
    Returns False (allow) when WEBHOOK_RATE_LIMIT is 0 or unset.
    """
    limit = _env_int("WEBHOOK_RATE_LIMIT", 0)
    if limit <= 0:
        return False  # limiter disabled
    window = _env_int("WEBHOOK_RATE_WINDOW", 60)
    now = time.monotonic()
    with _rate_lock:
        cutoff = now - window
        while _rate_timestamps and _rate_timestamps[0] < cutoff:
            _rate_timestamps.popleft()
        if len(_rate_timestamps) >= limit:
            return True
        _rate_timestamps.append(now)
        return False


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

def _check_secret() -> bool:
    """
    Return True if the request passes secret validation.
    If WEBHOOK_SECRET is not set, all requests are allowed (open mode).
    Uses hmac.compare_digest to prevent timing attacks.
    """
    secret = os.environ.get("WEBHOOK_SECRET", "")
    if not secret:
        return True
    provided = request.headers.get("X-Webhook-Token", "")
    return hmac.compare_digest(provided, secret)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@webhook_bp.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@webhook_bp.route("/webhook", methods=["POST"])
def webhook():
    # 1. Authenticate
    if not _check_secret():
        return jsonify({"error": "unauthorized"}), 401

    # 2. Rate limit — checked after auth so only authenticated callers consume quota
    if _check_rate_limit():
        logger.warning("Webhook rate limit exceeded (WEBHOOK_RATE_LIMIT=%s)", os.environ.get("WEBHOOK_RATE_LIMIT", "0"))
        return jsonify({"error": "too many requests"}), 429

    # 3. Validate Content-Type
    if not request.is_json:
        return jsonify({"error": "Content-Type must be application/json"}), 415

    # 4. Parse body
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object"}), 400

    # 5. Normalize alert
    try:
        alert = parse_alert(data)
    except Exception:
        logger.error("Alert parsing failed", exc_info=True)
        return jsonify({"error": "alert payload could not be parsed"}), 422

    logger.info(
        "Alert received: source=%s service=%s status=%s",
        alert.source, alert.service_name, alert.status,
    )
    logger.debug(
        "Alert detail: severity=%s message=%r details=%s",
        alert.severity, alert.message, alert.details,
    )

    # 6. Deduplication — suppress repeat alerts within the TTL window to protect
    #    AI API token budget from flapping services and flood attacks.
    if _is_duplicate(alert):
        logger.info(
            "Duplicate alert suppressed: service=%s status=%s",
            alert.service_name, alert.status,
        )
        return jsonify({"status": "deduplicated"}), 200

    # 7. AI analysis
    ai = get_ai_insight(alert)
    logger.info("AI insight generated for %s", alert.service_name)
    logger.debug("AI response: insight=%r actions=%s", ai.get("insight", "")[:200], ai.get("suggested_actions"))

    # 8. Dispatch to all configured platforms
    notification_errors = notify.dispatch(alert, ai)

    response: dict = {
        "status": "processed",
        "alert": {
            "source": alert.source,
            "service": alert.service_name,
            "alert_status": alert.status,
            "severity": alert.severity,
        },
        "ai_insight": ai.get("insight"),
        "suggested_actions": ai.get("suggested_actions", []),
    }
    logger.debug("Dispatch complete: errors=%s", notification_errors or "none")
    if notification_errors:
        response["notification_errors"] = notification_errors

    return jsonify(response), 200
