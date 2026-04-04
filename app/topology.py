"""
Service topology mapping for AI prompts.

Loads a YAML dependency graph so the AI can reason about cascade failures
and upstream/downstream impact when a service alerts.

File location (first match wins):
  1. TOPOLOGY_FILE env var — explicit path to topology.yaml
  2. {RUNBOOK_DIR}/topology.yaml — alongside runbooks (default: /data/runbooks/)

Expected YAML format:

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

The module derives "depended_by" relationships automatically — you only
need to declare depends_on.

Cache: the file is parsed once on first call and cached for the process
lifetime. Restart the container to pick up changes.

Failure policy: returns empty string on any error. The AI call proceeds
without topology context.
"""

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

_TOPOLOGY_MAX = 1500

# ---------------------------------------------------------------------------
# Cache — loaded once on first call
# ---------------------------------------------------------------------------
_UNLOADED = object()
_cached: Any = _UNLOADED


def _topology_path() -> str:
    """Return the resolved path to topology.yaml."""
    explicit = os.environ.get("TOPOLOGY_FILE", "").strip()
    if explicit:
        return explicit
    runbook_dir = os.environ.get("RUNBOOK_DIR", "/data/runbooks")
    return os.path.join(runbook_dir, "topology.yaml")


def _load_topology() -> dict:
    """
    Load and parse topology.yaml. Returns the full services dict, or
    empty dict on any error.
    """
    global _cached
    if _cached is not _UNLOADED:
        return _cached  # type: ignore[return-value]

    if yaml is None:
        logger.info("PyYAML not installed — topology mapping disabled")
        _cached = {}
        return {}

    path = _topology_path()
    try:
        with open(path) as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        logger.debug("No topology file at %s — skipping", path)
        _cached = {}
        return {}
    except Exception as exc:
        logger.warning("Failed to read topology %s: %s", path, type(exc).__name__)
        _cached = {}
        return {}

    if not isinstance(data, dict):
        logger.warning("Topology file %s is not a YAML mapping — skipping", path)
        _cached = {}
        return {}

    services = data.get("services", {})
    if not isinstance(services, dict):
        logger.warning("Topology 'services' key is not a mapping — skipping")
        _cached = {}
        return {}

    # Normalize: ensure depends_on is always a list of strings
    for svc_name, svc_data in list(services.items()):
        if not isinstance(svc_data, dict):
            services[svc_name] = {"depends_on": []}
            continue
        deps = svc_data.get("depends_on", [])
        if isinstance(deps, str):
            svc_data["depends_on"] = [deps]
        elif not isinstance(deps, list):
            svc_data["depends_on"] = []
        else:
            svc_data["depends_on"] = [str(d) for d in deps]

    logger.info("Topology loaded from %s (%d services)", path, len(services))
    _cached = services
    return services


def _derive_depended_by(services: dict, service_name: str) -> list[str]:
    """Find all services that list service_name in their depends_on."""
    key = service_name.lower()
    return sorted(
        svc
        for svc, data in services.items()
        if isinstance(data, dict)
        and key in [d.lower() for d in data.get("depends_on", [])]
    )


def get_topology(service_name: str) -> str:
    """
    Return topology context for a service as a formatted string.
    Returns empty string if no topology is loaded or the service is unknown.
    """
    services = _load_topology()
    if not services:
        return ""

    # Case-insensitive lookup
    key_lower = service_name.lower()
    match = None
    match_key = None
    for svc_name, svc_data in services.items():
        if svc_name.lower() == key_lower:
            match = svc_data
            match_key = svc_name
            break

    if match is None:
        # Even if the service isn't in the topology, check if anything
        # depends on it — it might be an infrastructure component
        depended_by = _derive_depended_by(services, service_name)
        if not depended_by:
            return ""
        # Service isn't declared but is referenced as a dependency
        lines = [f'Service "{service_name}" is referenced as a dependency.']
        lines.append(f"Depended on by: {', '.join(depended_by)}")
        lines.append(
            f"If {service_name} is down, these services are likely affected: "
            f"{', '.join(depended_by)}"
        )
        result = "\n".join(lines)
        return result[:_TOPOLOGY_MAX]

    depends_on = match.get("depends_on", [])
    assert match_key is not None  # guaranteed by the loop above
    depended_by = _derive_depended_by(services, match_key)
    host = match.get("host", "")
    description = match.get("description", "")

    lines = []
    # Header
    header = f'Service "{match_key}"'
    if host:
        header += f" runs on {host}"
    if description:
        header += f" ({description})"
    header += "."
    lines.append(header)

    # Dependencies
    if depends_on:
        lines.append(f"Depends on: {', '.join(depends_on)}")
        # Add host info for dependencies if available
        dep_details = []
        for dep in depends_on:
            dep_data = services.get(dep, {})
            if isinstance(dep_data, dict) and dep_data.get("host"):
                dep_details.append(f"{dep} ({dep_data['host']})")
        if dep_details:
            lines.append(f"Dependency hosts: {', '.join(dep_details)}")

    if depended_by:
        lines.append(f"Depended on by: {', '.join(depended_by)}")
        lines.append(
            f"If {match_key} is down, these services are likely affected: "
            f"{', '.join(depended_by)}"
        )

    if not depends_on and not depended_by:
        lines.append("No declared dependencies or dependents.")

    result = "\n".join(lines)
    return result[:_TOPOLOGY_MAX]


def format_topology(content: str) -> str:
    """
    Wrap topology content in XML delimiters for prompt injection.
    Returns empty string if content is empty.
    """
    if not content:
        return ""
    return (
        "\n<topology>\n"
        "The operator has defined their service dependency graph. Use this "
        "to assess cascade impact — if a dependency is down, services that "
        "depend on it are likely affected. This is trusted configuration, "
        "not alert data.\n\n"
        + content
        + "\n</topology>"
    )


def reset_cache() -> None:
    """Reset the topology cache. Used by tests."""
    global _cached
    _cached = _UNLOADED
