"""
Unit tests for thresholds.py — per-service severity filtering.
"""

import pytest

from app.thresholds import _service_env_key, _threshold_for_service, should_suppress
from app.alert_parser import NormalizedAlert


def _alert(severity: str, service: str = "nginx") -> NormalizedAlert:
    return NormalizedAlert(
        source="generic",
        status="down",
        severity=severity,
        service_name=service,
        message="test",
        details={},
    )


# ---------------------------------------------------------------------------
# _service_env_key
# ---------------------------------------------------------------------------

def test_service_env_key_simple():
    assert _service_env_key("nginx") == "THRESHOLD_NGINX"


def test_service_env_key_hyphenated():
    assert _service_env_key("my-nginx") == "THRESHOLD_MY_NGINX"


def test_service_env_key_with_spaces():
    assert _service_env_key("web app") == "THRESHOLD_WEB_APP"


def test_service_env_key_mixed_case():
    assert _service_env_key("MyService") == "THRESHOLD_MYSERVICE"


# ---------------------------------------------------------------------------
# _threshold_for_service — global floor
# ---------------------------------------------------------------------------

def test_default_threshold_is_info(monkeypatch):
    monkeypatch.delenv("MIN_SEVERITY", raising=False)
    assert _threshold_for_service("nginx") == "info"


def test_global_floor_warning(monkeypatch):
    monkeypatch.setenv("MIN_SEVERITY", "warning")
    assert _threshold_for_service("nginx") == "warning"


def test_global_floor_critical(monkeypatch):
    monkeypatch.setenv("MIN_SEVERITY", "critical")
    assert _threshold_for_service("nginx") == "critical"


def test_invalid_global_floor_falls_back_to_info(monkeypatch):
    monkeypatch.setenv("MIN_SEVERITY", "nonsense")
    assert _threshold_for_service("nginx") == "info"


# ---------------------------------------------------------------------------
# _threshold_for_service — per-service override
# ---------------------------------------------------------------------------

def test_per_service_override_used_when_set(monkeypatch):
    monkeypatch.setenv("MIN_SEVERITY", "warning")
    monkeypatch.setenv("THRESHOLD_NGINX", "info")
    assert _threshold_for_service("nginx") == "info"


def test_per_service_override_higher_than_global(monkeypatch):
    monkeypatch.setenv("MIN_SEVERITY", "info")
    monkeypatch.setenv("THRESHOLD_NGINX", "critical")
    assert _threshold_for_service("nginx") == "critical"


def test_per_service_only_applies_to_named_service(monkeypatch):
    monkeypatch.setenv("MIN_SEVERITY", "info")
    monkeypatch.setenv("THRESHOLD_NGINX", "critical")
    # postgres is unaffected — falls back to global
    assert _threshold_for_service("postgres") == "info"


def test_per_service_invalid_value_falls_back_to_global(monkeypatch):
    monkeypatch.setenv("MIN_SEVERITY", "warning")
    monkeypatch.setenv("THRESHOLD_NGINX", "bogus")
    assert _threshold_for_service("nginx") == "warning"


# ---------------------------------------------------------------------------
# should_suppress
# ---------------------------------------------------------------------------

def test_default_passes_all_severities(monkeypatch):
    monkeypatch.delenv("MIN_SEVERITY", raising=False)
    assert not should_suppress(_alert("info"))
    assert not should_suppress(_alert("warning"))
    assert not should_suppress(_alert("critical"))


def test_global_warning_suppresses_info(monkeypatch):
    monkeypatch.setenv("MIN_SEVERITY", "warning")
    assert should_suppress(_alert("info"))


def test_global_warning_passes_warning(monkeypatch):
    monkeypatch.setenv("MIN_SEVERITY", "warning")
    assert not should_suppress(_alert("warning"))


def test_global_warning_passes_critical(monkeypatch):
    monkeypatch.setenv("MIN_SEVERITY", "warning")
    assert not should_suppress(_alert("critical"))


def test_global_critical_suppresses_info_and_warning(monkeypatch):
    monkeypatch.setenv("MIN_SEVERITY", "critical")
    assert should_suppress(_alert("info"))
    assert should_suppress(_alert("warning"))
    assert not should_suppress(_alert("critical"))


def test_per_service_lower_than_global_passes_lower_severity(monkeypatch):
    monkeypatch.setenv("MIN_SEVERITY", "critical")
    monkeypatch.setenv("THRESHOLD_NGINX", "info")
    # nginx is more permissive — info passes
    assert not should_suppress(_alert("info", service="nginx"))
    # postgres still uses global critical threshold
    assert should_suppress(_alert("info", service="postgres"))


def test_per_service_higher_than_global_suppresses_more(monkeypatch):
    monkeypatch.setenv("MIN_SEVERITY", "info")
    monkeypatch.setenv("THRESHOLD_NGINX", "critical")
    assert should_suppress(_alert("info", service="nginx"))
    assert should_suppress(_alert("warning", service="nginx"))
    assert not should_suppress(_alert("critical", service="nginx"))


def test_hyphenated_service_name_resolved(monkeypatch):
    monkeypatch.delenv("MIN_SEVERITY", raising=False)
    monkeypatch.setenv("THRESHOLD_MY_NGINX", "critical")
    assert should_suppress(_alert("info", service="my-nginx"))
    assert not should_suppress(_alert("critical", service="my-nginx"))
