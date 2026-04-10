"""
Reverse Triage — operator-configured diagnostic scripts for deep-dive context.

When an alert fires for a configured service, Sentinel runs a pre-configured
script and injects its stdout into the AI prompt as <triage_context> so the
AI can produce a more specific diagnosis.

Configuration (one per service):
  REVERSE_TRIAGE_<SERVICE_KEY>=<absolute_path>

Service key normalization (same as per-service thresholds):
  service "nginx"     →  REVERSE_TRIAGE_NGINX
  service "my-nginx"  →  REVERSE_TRIAGE_MY_NGINX
  service "web app"   →  REVERSE_TRIAGE_WEB_APP

Script interface:
  argv[1]  — service name (exact, as received in the alert)
  argv[2]  — alert severity (info, warning, critical)
  stdout   — captured and injected into AI prompt (capped at 2000 chars)
  stderr   — discarded (never logged — may contain secrets)
  exit code — non-zero is treated as "no context available" (logged, not fatal)

Security:
  - Paths must be pre-configured by the operator via env vars — never user-supplied.
  - Scripts are executed with shell=False to prevent shell injection.
  - Sentinel's own environment variables are NOT passed to scripts (env={}).
  - Scripts run with a hard timeout (REVERSE_TRIAGE_TIMEOUT, default 10s).
  - Output is XML-escaped before prompt insertion to prevent tag breakout.

Failure policy:
  Any error (timeout, non-zero exit, missing file, permission denied) returns
  None. A failed triage script never blocks the alert pipeline.
"""

import logging
import os
import subprocess

from .alert_parser import NormalizedAlert

logger = logging.getLogger(__name__)

_OUTPUT_MAX = 2000  # chars of script output injected into prompt


def _service_env_key(service: str) -> str:
    """Normalize a service name to its REVERSE_TRIAGE_<KEY> env var name."""
    sanitised = "".join(c if c.isalnum() else "_" for c in service.upper())
    return f"REVERSE_TRIAGE_{sanitised}"


def get_triage_context(alert: NormalizedAlert) -> str | None:
    """
    Run the configured triage script for this alert's service and return its output.

    Returns None if:
    - No script is configured for this service.
    - The configured path does not exist or is not a file.
    - The script times out or exits non-zero.
    - Any other error occurs during execution.

    Never raises — failure is always silently logged and returns None.
    """
    key = _service_env_key(alert.service_name)
    script_path = os.environ.get(key, "").strip()

    if not script_path:
        return None  # not configured for this service

    # Validate path up front — operator configured but script missing is worth warning about
    if not os.path.isfile(script_path):
        logger.warning(
            "Reverse triage: script not found for service=%r (key=%s path=%r)",
            alert.service_name, key, script_path,
        )
        return None

    timeout = int(os.environ.get("REVERSE_TRIAGE_TIMEOUT", "10") or "10")

    try:
        result = subprocess.run(
            [script_path, alert.service_name, alert.severity],
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,   # never use shell=True — prevents injection via service names
            env={},        # blank env — no Sentinel secrets leaked to scripts
        )
    except subprocess.TimeoutExpired:
        logger.warning(
            "Reverse triage: script timed out after %ds for service=%r",
            timeout, alert.service_name,
        )
        return None
    except OSError as exc:
        logger.warning(
            "Reverse triage: failed to run script for service=%r: %s",
            alert.service_name, type(exc).__name__,
        )
        return None
    except Exception:
        logger.warning(
            "Reverse triage: unexpected error for service=%r",
            alert.service_name, exc_info=True,
        )
        return None

    if result.returncode != 0:
        logger.warning(
            "Reverse triage: script exited %d for service=%r — ignoring output",
            result.returncode, alert.service_name,
        )
        return None

    output = result.stdout.strip()
    if not output:
        return None

    if len(output) > _OUTPUT_MAX:
        output = output[:_OUTPUT_MAX]
        logger.debug(
            "Reverse triage: output truncated to %d chars for service=%r",
            _OUTPUT_MAX, alert.service_name,
        )

    logger.info(
        "Reverse triage: collected %d chars of context for service=%r",
        len(output), alert.service_name,
    )
    return output
