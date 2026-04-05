"""
Topology-based alert correlation.

When a service fires an alert, this module checks if an upstream dependency
already has an open incident. If so, the new alert is linked to that upstream
incident rather than creating a new one — reducing noise and surfacing the
root cause.

Correlation is structural (based on the topology.yaml dependency graph), not
AI-inferred. The AI is told about the correlation, not asked to determine it.
This prevents hallucinated correlations.

Limitations (v2.0-alpha):
  - Only correlates via direct depends_on relationships
  - Does NOT correlate across shared_resources (too many false positives)
  - 5-minute recency window — stale upstream incidents are ignored
  - If the alerting service is not in the topology, no correlation is attempted
"""

import logging
import time

from .alert_parser import NormalizedAlert

logger = logging.getLogger(__name__)

# Only correlate with upstream incidents created within this window (seconds).
# Prevents linking a new alert to a stale, forgotten incident.
_CORRELATION_WINDOW = 300  # 5 minutes


def _get_dependencies(service_name: str) -> list[str]:
    """
    Return the depends_on list for a service from the topology graph.

    Uses the cached topology — returns empty list if topology is not
    configured or the service is not in the graph.
    """
    # Import here to avoid circular imports (topology imports nothing from us)
    from .topology import _load_topology

    topo = _load_topology()
    if not topo:
        return []

    services = topo.get("services", {})
    # Case-insensitive lookup
    for svc_name, svc_data in services.items():
        if svc_name.lower() == service_name.lower() and isinstance(svc_data, dict):
            return [str(d) for d in svc_data.get("depends_on", [])]
    return []


def correlate_alert(
    alert: NormalizedAlert,
    open_incidents: list[dict],
) -> int | None:
    """
    Check if the alerting service depends on a service with an open incident.

    Returns the incident ID to link to, or None if no correlation found.

    Parameters:
      alert           — the incoming alert
      open_incidents  — list of open incident dicts from get_all_open_incidents()

    Only correlates when:
      1. The alerting service has depends_on entries in the topology
      2. An upstream dependency has an open incident
      3. The upstream incident was created within _CORRELATION_WINDOW seconds
    """
    if not open_incidents:
        return None

    deps = _get_dependencies(alert.service_name)
    if not deps:
        return None

    now = time.time()
    deps_lower = {d.lower() for d in deps}

    # Find the most recent open incident for any upstream dependency
    best: dict | None = None
    for inc in open_incidents:
        svc = inc.get("service", "")
        if svc.lower() in deps_lower:
            # Check recency — don't correlate with old incidents
            ts_start = inc.get("ts_start", 0)
            if (now - ts_start) > _CORRELATION_WINDOW:
                continue
            if best is None or ts_start > best.get("ts_start", 0):
                best = inc

    if best is not None:
        logger.info(
            "Correlation found: %s depends on %s (incident %d)",
            alert.service_name, best["service"], best["id"],
        )
        return best["id"]

    return None
