"""
Shared utility helpers for Sentinel app modules.
"""

import logging
import os

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
