"""
Unit tests for thresholds.py — severity filtering, quiet hours, metric thresholds.
"""

import pytest
from datetime import time as _Time

from app.thresholds import (
    _service_env_key,
    _threshold_for_service,
    _in_quiet_hours,
    _metric_keyword,
    _extract_metric_from_details,
    _extract_metric_from_message,
    _should_suppress_metric,
    should_suppress,
)
from app.alert_parser import NormalizedAlert


def _alert(severity: str, service: str = "nginx", message: str = "test", details: dict | None = None) -> NormalizedAlert:
    return NormalizedAlert(
        source="generic",
        status="down",
        severity=severity,
        service_name=service,
        message=message,
        details=details or {},
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


# ---------------------------------------------------------------------------
# _in_quiet_hours
# ---------------------------------------------------------------------------

def test_quiet_hours_disabled_when_unset(monkeypatch):
    monkeypatch.delenv("QUIET_HOURS", raising=False)
    assert not _in_quiet_hours()


def test_quiet_hours_same_day_inside(monkeypatch):
    monkeypatch.setenv("QUIET_HOURS", "22:00-23:00")
    assert _in_quiet_hours(now=_Time(22, 30))


def test_quiet_hours_same_day_outside(monkeypatch):
    monkeypatch.setenv("QUIET_HOURS", "22:00-23:00")
    assert not _in_quiet_hours(now=_Time(12, 0))


def test_quiet_hours_same_day_at_start(monkeypatch):
    monkeypatch.setenv("QUIET_HOURS", "22:00-23:00")
    assert _in_quiet_hours(now=_Time(22, 0))


def test_quiet_hours_same_day_at_end_exclusive(monkeypatch):
    # End time is exclusive — at exactly 23:00, quiet hours are over
    monkeypatch.setenv("QUIET_HOURS", "22:00-23:00")
    assert not _in_quiet_hours(now=_Time(23, 0))


def test_quiet_hours_overnight_inside_before_midnight(monkeypatch):
    monkeypatch.setenv("QUIET_HOURS", "22:00-08:00")
    assert _in_quiet_hours(now=_Time(23, 30))


def test_quiet_hours_overnight_inside_after_midnight(monkeypatch):
    monkeypatch.setenv("QUIET_HOURS", "22:00-08:00")
    assert _in_quiet_hours(now=_Time(3, 0))


def test_quiet_hours_overnight_outside(monkeypatch):
    monkeypatch.setenv("QUIET_HOURS", "22:00-08:00")
    assert not _in_quiet_hours(now=_Time(12, 0))


def test_quiet_hours_invalid_format_returns_false(monkeypatch):
    monkeypatch.setenv("QUIET_HOURS", "not-a-time-range")
    assert not _in_quiet_hours()


# ---------------------------------------------------------------------------
# Quiet hours + should_suppress integration
# ---------------------------------------------------------------------------

def test_quiet_hours_suppresses_non_critical(monkeypatch):
    monkeypatch.setenv("QUIET_HOURS", "22:00-08:00")
    monkeypatch.delenv("MIN_SEVERITY", raising=False)
    # Patch _in_quiet_hours to return True without depending on the clock
    monkeypatch.setattr("app.thresholds._in_quiet_hours", lambda: True)
    assert should_suppress(_alert("info"))
    assert should_suppress(_alert("warning"))
    assert not should_suppress(_alert("critical"))


def test_quiet_hours_custom_threshold(monkeypatch):
    monkeypatch.setenv("QUIET_HOURS_MIN_SEVERITY", "warning")
    monkeypatch.delenv("MIN_SEVERITY", raising=False)
    monkeypatch.setattr("app.thresholds._in_quiet_hours", lambda: True)
    assert should_suppress(_alert("info"))
    assert not should_suppress(_alert("warning"))
    assert not should_suppress(_alert("critical"))


def test_quiet_hours_takes_more_restrictive_threshold(monkeypatch):
    # Regular threshold is already critical; quiet hours adds warning — critical wins
    monkeypatch.setenv("MIN_SEVERITY", "critical")
    monkeypatch.setenv("QUIET_HOURS_MIN_SEVERITY", "warning")
    monkeypatch.setattr("app.thresholds._in_quiet_hours", lambda: True)
    assert should_suppress(_alert("warning"))
    assert not should_suppress(_alert("critical"))


def test_outside_quiet_hours_uses_regular_threshold(monkeypatch):
    monkeypatch.setenv("QUIET_HOURS_MIN_SEVERITY", "critical")
    monkeypatch.delenv("MIN_SEVERITY", raising=False)
    monkeypatch.setattr("app.thresholds._in_quiet_hours", lambda: False)
    # Outside quiet hours — default threshold (info) passes everything
    assert not should_suppress(_alert("info"))
    assert not should_suppress(_alert("warning"))


# ---------------------------------------------------------------------------
# _metric_keyword
# ---------------------------------------------------------------------------

def test_metric_keyword_strips_percent_suffix():
    assert _metric_keyword("memory_percent") == "memory"


def test_metric_keyword_strips_usage_suffix():
    assert _metric_keyword("disk_usage") == "disk"


def test_metric_keyword_no_suffix():
    assert _metric_keyword("memory") == "memory"


def test_metric_keyword_replaces_underscores():
    assert _metric_keyword("swap_space") == "swap space"


# ---------------------------------------------------------------------------
# _extract_metric_from_details
# ---------------------------------------------------------------------------

def test_extract_details_exact_key():
    alert = _alert("warning", details={"memory_percent": 71.18})
    assert _extract_metric_from_details(alert, "memory_percent") == pytest.approx(71.18)


def test_extract_details_base_key_without_suffix():
    # Details has "memory" but env key is "memory_percent"
    alert = _alert("warning", details={"memory": 71})
    assert _extract_metric_from_details(alert, "memory_percent") == pytest.approx(71.0)


def test_extract_details_string_value():
    alert = _alert("warning", details={"memory_percent": "71.18"})
    assert _extract_metric_from_details(alert, "memory_percent") == pytest.approx(71.18)


def test_extract_details_string_with_pct_sign():
    alert = _alert("warning", details={"memory_percent": "71.18%"})
    assert _extract_metric_from_details(alert, "memory_percent") == pytest.approx(71.18)


def test_extract_details_case_insensitive():
    alert = _alert("warning", details={"MEMORY_PERCENT": 71.18})
    assert _extract_metric_from_details(alert, "memory_percent") == pytest.approx(71.18)


def test_extract_details_missing_key():
    alert = _alert("warning", details={"cpu_percent": 45.0})
    assert _extract_metric_from_details(alert, "memory_percent") is None


def test_extract_details_empty_details():
    alert = _alert("warning", details={})
    assert _extract_metric_from_details(alert, "memory_percent") is None


# ---------------------------------------------------------------------------
# _extract_metric_from_message
# ---------------------------------------------------------------------------

def test_extract_message_simple():
    assert _extract_metric_from_message(
        "reporting high memory utilization, currently at 71.18%", "memory_percent"
    ) == pytest.approx(71.18)


def test_extract_message_integer_percent():
    assert _extract_metric_from_message(
        "disk usage is at 85%", "disk_percent"
    ) == pytest.approx(85.0)


def test_extract_message_closest_to_keyword():
    # "CPU at 45%, memory at 71%" — should return 71 for memory_percent
    assert _extract_metric_from_message(
        "CPU at 45%, memory at 71%", "memory_percent"
    ) == pytest.approx(71.0)


def test_extract_message_keyword_not_found():
    assert _extract_metric_from_message(
        "disk usage is at 85%", "memory_percent"
    ) is None


def test_extract_message_no_percentage():
    assert _extract_metric_from_message(
        "memory is high but no number here", "memory_percent"
    ) is None


# ---------------------------------------------------------------------------
# _should_suppress_metric + should_suppress integration
# ---------------------------------------------------------------------------

def test_metric_threshold_suppresses_below_threshold(monkeypatch):
    monkeypatch.setenv("METRIC_THRESHOLD_MEMORY_PERCENT", "95")
    alert = _alert("warning", details={"memory_percent": 71.18})
    assert _should_suppress_metric(alert)


def test_metric_threshold_passes_above_threshold(monkeypatch):
    monkeypatch.setenv("METRIC_THRESHOLD_MEMORY_PERCENT", "95")
    alert = _alert("warning", details={"memory_percent": 97.0})
    assert not _should_suppress_metric(alert)


def test_metric_threshold_passes_at_exact_threshold(monkeypatch):
    monkeypatch.setenv("METRIC_THRESHOLD_MEMORY_PERCENT", "95")
    alert = _alert("warning", details={"memory_percent": 95.0})
    assert not _should_suppress_metric(alert)


def test_metric_threshold_from_message(monkeypatch):
    monkeypatch.setenv("METRIC_THRESHOLD_MEMORY_PERCENT", "95")
    alert = _alert("warning", message="memory utilization at 71.18%")
    assert _should_suppress_metric(alert)


def test_metric_threshold_no_match_passes(monkeypatch):
    monkeypatch.setenv("METRIC_THRESHOLD_MEMORY_PERCENT", "95")
    # Alert has no memory data — threshold does not apply
    alert = _alert("warning", message="nginx is down")
    assert not _should_suppress_metric(alert)


def test_metric_threshold_invalid_env_value_ignored(monkeypatch):
    monkeypatch.setenv("METRIC_THRESHOLD_MEMORY_PERCENT", "not_a_number")
    alert = _alert("warning", details={"memory_percent": 71.0})
    assert not _should_suppress_metric(alert)


def test_metric_suppression_surfaced_through_should_suppress(monkeypatch):
    monkeypatch.delenv("MIN_SEVERITY", raising=False)
    monkeypatch.setenv("METRIC_THRESHOLD_CPU_PERCENT", "90")
    monkeypatch.setattr("app.thresholds._in_quiet_hours", lambda: False)
    assert should_suppress(_alert("critical", details={"cpu_percent": 45.0}))
    assert not should_suppress(_alert("critical", details={"cpu_percent": 92.0}))
