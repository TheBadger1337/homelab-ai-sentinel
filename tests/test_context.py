"""
Tests for app/context.py — operator infrastructure context injection.
"""

import os
import importlib

import pytest


def _reload_context(monkeypatch, env_val=None, file_content=None, file_path=None):
    """
    Reload the context module with fresh env/file state.
    Returns the reloaded module so tests can call its functions.
    """
    monkeypatch.delenv("SENTINEL_CONTEXT", raising=False)
    monkeypatch.delenv("SENTINEL_CONTEXT_FILE", raising=False)

    if env_val is not None:
        monkeypatch.setenv("SENTINEL_CONTEXT", env_val)

    if file_path is not None:
        monkeypatch.setenv("SENTINEL_CONTEXT_FILE", file_path)

    if file_content is not None and file_path is not None:
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w") as f:
            f.write(file_content)

    import app.context as ctx
    importlib.reload(ctx)
    return ctx


# ---------------------------------------------------------------------------
# get_operator_context
# ---------------------------------------------------------------------------

def test_no_context_returns_empty(monkeypatch, tmp_path):
    ctx = _reload_context(monkeypatch, file_path=str(tmp_path / "nonexistent.md"))
    assert ctx.get_operator_context() == ""


def test_env_var_context(monkeypatch, tmp_path):
    ctx = _reload_context(
        monkeypatch,
        env_val="3-node Proxmox cluster on 192.168.1.0/24",
        file_path=str(tmp_path / "nonexistent.md"),
    )
    assert ctx.get_operator_context() == "3-node Proxmox cluster on 192.168.1.0/24"


def test_file_context(monkeypatch, tmp_path):
    fpath = str(tmp_path / "context.md")
    ctx = _reload_context(
        monkeypatch,
        file_content="nginx on node2, TrueNAS on node3",
        file_path=fpath,
    )
    assert ctx.get_operator_context() == "nginx on node2, TrueNAS on node3"


def test_env_var_takes_priority_over_file(monkeypatch, tmp_path):
    fpath = str(tmp_path / "context.md")
    ctx = _reload_context(
        monkeypatch,
        env_val="from env",
        file_content="from file",
        file_path=fpath,
    )
    assert ctx.get_operator_context() == "from env"


def test_context_truncated_at_max(monkeypatch, tmp_path):
    long_ctx = "x" * 3000
    ctx = _reload_context(
        monkeypatch,
        env_val=long_ctx,
        file_path=str(tmp_path / "nonexistent.md"),
    )
    assert len(ctx.get_operator_context()) == 2000


def test_file_context_truncated_at_max(monkeypatch, tmp_path):
    fpath = str(tmp_path / "context.md")
    ctx = _reload_context(
        monkeypatch,
        file_content="y" * 3000,
        file_path=fpath,
    )
    assert len(ctx.get_operator_context()) == 2000


def test_whitespace_only_env_treated_as_empty(monkeypatch, tmp_path):
    ctx = _reload_context(
        monkeypatch,
        env_val="   \n  ",
        file_path=str(tmp_path / "nonexistent.md"),
    )
    assert ctx.get_operator_context() == ""


def test_empty_file_treated_as_no_context(monkeypatch, tmp_path):
    fpath = str(tmp_path / "context.md")
    ctx = _reload_context(
        monkeypatch,
        file_content="",
        file_path=fpath,
    )
    assert ctx.get_operator_context() == ""


# ---------------------------------------------------------------------------
# build_system_prompt
# ---------------------------------------------------------------------------

def test_build_system_prompt_no_context(monkeypatch, tmp_path):
    ctx = _reload_context(monkeypatch, file_path=str(tmp_path / "nonexistent.md"))
    base = "You are a monitoring assistant."
    assert ctx.build_system_prompt(base) == base


def test_build_system_prompt_with_context(monkeypatch, tmp_path):
    ctx = _reload_context(
        monkeypatch,
        env_val="3 Proxmox nodes, nginx on node2",
        file_path=str(tmp_path / "nonexistent.md"),
    )
    base = "You are a monitoring assistant."
    result = ctx.build_system_prompt(base)
    assert result.startswith(base)
    assert "<infrastructure_context>" in result
    assert "3 Proxmox nodes, nginx on node2" in result
    assert "</infrastructure_context>" in result


def test_build_system_prompt_context_marked_as_trusted(monkeypatch, tmp_path):
    ctx = _reload_context(
        monkeypatch,
        env_val="my infra",
        file_path=str(tmp_path / "nonexistent.md"),
    )
    result = ctx.build_system_prompt("base")
    assert "trusted configuration" in result
