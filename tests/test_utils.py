"""
Unit tests for utils.py helper functions.
"""

from app.utils import _env_int, _env_float, _validate_url


def test_env_int_returns_default_on_invalid_value(monkeypatch):
    monkeypatch.setenv("TEST_UTILS_INT", "not_a_number")
    assert _env_int("TEST_UTILS_INT", 42) == 42


def test_env_int_parses_valid_value(monkeypatch):
    monkeypatch.setenv("TEST_UTILS_INT", "99")
    assert _env_int("TEST_UTILS_INT", 0) == 99


def test_env_int_returns_default_when_unset(monkeypatch):
    monkeypatch.delenv("TEST_UTILS_INT", raising=False)
    assert _env_int("TEST_UTILS_INT", 7) == 7


def test_env_float_returns_default_on_invalid_value(monkeypatch):
    monkeypatch.setenv("TEST_UTILS_FLOAT", "abc")
    assert _env_float("TEST_UTILS_FLOAT", 1.5) == 1.5


def test_env_float_parses_valid_value(monkeypatch):
    monkeypatch.setenv("TEST_UTILS_FLOAT", "3.14")
    result = _env_float("TEST_UTILS_FLOAT", 0.0)
    assert abs(result - 3.14) < 1e-9


def test_env_float_returns_default_when_unset(monkeypatch):
    monkeypatch.delenv("TEST_UTILS_FLOAT", raising=False)
    assert _env_float("TEST_UTILS_FLOAT", 2.5) == 2.5


# ---------------------------------------------------------------------------
# _validate_url
# ---------------------------------------------------------------------------

def test_validate_url_accepts_http():
    assert _validate_url("http://gotify.home.internal:80", "TEST_VAR") is True


def test_validate_url_accepts_https():
    assert _validate_url("https://example.com/path", "TEST_VAR") is True


def test_validate_url_accepts_rfc1918():
    # Internal homelab IPs must remain reachable — all notification backends live here
    assert _validate_url("http://192.168.1.50:8080", "TEST_VAR") is True


def test_validate_url_rejects_file_scheme():
    assert _validate_url("file:///etc/passwd", "TEST_VAR") is False


def test_validate_url_rejects_ftp_scheme():
    assert _validate_url("ftp://files.example.com", "TEST_VAR") is False


def test_validate_url_rejects_no_scheme():
    assert _validate_url("gotify.home.internal:8080", "TEST_VAR") is False


def test_validate_url_rejects_localhost():
    assert _validate_url("http://localhost:8080", "TEST_VAR") is False


def test_validate_url_rejects_loopback_ip():
    assert _validate_url("http://127.0.0.1:9000", "TEST_VAR") is False


def test_validate_url_rejects_link_local():
    assert _validate_url("http://169.254.169.254/latest/meta-data/", "TEST_VAR") is False


def test_validate_url_rejects_ipv6_loopback():
    """::1 in bracket notation must be blocked."""
    assert _validate_url("http://[::1]:8080", "TEST_VAR") is False


def test_validate_url_rejects_ipv4_mapped_loopback():
    """IPv4-mapped IPv6 loopback (::ffff:127.0.0.1) must be blocked."""
    assert _validate_url("http://[::ffff:127.0.0.1]", "TEST_VAR") is False


def test_validate_url_rejects_ipv6_link_local():
    """fe80::/10 link-local IPv6 must be blocked."""
    assert _validate_url("http://[fe80::1]", "TEST_VAR") is False


def test_validate_url_rejects_unspecified():
    """0.0.0.0 (unspecified) must be blocked."""
    assert _validate_url("http://0.0.0.0:5000", "TEST_VAR") is False


def test_validate_url_rejects_empty_hostname():
    """URL with no hostname must be blocked."""
    assert _validate_url("http:///path", "TEST_VAR") is False


def test_validate_url_accepts_rfc1918_10_block():
    assert _validate_url("http://10.0.0.1:8080", "TEST_VAR") is True


def test_validate_url_accepts_rfc1918_172_block():
    assert _validate_url("http://172.16.0.1:8080", "TEST_VAR") is True
