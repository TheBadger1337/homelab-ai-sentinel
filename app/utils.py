"""
Shared utility helpers for Sentinel app modules.
"""

import logging
import os
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def _env_int(key: str, default: int) -> int:
    """Read an integer env var. Returns default and logs a warning on invalid values."""
    try:
        return int(os.environ.get(key, str(default)))
    except ValueError:
        logger.warning("Invalid value for %s env var — using default %d", key, default)
        return default


def _env_float(key: str, default: float) -> float:
    """Read a float env var. Returns default and logs a warning on invalid values."""
    try:
        return float(os.environ.get(key, str(default)))
    except ValueError:
        logger.warning("Invalid value for %s env var — using default %g", key, default)
        return default


def _validate_url(url: str, env_var: str) -> bool:
    """
    Return True if the URL uses http or https.
    Rejects other schemes (file://, ftp://, etc.) to prevent SSRF via
    misconfigured or attacker-controlled URL env vars.
    Logs a warning and returns False on rejection.
    """
    scheme = urlparse(url).scheme
    if scheme not in ("http", "https"):
        logger.warning(
            "%s: URL scheme must be http or https (got %r) — skipping notification",
            env_var, scheme,
        )
        return False
    return True
