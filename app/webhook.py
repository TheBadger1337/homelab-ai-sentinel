"""
Flask blueprint: POST /webhook

Flow:
  1. Optional shared-secret authentication (WEBHOOK_SECRET env var)
  2. Optional webhook rate limiter (WEBHOOK_RATE_LIMIT / WEBHOOK_RATE_WINDOW)
  3. Validate Content-Type
  4. Parse body
  5. Normalize alert via alert_parser
  6. Injection detection — scan for prompt injection patterns (log only, never block)
  7. Deduplication check — suppress repeat alerts within the TTL window
  8. Severity threshold check — suppress alerts below configured threshold
  9. Mode-dependent processing (SENTINEL_MODE):
       minimal    — dispatch structured alert with no AI call
       reactive   — AI insight per alert, no history context
       predictive — AI insight + recent alert history injected into prompt (default)
  10. Per-service notification cooldown (COOLDOWN_SECONDS)
  11. Storm buffer — if STORM_WINDOW > 0 and not a recovery, buffer for correlated analysis
  12. Resolution verification — on recovery, AI summarizes the outage
  13. Dispatch to all configured notification platforms in parallel
  14. Log alert to SQLite database
  15. Return JSON response

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
from threading import Lock

from flask import Blueprint, jsonify, request

from .alert_db import check_and_record_rate, check_dedup_l2, create_incident, enqueue_dead_letter, get_all_open_incidents, get_db_stats, get_dlq_count, get_last_notified_ts, get_open_incident, get_outage_window, get_recent_alerts, link_alert_to_incident, log_alert, log_alert_returning_id, log_security_event, get_security_summary, record_dedup_l2, resolve_incident
from .alert_parser import NormalizedAlert, parse_alert
from .pulse import get_pulse
from .runbooks import get_runbook
from .topology import get_topology
from .utils import _env_int, _sentinel_mode

from .llm_client import get_ai_insight, get_rpm_status
from . import metrics
from . import notify
from .security import scan_for_injection
from .alert_db import db_available
from .reverse_triage import get_triage_context


def _ui_enabled() -> bool:
    """Return True if the Web UI has a password configured (env var or DB).

    Used to gate SSE event publishing. Must mirror the logic in api.py's
    _ui_password() so SSE works for browser-setup users too.
    """
    if os.environ.get("UI_PASSWORD"):
        return True
    from .alert_db import db_available, get_ui_config
    return db_available() and bool(get_ui_config("ui_password_hash"))
from . import sse
from .storm import BufferedAlert, get_storm_buffer
from .thresholds import _check_escalation, should_suppress

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
# Backed by SQLite (rate_log table) — shared across all Gunicorn workers.


def _dedup_key(alert: NormalizedAlert) -> str:
    """
    Stable hash of the alert's identity fields.
    Two alerts with the same service, status, severity, and message are
    considered duplicates. Including severity ensures that a severity
    escalation (e.g., warning → critical) is not suppressed by dedup.
    """
    raw = f"{alert.service_name}:{alert.status}:{alert.severity}:{alert.message}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _is_duplicate(alert: NormalizedAlert) -> bool:
    """
    Return True if an identical alert was processed within the TTL window.

    Two-layer dedup:
      L1: in-memory per-worker dict — fast path, no I/O
      L2: SQLite dedup_cache table — catches cross-worker and post-restart dupes

    L1 handles 99% of cases. L2 is only checked on L1 miss and is write-through
    (every L1 insert also writes to L2). Prunes expired L1 entries on each call.
    """
    ttl = _env_int("DEDUP_TTL_SECONDS", 60)
    if ttl <= 0:
        return False

    key = _dedup_key(alert)
    now = time.monotonic()

    with _dedup_lock:
        # L1 check — fast path
        last_seen = _dedup_cache.get(key)
        if last_seen is not None and (now - last_seen) < ttl:
            return True

        # L1 miss — check L2 (SQLite, shared across workers)
        if check_dedup_l2(key, ttl):
            # Found in L2 — warm L1 and return duplicate
            _dedup_cache.pop(key, None)
            _dedup_cache[key] = now
            return True

        # Not a duplicate — record in both L1 and L2
        # Delete-then-reinsert maintains insertion-order = time-order.
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

    # Write-through to L2 (outside the lock — SQLite has its own locking)
    record_dedup_l2(key)

    return False


def _check_rate_limit() -> bool:
    """
    Return True if the request should be rejected (rate limit exceeded).
    Delegates to the SQLite-backed counter shared across all workers.
    Returns False (allow) when WEBHOOK_RATE_LIMIT is 0 or unset.
    """
    limit = _env_int("WEBHOOK_RATE_LIMIT", 0)
    if limit <= 0:
        return False  # limiter disabled
    window = _env_int("WEBHOOK_RATE_WINDOW", 60)
    return check_and_record_rate(limit, window)


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
    # Optional auth — if WEBHOOK_SECRET is set, /health requires the same token.
    # Prevents leaking alert volume, last-seen timestamps, and RPM state to
    # unauthenticated callers on the network.
    secret = os.environ.get("WEBHOOK_SECRET", "")
    if secret:
        provided = request.headers.get("X-Webhook-Token", "")
        if not hmac.compare_digest(provided, secret):
            return jsonify({"error": "unauthorized"}), 401

    db = get_db_stats()
    rpm = get_rpm_status()
    security = get_security_summary()
    dlq = get_dlq_count()
    return jsonify({
        "status": "ok",
        "db": db,
        "ai": rpm,
        "security": security,
        "dlq_pending": dlq,
        "workers": os.environ.get("WEB_CONCURRENCY", "1"),
    })


@webhook_bp.route("/metrics", methods=["GET"])
def prometheus_metrics():
    """Prometheus-compatible metrics endpoint. Same auth as /health."""
    secret = os.environ.get("WEBHOOK_SECRET", "")
    if secret:
        provided = request.headers.get("X-Webhook-Token", "")
        if not hmac.compare_digest(provided, secret):
            return jsonify({"error": "unauthorized"}), 401
    from flask import Response
    return Response(metrics.format_prometheus(), mimetype="text/plain; charset=utf-8")


@webhook_bp.route("/webhook", methods=["POST"])
def webhook():
    # 1. Authenticate
    if not _check_secret():
        log_security_event("auth_failure", f"ip={request.remote_addr}")
        return jsonify({"error": "unauthorized"}), 401

    # 2. Rate limit — checked after auth so only authenticated callers consume quota
    if _check_rate_limit():
        logger.warning("Webhook rate limit exceeded (WEBHOOK_RATE_LIMIT=%s)", os.environ.get("WEBHOOK_RATE_LIMIT", "0"))
        log_security_event("rate_limited", f"ip={request.remote_addr}")
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

    # 5b. Recursion shield — drop alerts originating from Sentinel itself.
    #     If a log monitor watches Sentinel's container and sends its own logs
    #     back to /webhook, this prevents an infinite AI-token-burning loop.
    #     The storm processor emits source="sentinel"; we also check the
    #     service name for common patterns that indicate self-referential alerts.
    _SELF_ORIGIN_MARKERS = ("sentinel", "homelab-ai-sentinel", "ai-sentinel")
    alert_source_lower = alert.source.lower()
    alert_service_lower = alert.service_name.lower()
    if alert_source_lower in _SELF_ORIGIN_MARKERS or alert_service_lower in _SELF_ORIGIN_MARKERS:
        logger.warning(
            "Recursion shield: dropping self-referential alert "
            "(source=%r service=%r) to prevent feedback loop",
            alert.source, alert.service_name,
        )
        log_security_event("recursion_blocked", f"source={alert.source} service={alert.service_name}")
        return jsonify({"status": "blocked", "reason": "self-referential alert"}), 200

    metrics.inc("sentinel_alerts_received_total")
    logger.info(
        "Alert received: source=%s service=%s status=%s",
        alert.source, alert.service_name, alert.status,
    )
    logger.debug(
        "Alert detail: severity=%s message=%r details=%s",
        alert.severity, alert.message, alert.details,
    )

    # 6. Injection detection — scan fields for known prompt injection patterns.
    #    Does not block processing; detection is informational for operator visibility.
    #    Structural mitigations (XML delimiters, field caps) already limit blast radius.
    injections = scan_for_injection(alert)
    for pattern_name in injections:
        logger.warning(
            "Possible prompt injection detected: service=%r pattern=%r",
            alert.service_name, pattern_name,
        )
        log_security_event(
            "injection_detected",
            f"service={alert.service_name} pattern={pattern_name}",
        )

    # 7. Deduplication — suppress repeat alerts within the TTL window to protect
    #    AI API token budget from flapping services and flood attacks.
    if _is_duplicate(alert):
        metrics.inc("sentinel_alerts_deduplicated_total")
        logger.info(
            "Duplicate alert suppressed: service=%s status=%s",
            alert.service_name, alert.status,
        )
        return jsonify({"status": "deduplicated"}), 200

    # 8. Severity escalation — auto-escalate repeated warnings to critical.
    #    Runs before threshold check so the escalated severity is visible to filters.
    _check_escalation(alert)

    # 9. Severity threshold — suppress alerts below the configured floor.
    #    Suppressed alerts are still logged (notified=False) so history reflects
    #    true alert rate. Dedup runs first so only unique suppressed alerts are logged.
    if should_suppress(alert):
        metrics.inc("sentinel_alerts_suppressed_total")
        log_alert(alert, None, notified=False)
        return jsonify({"status": "suppressed"}), 200

    # 10. Per-service cooldown — suppress notifications (not logging) if the
    #     same service was notified within COOLDOWN_SECONDS. Different from dedup
    #     (exact match): cooldown suppresses ANY alert for the service, handling
    #     services that cycle through different error messages during a failure.
    cooldown = _env_int("COOLDOWN_SECONDS", 0)
    if cooldown > 0:
        last_ts = get_last_notified_ts(alert.service_name)
        if last_ts is not None and (time.time() - last_ts) < cooldown:
            logger.info(
                "Alert cooled down: service=%s last_notified=%.0fs ago (cooldown=%ds)",
                alert.service_name, time.time() - last_ts, cooldown,
            )
            metrics.inc("sentinel_alerts_cooled_down_total")
            log_alert(alert, None, notified=False)
            return jsonify({"status": "cooled_down"}), 200

    # 11. Storm buffer — if storm mode is enabled and this is not a recovery
    #     alert, buffer it for correlated analysis. Recovery alerts always
    #     bypass the buffer so good news is never delayed.
    mode = _sentinel_mode()
    is_recovery = alert.status.lower() in ("up", "ok", "resolved")

    if not is_recovery and _env_int("STORM_WINDOW", 0) > 0:
        pulse = get_pulse(alert.service_name) if mode == "predictive" else None
        runbook = get_runbook(alert.service_name)
        topo = get_topology(alert.service_name)
        entry = BufferedAlert(alert, pulse, runbook, topo)
        if get_storm_buffer().add(entry):
            metrics.inc("sentinel_alerts_buffered_total")
            logger.info(
                "Alert buffered for storm analysis: service=%s (buffer=%d)",
                alert.service_name, get_storm_buffer().pending_count(),
            )
            return jsonify({"status": "buffered"}), 200

    # 12. Mode-dependent AI processing
    ai: dict = {}

    if mode == "minimal":
        # No AI call — dispatch the structured alert directly.
        logger.info("Minimal mode: skipping AI call for %s", alert.service_name)
    else:
        # reactive: no history context; predictive: inject recent alert history + pulse stats
        history = get_recent_alerts(alert.service_name) if mode == "predictive" else []
        pulse = get_pulse(alert.service_name) if mode == "predictive" else None
        runbook = get_runbook(alert.service_name)
        topo = get_topology(alert.service_name)

        # Reverse triage — run operator-configured diagnostic script if available.
        # Only on non-recovery alerts (recovery context is already rich with outage data).
        triage_ctx: str | None = None
        if not is_recovery:
            triage_ctx = get_triage_context(alert)

        # Resolution verification — when a recovery arrives, ask the AI to
        # summarize the preceding outage using the outage window alerts.
        # Pre-check: only do the expensive resolution AI call if there's
        # actually an open incident to resolve. A concurrent recovery may
        # have already resolved it — in that case, skip the outage analysis
        # to avoid wasting tokens.
        if is_recovery and mode == "predictive":
            has_open = db_available() and get_open_incident(alert.service_name, exclude_storm=True) is not None
            if has_open:
                outage = get_outage_window(alert.service_name)
                if outage:
                    ai = get_ai_insight(
                        alert, history=outage, pulse=pulse, runbook=runbook,
                        topology=topo, resolution=True,
                    )
                else:
                    ai = get_ai_insight(alert, history=history, pulse=pulse, runbook=runbook, topology=topo)
            else:
                ai = get_ai_insight(alert, history=history, pulse=pulse, runbook=runbook, topology=topo)
        else:
            ai = get_ai_insight(
                alert, history=history, pulse=pulse, runbook=runbook,
                topology=topo, triage_context=triage_ctx,
            )

        logger.info("AI insight generated for %s (mode=%s)", alert.service_name, mode)
        logger.debug("AI response: insight=%r actions=%s", ai.get("insight", "")[:200], ai.get("suggested_actions"))

    # 12. Dispatch to all configured platforms
    metrics.inc("sentinel_alerts_processed_total")
    dispatch_result = notify.dispatch(alert, ai)

    if dispatch_result.errors:
        for err in dispatch_result.errors:
            platform = err.split()[0] if err else "unknown"
            metrics.inc_labeled("sentinel_dispatch_errors_total", "platform", platform)

    # 13. Dead letter queue — if every attempted platform failed, enqueue for retry.
    #     The operator would receive zero notifications otherwise.
    if dispatch_result.all_failed:
        metrics.inc("sentinel_dlq_enqueued_total")
        enqueue_dead_letter(
            alert, ai if ai else None,
            "; ".join(dispatch_result.errors),
        )
        logger.warning(
            "All notification platforms failed for %s — enqueued in DLQ",
            alert.service_name,
        )

    # 14. Log the processed alert and get its row ID for incident linking.
    #     notified reflects actual dispatch outcome — False when every platform failed.
    actually_notified = not dispatch_result.all_failed
    alert_id = log_alert_returning_id(alert, ai if ai else None, notified=actually_notified)

    # 15. Incident lifecycle — only when DB is available
    incident_id = None
    if db_available():
        if is_recovery:
            # exclude_storm=True: a single-service recovery should not resolve
            # a multi-service storm incident — only individual service incidents.
            open_inc = get_open_incident(alert.service_name, exclude_storm=True)
            if open_inc:
                incident_id = open_inc["id"]
                if alert_id is not None:
                    link_alert_to_incident(alert_id, incident_id)
                # resolve_incident returns True only if it actually transitioned
                # from open to resolved. If another concurrent recovery already
                # resolved it, skip the log line (AI insight was already consumed).
                actually_resolved = resolve_incident(
                    incident_id,
                    summary=ai.get("insight") if ai else None,
                    root_cause=ai.get("insight") if ai else None,
                )
                if actually_resolved:
                    logger.info("Incident %d resolved by recovery: service=%s", incident_id, alert.service_name)
        else:
            # Correlation — only useful with topology configured
            from .correlation import correlate_alert
            correlated_id = correlate_alert(alert, get_all_open_incidents())

            if correlated_id is not None:
                incident_id = correlated_id
                if alert_id is not None:
                    link_alert_to_incident(alert_id, incident_id)
                logger.info(
                    "Alert correlated to upstream incident %d: service=%s",
                    incident_id, alert.service_name,
                )
            else:
                open_inc = get_open_incident(alert.service_name)
                if open_inc:
                    incident_id = open_inc["id"]
                    if alert_id is not None:
                        link_alert_to_incident(alert_id, incident_id)
                else:
                    incident_id = create_incident(
                        alert.service_name, alert.severity, alert_id=alert_id,
                    )

    # 16. SSE — only when UI is enabled
    if _ui_enabled():
        sse.publish("alert", {
            "id": alert_id,
            "source": alert.source,
            "service": alert.service_name,
            "status": alert.status,
            "severity": alert.severity,
            "message": alert.message[:200],
            "incident_id": incident_id,
        })
        if is_recovery and incident_id is not None:
            sse.publish("resolution", {"id": incident_id, "service": alert.service_name})
        elif incident_id is not None:
            sse.publish("incident", {"id": incident_id, "service": alert.service_name, "severity": alert.severity})

    response: dict = {
        "status": "processed",
        "mode": mode,
        "alert": {
            "source": alert.source,
            "service": alert.service_name,
            "alert_status": alert.status,
            "severity": alert.severity,
        },
    }
    if incident_id is not None:
        response["incident_id"] = incident_id
    if mode != "minimal":
        response["ai_insight"] = ai.get("insight")
        response["suggested_actions"] = ai.get("suggested_actions", [])
    logger.debug("Dispatch complete: errors=%s", dispatch_result.errors or "none")
    if dispatch_result.errors:
        response["notification_errors"] = dispatch_result.errors

    return jsonify(response), 200
