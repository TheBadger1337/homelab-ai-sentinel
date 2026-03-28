"""
Shared utility helpers for Sentinel app modules.
"""

import ipaddress
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
    - The literal hostname "localhost"
    - Any IP address that resolves as loopback, link-local, or unspecified,
      regardless of encoding (dotted-decimal, full IPv6, IPv4-mapped IPv6,
      IPv6 link-local fe80::/10, etc.)

    Specifically blocked IP ranges:
      Loopback:    127.x.x.x, ::1, ::ffff:127.x.x.x
      Link-local:  169.254.x.x (cloud metadata), fe80::/10 (IPv6 link-local)
      Unspecified: 0.0.0.0, ::

    RFC1918 ranges (192.168.x.x, 10.x.x.x, 172.16-31.x.x) are intentionally
    allowed — all Sentinel notification backends run on the internal network.

    DNS-based rebinding (a hostname resolving to a blocked IP) is not detected
    here — validation occurs without a network lookup. This is an accepted
    limitation for a homelab operator-controlled configuration.
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
    if not hostname:
        logger.warning(
            "%s: URL has no hostname — skipping notification", env_var,
        )
        return False

    # Reject the literal string "localhost" (not parseable as an IP address)
    if hostname == "localhost":
        logger.warning(
            "%s: URL targets a restricted address (%r) — skipping notification",
            env_var, hostname,
        )
        return False

    # Use ipaddress to validate all IP-literal hostnames regardless of encoding.
    # This catches:
    #   - All 127.x.x.x loopback variants (not just 127.0.0.1)
    #   - ::1 and full-form IPv6 loopback equivalents
    #   - ::ffff:127.0.0.1 (IPv4-mapped loopback) — Python does not mark this
    #     is_loopback, so we unwrap IPv4-mapped addresses and check the inner IP
    #   - 169.254.x.x / fe80::/10 link-local (cloud metadata endpoints)
    #   - 0.0.0.0 / :: unspecified
    try:
        addr = ipaddress.ip_address(hostname)
        # Unwrap IPv4-mapped IPv6 (::ffff:x.x.x.x) so the inner IPv4 address
        # gets its is_loopback / is_link_local flags checked correctly.
        check: ipaddress.IPv4Address | ipaddress.IPv6Address = addr
        if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
            check = addr.ipv4_mapped
        if check.is_loopback or check.is_link_local or check.is_unspecified:
            logger.warning(
                "%s: URL targets a restricted address (%r) — skipping notification",
                env_var, hostname,
            )
            return False
    except ValueError:
        pass  # hostname is a DNS name — cannot block without a network lookup

    return True
