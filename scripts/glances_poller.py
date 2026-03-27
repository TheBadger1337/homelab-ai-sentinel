#!/usr/bin/env python3
"""
Glances → Sentinel alert poller.

Glances does not push webhooks — this script polls the Glances REST API on a
configurable interval and POSTs to Sentinel whenever active alerts are found.
It tracks which alerts have already been forwarded and only re-sends when a
previously-resolved alert fires again.

Usage:
    python3 scripts/glances_poller.py

Environment variables:
    GLANCES_URL          Base URL of the Glances API, e.g. http://10.0.0.10:61208
    SENTINEL_URL         Sentinel webhook URL, e.g. http://localhost:5000/webhook
    SENTINEL_SECRET      Optional: WEBHOOK_SECRET value (sets X-Webhook-Token header)
    GLANCES_HOST_LABEL   Human-readable hostname label (defaults to host in GLANCES_URL)
    POLL_INTERVAL        Seconds between polls (default: 30)

Docker example:
    docker run -d \\
      --name glances-poller \\
      -e GLANCES_URL=http://10.0.0.10:61208 \\
      -e SENTINEL_URL=http://sentinel:5000/webhook \\
      -e POLL_INTERVAL=30 \\
      python:3.11-slim python3 /app/glances_poller.py

Glances API docs: https://glances.readthedocs.io/en/latest/api.html
"""

import hashlib
import logging
import os
import time

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("glances_poller")

# Tracks alerts already forwarded. Cleared when the alert resolves (end != -1).
_sent_alerts: set[str] = set()


def _alert_key(alert: dict) -> str:
    """Stable identifier for a specific alert occurrence."""
    raw = f"{alert.get('type')}:{alert.get('state')}:{alert.get('begin')}"
    return hashlib.sha256(raw.encode()).hexdigest()


def poll_and_forward(
    glances_url: str,
    sentinel_url: str,
    secret: str,
    hostname: str,
) -> None:
    """Fetch current Glances alerts and POST any new ones to Sentinel."""
    try:
        resp = requests.get(
            f"{glances_url.rstrip('/')}/api/4/alert",
            timeout=10,
        )
        resp.raise_for_status()
        all_alerts = resp.json()
    except requests.RequestException as exc:
        logger.warning("Failed to poll Glances at %s: %s", glances_url, exc)
        return
    except ValueError:
        logger.warning("Glances returned non-JSON response")
        return

    # Only process active alerts (end == -1 means still firing)
    active = [a for a in all_alerts if isinstance(a, dict) and a.get("end", -1) == -1]
    active_keys = {_alert_key(a) for a in active}

    for alert in active:
        key = _alert_key(alert)
        if key in _sent_alerts:
            continue  # already forwarded this occurrence

        begin = alert.get("begin")
        duration = int(time.time() - begin) if begin else None

        payload = {
            "glances_host":     hostname,
            "glances_type":     alert.get("type", "unknown"),
            "glances_state":    alert.get("state", "UNKNOWN"),
            "glances_value":    alert.get("avg"),
            "glances_min":      alert.get("min"),
            "glances_max":      alert.get("max"),
            "glances_duration": duration,
            "glances_top":      alert.get("top", [])[:5],
        }
        # Strip None values to keep the payload clean
        payload = {k: v for k, v in payload.items() if v is not None}

        headers: dict[str, str] = {}
        if secret:
            headers["X-Webhook-Token"] = secret

        try:
            r = requests.post(sentinel_url, json=payload, headers=headers, timeout=10)
            r.raise_for_status()
            _sent_alerts.add(key)
            logger.info(
                "Forwarded %s alert from %s (state=%s value=%s)",
                alert.get("type"), hostname, alert.get("state"), alert.get("avg"),
            )
        except requests.RequestException as exc:
            logger.warning("Failed to POST to Sentinel: %s", exc)

    # Remove resolved alerts from the sent set so they can fire again if they recur
    resolved = _sent_alerts - active_keys
    _sent_alerts.difference_update(resolved)
    if resolved:
        logger.debug("Cleared %d resolved alert(s) from tracking set", len(resolved))


def main() -> None:
    glances_url = os.environ.get("GLANCES_URL", "http://localhost:61208")
    sentinel_url = os.environ.get("SENTINEL_URL", "http://localhost:5000/webhook")
    secret = os.environ.get("SENTINEL_SECRET", "")
    interval = int(os.environ.get("POLL_INTERVAL", "30"))

    # Derive a friendly hostname label from the URL if not explicitly set
    default_host = glances_url.split("//", 1)[-1].split(":")[0]
    hostname = os.environ.get("GLANCES_HOST_LABEL", default_host)

    logger.info(
        "Glances poller started: %s → %s every %ds (host label: %s)",
        glances_url, sentinel_url, interval, hostname,
    )

    while True:
        poll_and_forward(glances_url, sentinel_url, secret, hostname)
        time.sleep(interval)


if __name__ == "__main__":
    main()
