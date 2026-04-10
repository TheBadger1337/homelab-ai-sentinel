"""
Action Proxy — operator-defined runnable scripts with UI approval gate.

Operators define actions in ``actions.yaml`` (inside RUNBOOK_DIR by default,
overridden by ACTIONS_FILE env var). Each action is a named script with an
optional per-service filter, description, command list, and timeout.

When an alert fires, the webhook pipeline queues all catalog actions that
apply to the alerting service. An operator then approves or rejects each
action from the web UI. Approved actions run via subprocess in an empty
environment (no secrets leak) and their output is stored in SQLite.

YAML format
===========
::

    actions:
      restart_nginx:
        description: "Restart nginx service"
        command: ["systemctl", "restart", "nginx"]
        timeout: 15           # optional, default 30
        services: ["nginx"]   # optional — empty list means all services

      disk_check:
        description: "Report disk usage"
        command: "df -h"      # string is split on whitespace
        # no services key → applies to every alert

Security
========
- All actions run with ``env={}`` — no environment variable inheritance so
  secrets from .secrets.env cannot leak into the subprocess.
- ``shell=False`` — command is passed as a list, preventing shell injection.
- stdout + stderr are capped at 4 096 characters before DB storage.
- Only UI-authenticated operators can trigger approvals.
"""

import logging
import os
import subprocess
from dataclasses import dataclass, field
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_OUTPUT_CAP = 4096


@dataclass
class ActionDef:
    name: str
    description: str
    command: list[str]
    timeout: int = 30
    services: list[str] = field(default_factory=list)  # empty → applies to all


def _actions_path() -> str:
    explicit = os.environ.get("ACTIONS_FILE", "").strip()
    if explicit:
        return explicit
    runbook_dir = os.environ.get("RUNBOOK_DIR", "/data/runbooks")
    return os.path.join(runbook_dir, "actions.yaml")


def load_catalog() -> list[ActionDef]:
    """Load and return the action catalog from actions.yaml.

    Returns an empty list when the file is absent or unparseable.
    Warnings are logged but never raised — a bad catalog must not block
    the webhook pipeline.
    """
    path = _actions_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            data: Any = yaml.safe_load(f)
        if not isinstance(data, dict):
            logger.warning("actions.yaml: expected a mapping at the top level — skipping")
            return []
        raw_actions = data.get("actions") or {}
        if not isinstance(raw_actions, dict):
            logger.warning("actions.yaml: 'actions' key must be a mapping — skipping")
            return []
        catalog: list[ActionDef] = []
        for name, cfg in raw_actions.items():
            if not isinstance(cfg, dict):
                logger.warning("actions.yaml: action %r must be a mapping — skipping", name)
                continue
            cmd = cfg.get("command", [])
            if isinstance(cmd, str):
                cmd = cmd.split()
            if not cmd:
                logger.warning("actions.yaml: action %r has no command — skipping", name)
                continue
            catalog.append(
                ActionDef(
                    name=str(name),
                    description=str(cfg.get("description", "")),
                    command=[str(c) for c in cmd],
                    timeout=max(1, int(cfg.get("timeout", 30))),
                    services=[s.lower() for s in cfg.get("services", [])],
                )
            )
        return catalog
    except Exception as exc:
        logger.warning("Failed to load actions.yaml: %s", type(exc).__name__)
        return []


def get_applicable_actions(service_name: str) -> list[ActionDef]:
    """Return catalog actions applicable to *service_name*.

    An action is applicable when its ``services`` list is empty (global) or
    contains the service name (case-insensitive).
    """
    service_lower = service_name.lower()
    return [
        a for a in load_catalog()
        if not a.services or service_lower in a.services
    ]


def run_action(action: ActionDef) -> tuple[int, str]:
    """Execute *action* and return ``(returncode, output)``.

    The command runs in a blank environment (``env={}``), no shell expansion,
    and is killed after *timeout* seconds. Output is the combined stdout +
    stderr, capped to ``_OUTPUT_CAP`` characters.

    Returns ``(-1, <reason>)`` on timeout or unexpected error.
    """
    try:
        result = subprocess.run(
            action.command,
            capture_output=True,
            text=True,
            timeout=action.timeout,
            env={},
            shell=False,
        )
        combined = (result.stdout + result.stderr)[:_OUTPUT_CAP]
        return result.returncode, combined
    except subprocess.TimeoutExpired:
        return -1, f"Action timed out after {action.timeout}s"
    except Exception as exc:
        return -1, f"Action error: {type(exc).__name__}"
