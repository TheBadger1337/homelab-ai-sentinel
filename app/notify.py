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


# Required env vars per platform — if any are missing, the platform is unconfigured.
_REQUIRED_VARS: dict[str, list[str]] = {
    "discord":  ["DISCORD_WEBHOOK_URL"],
    "slack":    ["SLACK_WEBHOOK_URL"],
    "telegram": ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"],
    "ntfy":     ["NTFY_URL"],
    "email":    ["SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"],
    "whatsapp": ["WHATSAPP_TOKEN", "WHATSAPP_PHONE_ID", "WHATSAPP_TO"],
    "signal":   ["SIGNAL_API_URL", "SIGNAL_SENDER", "SIGNAL_RECIPIENT"],
    "gotify":   ["GOTIFY_URL", "GOTIFY_APP_TOKEN"],
    "matrix":   ["MATRIX_HOMESERVER", "MATRIX_ACCESS_TOKEN", "MATRIX_ROOM_ID"],
    "imessage": ["IMESSAGE_URL", "IMESSAGE_PASSWORD", "IMESSAGE_TO"],
}


def _is_configured(client: types.ModuleType) -> bool:
    """Return True if the platform's required env vars are all set."""
    name = client.__name__.rsplit(".", 1)[-1].replace("_client", "")
    required = _REQUIRED_VARS.get(name, [])
    return all(os.environ.get(v) for v in required)


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


_SKIPPED = "__skipped__"


def _call_client(
    client: types.ModuleType,
    alert: NormalizedAlert,
    ai: dict[str, Any],
) -> str | None:
    """
    Call a single notification client. Returns an error string on failure,
    _SKIPPED if the client was disabled/unconfigured, or None on success.
    Never raises — all exceptions are caught here so the thread pool cannot
    surface unexpected exceptions to the caller.
    """
    name = client.__name__.rsplit(".", 1)[-1].replace("_client", "")
    if _is_disabled(client):
        logger.debug("Skipping %s notifier (disabled via env var)", name)
        return _SKIPPED
    if not _is_configured(client):
        logger.debug("Skipping %s notifier (not configured)", name)
        return _SKIPPED
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


class DispatchResult:
    """Result of a dispatch operation."""
    __slots__ = ("errors", "attempted", "succeeded")

    def __init__(self, errors: list[str], attempted: int, succeeded: int):
        self.errors = errors
        self.attempted = attempted
        self.succeeded = succeeded

    @property
    def all_failed(self) -> bool:
        """True if at least one platform was attempted and none succeeded."""
        return self.attempted > 0 and self.succeeded == 0


_DISPATCH_TIMEOUT = 30  # seconds — max time to wait for all platforms


def dispatch(alert: NormalizedAlert, ai: dict[str, Any]) -> DispatchResult:
    """
    Call every configured notification client in parallel.

    Returns a DispatchResult with error strings, attempted count, and success
    count. Never raises. Individual platforms that exceed the 30-second timeout
    are logged as timed out — prevents a single hanging SMTP connection from
    blocking the entire dispatch indefinitely.
    """
    errors: list[str] = []
    attempted = 0
    succeeded = 0
    with ThreadPoolExecutor(max_workers=len(_CLIENTS)) as pool:
        futures = {pool.submit(_call_client, client, alert, ai): client for client in _CLIENTS}
        try:
            for future in as_completed(futures, timeout=_DISPATCH_TIMEOUT):
                result = future.result()
                if result == _SKIPPED:
                    continue
                attempted += 1
                if result is not None:
                    errors.append(result)
                else:
                    succeeded += 1
        except TimeoutError:
            # Some platforms didn't finish in time — log which ones timed out
            for future, client in futures.items():
                if not future.done():
                    name = client.__name__.split(".")[-1].replace("_client", "")
                    errors.append(f"{name}: timed out after {_DISPATCH_TIMEOUT}s")
                    attempted += 1
                    logger.warning("Dispatch timeout: %s did not complete in %ds", name, _DISPATCH_TIMEOUT)
                    future.cancel()
    return DispatchResult(errors, attempted, succeeded)
