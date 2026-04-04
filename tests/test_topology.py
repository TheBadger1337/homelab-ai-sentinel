"""
Tests for topology mapping.
"""

import json
import os

import pytest
from unittest.mock import patch, MagicMock

from app.alert_parser import NormalizedAlert
import app.topology as topo
import app.llm_client as lc


@pytest.fixture(autouse=True)
def _reset_cache():
    """Reset the topology cache before each test."""
    topo.reset_cache()
    yield
    topo.reset_cache()


def _write_topology(tmp_path, data):
    """Write a topology YAML string to a temp file and return the path."""
    path = tmp_path / "topology.yaml"
    path.write_text(data)
    return str(path)


def _make_alert(service="nginx", status="down", severity="critical", message="Connection refused"):
    return NormalizedAlert(
        source="uptime_kuma",
        status=status,
        severity=severity,
        service_name=service,
        message=message,
        details={},
    )


# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------

def test_load_basic_topology(tmp_path, monkeypatch):
    path = _write_topology(tmp_path, """
services:
  nginx:
    depends_on: [docker]
    host: node2
    description: Reverse proxy
  postgres:
    depends_on: [docker]
    host: node1
""")
    monkeypatch.setenv("TOPOLOGY_FILE", path)
    services = topo._load_topology()
    assert "nginx" in services
    assert "postgres" in services
    assert services["nginx"]["depends_on"] == ["docker"]
    assert services["nginx"]["host"] == "node2"


def test_load_from_runbook_dir(tmp_path, monkeypatch):
    runbook_dir = str(tmp_path / "runbooks")
    os.makedirs(runbook_dir)
    path = os.path.join(runbook_dir, "topology.yaml")
    with open(path, "w") as f:
        f.write("services:\n  redis:\n    depends_on: []\n")
    monkeypatch.setenv("RUNBOOK_DIR", runbook_dir)
    monkeypatch.delenv("TOPOLOGY_FILE", raising=False)
    services = topo._load_topology()
    assert "redis" in services


def test_topology_file_env_takes_priority(tmp_path, monkeypatch):
    # TOPOLOGY_FILE should be checked before RUNBOOK_DIR
    explicit = _write_topology(tmp_path, "services:\n  explicit:\n    depends_on: []\n")
    monkeypatch.setenv("TOPOLOGY_FILE", explicit)
    monkeypatch.setenv("RUNBOOK_DIR", "/nonexistent")
    services = topo._load_topology()
    assert "explicit" in services


def test_missing_file_returns_empty(monkeypatch):
    monkeypatch.setenv("TOPOLOGY_FILE", "/nonexistent/topology.yaml")
    services = topo._load_topology()
    assert services == {}


def test_invalid_yaml_returns_empty(tmp_path, monkeypatch):
    path = _write_topology(tmp_path, "not: [valid: yaml: {{{")
    monkeypatch.setenv("TOPOLOGY_FILE", path)
    services = topo._load_topology()
    assert services == {}


def test_non_dict_root_returns_empty(tmp_path, monkeypatch):
    path = _write_topology(tmp_path, "- just a list\n- not a mapping")
    monkeypatch.setenv("TOPOLOGY_FILE", path)
    services = topo._load_topology()
    assert services == {}


def test_non_dict_services_returns_empty(tmp_path, monkeypatch):
    path = _write_topology(tmp_path, "services: not_a_dict")
    monkeypatch.setenv("TOPOLOGY_FILE", path)
    services = topo._load_topology()
    assert services == {}


def test_depends_on_string_normalized_to_list(tmp_path, monkeypatch):
    path = _write_topology(tmp_path, "services:\n  app:\n    depends_on: postgres\n")
    monkeypatch.setenv("TOPOLOGY_FILE", path)
    services = topo._load_topology()
    assert services["app"]["depends_on"] == ["postgres"]


def test_depends_on_missing_defaults_to_empty_list(tmp_path, monkeypatch):
    path = _write_topology(tmp_path, "services:\n  app:\n    host: node1\n")
    monkeypatch.setenv("TOPOLOGY_FILE", path)
    services = topo._load_topology()
    assert services["app"]["depends_on"] == []


def test_non_dict_service_entry_normalized(tmp_path, monkeypatch):
    path = _write_topology(tmp_path, "services:\n  bad_entry: just_a_string\n")
    monkeypatch.setenv("TOPOLOGY_FILE", path)
    services = topo._load_topology()
    assert services["bad_entry"] == {"depends_on": []}


# ---------------------------------------------------------------------------
# Cache behavior
# ---------------------------------------------------------------------------

