"""
Unit tests for utils.py helper functions.
"""

from app.utils import _env_int, _env_float


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
