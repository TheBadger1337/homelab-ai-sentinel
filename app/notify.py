"""
Notification dispatcher.

Iterates every configured client and calls post_alert().
Each client self-selects based on its own env vars — no env var means it
silently skips. Errors are isolated per-platform and returned as a list;
one failing target never blocks the others.
"""

import logging
import types
from typing import Any

import requests

from .alert_parser import NormalizedAlert
from . import discord_client, slack_client, telegram_client, ntfy_client, email_client

logger = logging.getLogger(__name__)

_CLIENTS: list[types.ModuleType] = [discord_client, slack_client, telegram_client, ntfy_client, email_client]


def dispatch(alert: NormalizedAlert, ai: dict[str, Any]) -> list[str]:
    """
    Call every configured notification client.

    Returns a (possibly empty) list of error strings — one entry per failed
    platform. Never raises.
    """
    errors: list[str] = []
    for client in _CLIENTS:
        name = client.__name__.rsplit(".", 1)[-1].replace("_client", "")
        try:
            client.post_alert(alert, ai)
        except requests.RequestException as exc:
            # Log full detail (including URLs) server-side only
            logger.warning("%s delivery failed: %s", name, exc)
            errors.append(f"{name} delivery failed")
        except Exception as exc:  # noqa: BLE001 — must never propagate
            logger.exception("Unexpected error in %s notifier: %s", name, exc)
            errors.append(f"{name} error")
    return errors