def test_cache_prevents_repeated_reads(tmp_path, monkeypatch):
    path = _write_topology(tmp_path, "services:\n  nginx:\n    depends_on: []\n")
    monkeypatch.setenv("TOPOLOGY_FILE", path)
    s1 = topo._load_topology()
    # Overwrite file — should not affect cached result
    with open(path, "w") as f:
        f.write("services:\n  changed:\n    depends_on: []\n")
    s2 = topo._load_topology()
    assert s1 is s2
    assert "nginx" in s2


def test_reset_cache_clears(tmp_path, monkeypatch):
    path = _write_topology(tmp_path, "services:\n  nginx:\n    depends_on: []\n")
    monkeypatch.setenv("TOPOLOGY_FILE", path)
    topo._load_topology()
    topo.reset_cache()
    # After reset, next load should re-read file
    with open(path, "w") as f:
        f.write("services:\n  changed:\n    depends_on: []\n")
    s = topo._load_topology()
    assert "changed" in s


# ---------------------------------------------------------------------------
# No PyYAML
# ---------------------------------------------------------------------------

def test_no_yaml_returns_empty(monkeypatch):
    monkeypatch.setattr(topo, "yaml", None)
    services = topo._load_topology()
    assert services == {}


# ---------------------------------------------------------------------------
# get_topology — formatted output
# ---------------------------------------------------------------------------

_SAMPLE_YAML = """\
services:
  nginx:
    depends_on: [docker]
    host: node2
    description: Reverse proxy for all web services
  postgres:
    depends_on: [docker]
    host: node1
    description: Primary database
  nextcloud:
    depends_on: [nginx, postgres, redis]
    host: node1
  redis:
    depends_on: [docker]
    host: node1
    description: Session cache
  gitea:
    depends_on: [nginx, postgres]
    host: node2
  docker:
    depends_on: []
    host: node1
    description: Container runtime
"""


def test_get_topology_service_with_deps_and_dependents(tmp_path, monkeypatch):
    path = _write_topology(tmp_path, _SAMPLE_YAML)
    monkeypatch.setenv("TOPOLOGY_FILE", path)
    result = topo.get_topology("nginx")
    assert "nginx" in result
    assert "node2" in result
    assert "Reverse proxy" in result
    assert "docker" in result  # depends_on
    assert "nextcloud" in result  # depended_by
    assert "gitea" in result  # depended_by


def test_get_topology_leaf_service(tmp_path, monkeypatch):
    path = _write_topology(tmp_path, _SAMPLE_YAML)
    monkeypatch.setenv("TOPOLOGY_FILE", path)
    result = topo.get_topology("nextcloud")
    assert "nginx" in result
    assert "postgres" in result
    assert "redis" in result


def test_get_topology_root_service(tmp_path, monkeypatch):
    path = _write_topology(tmp_path, _SAMPLE_YAML)
    monkeypatch.setenv("TOPOLOGY_FILE", path)
    result = topo.get_topology("docker")
    assert "Container runtime" in result
    # docker is depended on by many services
    assert "nginx" in result
    assert "postgres" in result
    assert "redis" in result


def test_get_topology_unknown_service(tmp_path, monkeypatch):
    path = _write_topology(tmp_path, _SAMPLE_YAML)
    monkeypatch.setenv("TOPOLOGY_FILE", path)
    result = topo.get_topology("unknown_service")
    assert result == ""


def test_get_topology_case_insensitive(tmp_path, monkeypatch):
    path = _write_topology(tmp_path, _SAMPLE_YAML)
    monkeypatch.setenv("TOPOLOGY_FILE", path)
    result = topo.get_topology("NGINX")
    assert "nginx" in result
    assert "node2" in result


def test_get_topology_no_file_returns_empty(monkeypatch):
    monkeypatch.setenv("TOPOLOGY_FILE", "/nonexistent")
    assert topo.get_topology("nginx") == ""


def test_get_topology_referenced_but_not_declared(tmp_path, monkeypatch):
    # A service that is referenced in depends_on but not declared as a top-level key
    path = _write_topology(tmp_path, """\
services:
  app:
    depends_on: [mystery_dep]
""")
    monkeypatch.setenv("TOPOLOGY_FILE", path)
    result = topo.get_topology("mystery_dep")
    assert "referenced as a dependency" in result
    assert "app" in result


def test_get_topology_no_deps_no_dependents(tmp_path, monkeypatch):
    path = _write_topology(tmp_path, "services:\n  standalone:\n    depends_on: []\n    host: node1\n")
    monkeypatch.setenv("TOPOLOGY_FILE", path)
    result = topo.get_topology("standalone")
    assert "No declared dependencies" in result


# ---------------------------------------------------------------------------
# format_topology
# ---------------------------------------------------------------------------

