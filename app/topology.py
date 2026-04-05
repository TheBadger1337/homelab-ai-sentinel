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
        uses: [storage_array_01]
        host: node2
        description: Reverse proxy for all web services

      postgres:
        depends_on: [docker]
        host: node1
        description: Primary database

      nextcloud:
        depends_on: [nginx, postgres, redis]
        uses: [storage_array_01]
        host: node1

    shared_resources:
      storage_array_01:
        type: storage
        description: TrueNAS CIFS share on node3

      vlan_10:
        type: network
        description: IoT VLAN - 192.168.10.0/24

The module derives "depended_by" relationships automatically — you only
need to declare depends_on. When multiple services share a resource
(via "uses"), the AI can correlate simultaneous failures to the shared
resource even if the resource itself didn't send a webhook.

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
    Load and parse topology.yaml. Returns a dict with keys:
      "services":         {name: {depends_on: [...], uses: [...], ...}}
      "shared_resources": {name: {type: str, description: str}}

    Returns empty dict on any error.
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

    # Normalize: ensure depends_on and uses are always lists of strings
    for svc_name, svc_data in list(services.items()):
        if not isinstance(svc_data, dict):
            services[svc_name] = {"depends_on": [], "uses": []}
            continue
        for list_key in ("depends_on", "uses"):
            val = svc_data.get(list_key, [])
            if isinstance(val, str):
                svc_data[list_key] = [val]
            elif not isinstance(val, list):
                svc_data[list_key] = []
            else:
                svc_data[list_key] = [str(d) for d in val]

    # Parse shared_resources (optional section)
    shared = data.get("shared_resources", {})
    if not isinstance(shared, dict):
        shared = {}
    for res_name, res_data in list(shared.items()):
        if not isinstance(res_data, dict):
            shared[res_name] = {"type": "unknown", "description": ""}

    resource_count = len(shared)
    logger.info(
        "Topology loaded from %s (%d services, %d shared resources)",
        path, len(services), resource_count,
    )
    result = {"services": services, "shared_resources": shared}
    _cached = result
    return result


def _derive_depended_by(services: dict, service_name: str) -> list[str]:
    """Find all services that list service_name in their depends_on."""
    key = service_name.lower()
    return sorted(
        svc
        for svc, data in services.items()
        if isinstance(data, dict)
        and key in [d.lower() for d in data.get("depends_on", [])]
    )


def _find_shared_resources(
    services: dict, shared_resources: dict, service_name: str,
) -> list[str]:
    """Find shared resources used by this service and list co-users.

    Returns lines like:
      'Shares "storage_array_01" (storage: TrueNAS share) with: nginx, plex'
    """
    key_lower = service_name.lower()

    # Find which resources this service uses
    uses: list[str] = []
    for svc_name, svc_data in services.items():
        if svc_name.lower() == key_lower and isinstance(svc_data, dict):
            uses = svc_data.get("uses", [])
            break

    if not uses:
        return []

    lines = []
    for resource_name in uses:
        res_lower = resource_name.lower()
        # Find other services that use the same resource
        co_users = sorted(
            svc for svc, data in services.items()
            if isinstance(data, dict)
            and svc.lower() != key_lower
            and res_lower in [u.lower() for u in data.get("uses", [])]
        )

        res_info = shared_resources.get(resource_name, {})
        res_type = res_info.get("type", "unknown") if isinstance(res_info, dict) else "unknown"
        res_desc = res_info.get("description", "") if isinstance(res_info, dict) else ""

        label = f"{resource_name} ({res_type}"
        if res_desc:
            label += f": {res_desc}"
        label += ")"

        if co_users:
            lines.append(f'Shares "{label}" with: {", ".join(co_users)}')
            lines.append(
                f"If {resource_name} is degraded, all users are affected: "
                f"{service_name}, {', '.join(co_users)}"
            )
        else:
            lines.append(f'Uses resource "{label}" (no other services share it)')

    return lines


def get_topology(service_name: str) -> str:
    """
    Return topology context for a service as a formatted string.
    Returns empty string if no topology is loaded or the service is unknown.
    """
    topo = _load_topology()
    if not topo:
        return ""

    services = topo.get("services", {})
    shared_resources = topo.get("shared_resources", {})
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

    # Shared resources
    resource_lines = _find_shared_resources(services, shared_resources, match_key)
    lines.extend(resource_lines)

    if not depends_on and not depended_by and not resource_lines:
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
