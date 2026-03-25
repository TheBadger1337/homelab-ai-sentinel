"""
Notification dispatcher.

Runs every configured client concurrently using a thread pool so that a slow
or unresponsive platform (e.g. a 10s timeout on WhatsApp) does not delay
delivery to all other platforms.

Worst-case wall-clock time:
  Before (sequential): Gemini 30s + 10 clients × 10s = ~130s
  After  (parallel):   Gemini 30s + max(single client timeout) = ~45s

Each client self-selects based on its own env vars — no env var means it
silently skips. Errors are isolated per-platform and returned as a list;
one failing target never blocks the others.

Secret-safe logging:
  requests.HTTPError.__str__() embeds the full request URL, which for some
  clients (e.g. Telegram) contains the API token in the path. We log only
  the exception type and HTTP status code — never the raw exception string.
"""

import logging
import os
import types
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests

from .alert_parser import NormalizedAlert
from . import (
    discord_client,
    slack_client,
    telegram_client,
    ntfy_client,
    email_client,
    whatsapp_client,
    signal_client,
    gotify_client,
    matrix_client,
    imessage_client,
)

logger = logging.getLogger(__name__)

_CLIENTS: list[types.ModuleType] = [
    discord_client,
    slack_client,
    telegram_client,
    ntfy_client,
    email_client,
    whatsapp_client,
    signal_client,
    gotify_client,
    matrix_client,
    imessage_client,
]


def _is_disabled(client: types.ModuleType) -> bool:
    """
    Return True if the platform has been disabled via {NAME}_DISABLED=true.

    Examples: DISCORD_DISABLED=true, TELEGRAM_DISABLED=true, MATRIX_DISABLED=true
    This is the single place where all platform disable flags are checked —
    clients themselves do not need to check their own disable flag.
    """
    name = client.__name__.rsplit(".", 1)[-1].replace("_client", "").upper()
    return os.environ.get(f"{name}_DISABLED", "").lower() == "true"


def _safe_exc_log(exc: requests.RequestException) -> str:
    """
    Return a log-safe description of a requests exception.
    Never includes the URL (which may contain API tokens) or auth headers.
    """
    status = None
    if hasattr(exc, "response") and exc.response is not None:
        status = exc.response.status_code
    return f"{type(exc).__name__} (HTTP {status})" if status else type(exc).__name__


def _call_client(
    client: types.ModuleType,
    alert: NormalizedAlert,
    ai: dict[str, Any],
) -> str | None:
    """
    Call a single notification client. Returns an error string on failure,
    None on success. Never raises — all exceptions are caught here so the
    thread pool cannot surface unexpected exceptions to the caller.
    """
    name = client.__name__.rsplit(".", 1)[-1].replace("_client", "")
    if _is_disabled(client):
        logger.debug("Skipping %s notifier (disabled via env var)", name)
        return None
    try:
        client.post_alert(alert, ai)
        logger.debug("%s notifier succeeded", name)
        return None
    except requests.RequestException as exc:
        logger.warning("%s delivery failed: %s", name, _safe_exc_log(exc))
        return f"{name} delivery failed"
    except Exception:  # noqa: BLE001 — must never propagate
        logger.exception("Unexpected error in %s notifier", name)
        return f"{name} error"


def dispatch(alert: NormalizedAlert, ai: dict[str, Any]) -> list[str]:
    """
    Call every configured notification client in parallel.

    Returns a (possibly empty) list of error strings — one entry per failed
    platform. Never raises.
    """
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=len(_CLIENTS)) as pool:
        futures = {pool.submit(_call_client, client, alert, ai): client for client in _CLIENTS}
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                errors.append(result)
    return errors
