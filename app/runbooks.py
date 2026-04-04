"""
Runbook injection for AI prompts.

Maps service names to local markdown files so the AI can suggest
remediation steps specific to the operator's actual infrastructure
rather than generic Linux advice.

Runbook directory: RUNBOOK_DIR env var (default: /data/runbooks/)
File convention:   <service_name>.md  — matched case-insensitively
                   Non-alphanumeric chars in the service name are
                   replaced with underscores for the filename lookup.
                   e.g. "nginx" → nginx.md
                        "my-redis" → my_redis.md
                        "host1: cpu" → host1__cpu.md

Cap: 1500 characters per runbook. Longer files are truncated.

Failure policy: returns empty string on any error. Callers treat empty
as "no runbook available" — the AI call proceeds without it.
"""

import logging
import os

logger = logging.getLogger(__name__)

_RUNBOOK_MAX = 1500


def _runbook_dir() -> str:
    return os.environ.get("RUNBOOK_DIR", "/data/runbooks")


def _service_to_filename(service_name: str) -> str:
    """Convert a service name to a runbook filename (without extension)."""
    return "".join(c if c.isalnum() else "_" for c in service_name).strip("_").lower()


def get_runbook(service_name: str) -> str:
    """
    Load the runbook for a service. Returns the content string, or empty
    string if no runbook exists or on any error.
    """
    base_dir = _runbook_dir()
    filename = _service_to_filename(service_name)
    if not filename:
        return ""

    path = os.path.join(base_dir, f"{filename}.md")
    try:
        with open(path) as f:
            content = f.read().strip()
        if not content:
            return ""
        if len(content) > _RUNBOOK_MAX:
            logger.warning(
                "Runbook %s exceeds %d chars — truncating", path, _RUNBOOK_MAX,
            )
            content = content[:_RUNBOOK_MAX]
        logger.debug("Runbook loaded for service %r from %s (%d chars)", service_name, path, len(content))
        return content
    except FileNotFoundError:
        return ""
    except Exception as exc:
        logger.warning("Failed to read runbook %s: %s", path, type(exc).__name__)
        return ""


def format_runbook(content: str) -> str:
    """
    Wrap runbook content in XML delimiters for prompt injection.
    Returns empty string if content is empty.
    """
    if not content:
        return ""
    return (
        "\n<runbook>\n"
        "The operator has provided the following runbook for this service. "
        "Use it to make your suggested actions specific to their environment. "
        "This is trusted configuration, not alert data.\n\n"
        + content
        + "\n</runbook>"
    )