def test_format_topology_wraps_in_xml():
    content = 'Service "nginx" runs on node2.'
    result = topo.format_topology(content)
    assert result.startswith("\n<topology>")
    assert "</topology>" in result
    assert "nginx" in result
    assert "dependency graph" in result


def test_format_topology_empty_returns_empty():
    assert topo.format_topology("") == ""


# ---------------------------------------------------------------------------
# derive_depended_by
# ---------------------------------------------------------------------------

def test_derive_depended_by(tmp_path, monkeypatch):
    path = _write_topology(tmp_path, _SAMPLE_YAML)
    monkeypatch.setenv("TOPOLOGY_FILE", path)
    services = topo._load_topology()
    depended_by = topo._derive_depended_by(services, "nginx")
    assert "gitea" in depended_by
    assert "nextcloud" in depended_by
    assert "postgres" not in depended_by


def test_derive_depended_by_case_insensitive(tmp_path, monkeypatch):
    path = _write_topology(tmp_path, _SAMPLE_YAML)
    monkeypatch.setenv("TOPOLOGY_FILE", path)
    services = topo._load_topology()
    depended_by = topo._derive_depended_by(services, "NGINX")
    assert "gitea" in depended_by


# ---------------------------------------------------------------------------
# Prompt integration
# ---------------------------------------------------------------------------

def test_topology_injected_into_prompt(tmp_path, monkeypatch):
    path = _write_topology(tmp_path, _SAMPLE_YAML)
    monkeypatch.setenv("TOPOLOGY_FILE", path)
    alert = _make_alert(service="nginx")
    topo_str = topo.get_topology("nginx")
    prompt = lc._build_prompt(alert, topology=topo_str)
    assert "<topology>" in prompt
    assert "nginx" in prompt
    assert "dependency graph" in prompt


def test_empty_topology_not_in_prompt():
    alert = _make_alert()
    prompt = lc._build_prompt(alert, topology="")
    assert "<topology>" not in prompt


# ---------------------------------------------------------------------------
# Webhook integration
# ---------------------------------------------------------------------------

@pytest.fixture
def _db(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    from app.alert_db import init_db
    init_db()


@pytest.fixture
def client(monkeypatch, _db):
    monkeypatch.setenv("SENTINEL_MODE", "predictive")
    monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("DEDUP_TTL_SECONDS", "0")
    monkeypatch.setenv("COOLDOWN_SECONDS", "0")
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_TOKEN", "test-token")
    monkeypatch.setenv("GEMINI_RPM", "0")
    monkeypatch.setenv("GEMINI_RETRIES", "0")
    with lc._gemini_rpm_lock:
        lc._gemini_rpm_call_times.clear()
    from app import create_app
    app = create_app()
    return app.test_client()


def _mock_gemini_resp(insight="AI insight", actions=None):
    mock = MagicMock()
    mock.status_code = 200
    mock.raise_for_status.return_value = None
    mock.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": json.dumps({
            "insight": insight,
            "suggested_actions": actions or ["check logs"],
        })}]}}]
    }
    return mock


def test_webhook_passes_topology_to_ai(tmp_path, client, monkeypatch):
    path = _write_topology(tmp_path, _SAMPLE_YAML)
    monkeypatch.setenv("TOPOLOGY_FILE", path)

    with patch.object(lc._gemini_session, "post", return_value=_mock_gemini_resp()) as mock_post:
        resp = client.post(
            "/webhook",
            data=json.dumps({
                "heartbeat": {"status": 0},
                "monitor": {"name": "nginx"},
                "msg": "nginx is down",
            }),
            content_type="application/json",
        )

    assert resp.status_code == 200
    # Verify the AI was called and the prompt included topology
    call_args = mock_post.call_args
    payload = call_args[1]["json"] if "json" in call_args[1] else call_args[0][1]
    prompt_text = payload["contents"][0]["parts"][0]["text"]
    assert "<topology>" in prompt_text


def test_webhook_works_without_topology(client, monkeypatch):
    monkeypatch.setenv("TOPOLOGY_FILE", "/nonexistent/topology.yaml")

    with patch.object(lc._gemini_session, "post", return_value=_mock_gemini_resp()) as mock_post:
        resp = client.post(
            "/webhook",
            data=json.dumps({
                "heartbeat": {"status": 0},
                "monitor": {"name": "nginx"},
                "msg": "nginx is down",
            }),
            content_type="application/json",
        )

    assert resp.status_code == 200
    call_args = mock_post.call_args
    payload = call_args[1]["json"] if "json" in call_args[1] else call_args[0][1]
    prompt_text = payload["contents"][0]["parts"][0]["text"]
    assert "<topology>" not in prompt_text
