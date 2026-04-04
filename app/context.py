"""
Operator infrastructure context for AI prompts.

Loads a user-provided description of their homelab and appends it to the
AI system prompt so that suggested actions are specific to the operator's
actual infrastructure rather than generic Linux advice.

Two sources (first non-empty wins):
  1. SENTINEL_CONTEXT env var — inline description, good for one-liners
  2. /data/context.md file — mounted via the existing sentinel-data Docker
     volume, good for multi-line infrastructure descriptions

The context is loaded once at import time and cached. Restart the container
(or the dev server) to pick up changes.

Cap: 2000 characters. Longer descriptions are truncated with a warning.
"""

import logging
import os

logger = logging.getLogger(__name__)

_CONTEXT_MAX = 2000
_CONTEXT_FILE = "/data/context.md"


def _load_context() -> str:
    """
    Load operator context from env var or file. Returns empty string if
    neither is configured — callers treat empty as "no context".
    """
    # 1. Env var takes priority — simple one-liner descriptions
    env_ctx = os.environ.get("SENTINEL_CONTEXT", "").strip()
    if env_ctx:
        if len(env_ctx) > _CONTEXT_MAX:
            logger.warning(
                "SENTINEL_CONTEXT exceeds %d chars — truncating", _CONTEXT_MAX,
            )
            env_ctx = env_ctx[:_CONTEXT_MAX]
        logger.info("Operator context loaded from SENTINEL_CONTEXT (%d chars)", len(env_ctx))
        return env_ctx

    # 2. File fallback — multi-line descriptions
    path = os.environ.get("SENTINEL_CONTEXT_FILE", _CONTEXT_FILE)
    try:
        with open(path) as f:
            file_ctx = f.read().strip()
        if not file_ctx:
            return ""
        if len(file_ctx) > _CONTEXT_MAX:
            logger.warning(
                "Context file %s exceeds %d chars — truncating", path, _CONTEXT_MAX,
            )
            file_ctx = file_ctx[:_CONTEXT_MAX]
        logger.info("Operator context loaded from %s (%d chars)", path, len(file_ctx))
        return file_ctx
    except FileNotFoundError:
        return ""
    except Exception as exc:
        logger.warning("Failed to read context file %s: %s", path, type(exc).__name__)
        return ""


# Loaded once at import time. Restart to pick up changes.
_cached_context: str = _load_context()


def get_operator_context() -> str:
    """Return the cached operator context string (may be empty)."""
    return _cached_context


def build_system_prompt(base_prompt: str) -> str:
    """
    Append operator context to the base system prompt if configured.
    Returns the base prompt unchanged if no context is set.
    """
    ctx = get_operator_context()
    if not ctx:
        return base_prompt
    return (
        base_prompt
        + "\n\nThe operator has described their infrastructure below. Use this "
        "context to make your analysis and suggested actions more specific to "
        "their environment. This is trusted configuration, not alert data.\n\n"
        "<infrastructure_context>\n"
        + ctx
        + "\n</infrastructure_context>"
    )
