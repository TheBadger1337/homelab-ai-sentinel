"""
Tests for app/runbooks.py — service runbook injection.
"""

import os

import pytest

from app.runbooks import _service_to_filename, get_runbook, format_runbook


# ---------------------------------------------------------------------------
# _service_to_filename
# ---------------------------------------------------------------------------

def test_filename_simple():
    assert _service_to_filename("nginx") == "nginx"


def test_filename_hyphenated():
    assert _service_to_filename("my-redis") == "my_redis"


def test_filename_with_spaces():
    assert _service_to_filename("web app") == "web_app"


def test_filename_with_colon():
    assert _service_to_filename("host1: cpu") == "host1__cpu"


def test_filename_uppercase_lowered():
    assert _service_to_filename("Nginx") == "nginx"


def test_filename_empty():
    assert _service_to_filename("") == ""


# ---------------------------------------------------------------------------
# get_runbook
# ---------------------------------------------------------------------------

def test_get_runbook_no_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("RUNBOOK_DIR", str(tmp_path / "nonexistent"))
    assert get_runbook("nginx") == ""


def test_get_runbook_no_matching_file(monkeypatch, tmp_path):
    monkeypatch.setenv("RUNBOOK_DIR", str(tmp_path))
    assert get_runbook("nginx") == ""


def test_get_runbook_loads_file(monkeypatch, tmp_path):
    monkeypatch.setenv("RUNBOOK_DIR", str(tmp_path))
    (tmp_path / "nginx.md").write_text("Check: systemctl status nginx")
    assert get_runbook("nginx") == "Check: systemctl status nginx"


def test_get_runbook_case_insensitive(monkeypatch, tmp_path):
    monkeypatch.setenv("RUNBOOK_DIR", str(tmp_path))
    (tmp_path / "nginx.md").write_text("runbook content")
    assert get_runbook("Nginx") == "runbook content"


def test_get_runbook_hyphenated_service(monkeypatch, tmp_path):
    monkeypatch.setenv("RUNBOOK_DIR", str(tmp_path))
    (tmp_path / "my_redis.md").write_text("redis runbook")
    assert get_runbook("my-redis") == "redis runbook"


def test_get_runbook_truncated(monkeypatch, tmp_path):
    monkeypatch.setenv("RUNBOOK_DIR", str(tmp_path))
    (tmp_path / "nginx.md").write_text("x" * 2000)
    result = get_runbook("nginx")
    assert len(result) == 1500


def test_get_runbook_empty_file(monkeypatch, tmp_path):
    monkeypatch.setenv("RUNBOOK_DIR", str(tmp_path))
    (tmp_path / "nginx.md").write_text("")
    assert get_runbook("nginx") == ""


def test_get_runbook_whitespace_only_file(monkeypatch, tmp_path):
    monkeypatch.setenv("RUNBOOK_DIR", str(tmp_path))
    (tmp_path / "nginx.md").write_text("   \n  ")
    assert get_runbook("nginx") == ""


# ---------------------------------------------------------------------------
# format_runbook
# ---------------------------------------------------------------------------

def test_format_runbook_empty():
    assert format_runbook("") == ""


def test_format_runbook_wraps_in_xml():
    result = format_runbook("Check systemctl status nginx")
    assert "<runbook>" in result
    assert "</runbook>" in result
    assert "Check systemctl status nginx" in result


def test_format_runbook_marked_as_trusted():
    result = format_runbook("some content")
    assert "trusted configuration" in result
