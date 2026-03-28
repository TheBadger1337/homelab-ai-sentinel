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
    Return True if the URL is safe to use as an outbound HTTP target.

    Rejects:
    - Non-http/https schemes (file://, ftp://, etc.) — SSRF via scheme abuse
    - Loopback addresses (127.x.x.x, localhost, ::1) — avoids proxying to
      the container itself or the Docker host loopback interface
    - Link-local / cloud metadata addresses (169.254.x.x) — blocks AWS/GCP/Azure
      instance metadata endpoint probing

    RFC1918 ranges (192.168.x.x, 10.x.x.x, 172.16-31.x.x) are intentionally
    allowed — all Sentinel notification backends run on the internal network.
    """
    parsed = urlparse(url)
    scheme = parsed.scheme
    if scheme not in ("http", "https"):
        logger.warning(
            "%s: URL scheme must be http or https (got %r) — skipping notification",
            env_var, scheme,
        )
        return False

    hostname = (parsed.hostname or "").lower()
    if (
        hostname in ("localhost", "127.0.0.1", "::1", "0.0.0.0")
        or hostname.startswith("127.")
        or hostname.startswith("169.254.")
    ):
        logger.warning(
            "%s: URL targets a restricted address (%r) — skipping notification",
            env_var, hostname,
        )
        return False

    return True
