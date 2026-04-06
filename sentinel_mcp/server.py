"""
Homelab AI Sentinel MCP Server

Lets Claude, ChatGPT, and any MCP-compatible AI assistant query a running
Sentinel instance — check operational health, browse recent alerts, and
inspect open incidents.

Usage:
    # Install
    pip install mcp httpx

    # Configure (add to your .env or shell)
    export SENTINEL_URL=http://your-host:5000
    export SENTINEL_TOKEN=your_webhook_secret   # optional — omit if WEBHOOK_SECRET unset

    # Run
    python -m sentinel_mcp.server

    # Or via uvx for Claude Desktop / Smithery:
    uvx --from homelab-ai-sentinel-mcp sentinel-mcp

Claude Desktop config (~/.claude/claude_desktop_config.json):
    {
      "mcpServers": {
        "homelab-sentinel": {
          "command": "uvx",
          "args": ["--from", "homelab-ai-sentinel-mcp", "sentinel-mcp"],
          "env": {
            "SENTINEL_URL": "http://192.168.1.x:5000",
            "SENTINEL_TOKEN": "your_webhook_secret"
          }
        }
      }
    }
"""

import os
import sys

import httpx
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SENTINEL_URL = os.environ.get("SENTINEL_URL", "http://localhost:5000").rstrip("/")
SENTINEL_TOKEN = os.environ.get("SENTINEL_TOKEN", "").strip()

mcp = FastMCP(
    "Homelab AI Sentinel",
    instructions=(
        "Query a running Homelab AI Sentinel instance. "
        "Use sentinel_health first to confirm connectivity, "
        "then sentinel_alerts or sentinel_incidents as needed."
    ),
)


def _headers() -> dict[str, str]:
    h = {"Accept": "application/json"}
    if SENTINEL_TOKEN:
        h["Authorization"] = f"Bearer {SENTINEL_TOKEN}"
    return h


def _get(path: str, params: dict | None = None) -> dict:
    """GET from Sentinel MCP API. Raises on non-2xx."""
    url = f"{SENTINEL_URL}{path}"
    try:
        r = httpx.get(url, headers=_headers(), params=params, timeout=10.0)
        r.raise_for_status()
        return r.json()
    except httpx.ConnectError:
        raise RuntimeError(
            f"Cannot reach Sentinel at {SENTINEL_URL}. "
            "Check SENTINEL_URL and that Sentinel is running."
        )
    except httpx.HTTPStatusError as exc:
        body = exc.response.text[:200]
        raise RuntimeError(
            f"Sentinel returned HTTP {exc.response.status_code}: {body}"
        )


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def sentinel_health() -> dict:
    """
    Check if Homelab AI Sentinel is running and return its operational state.

    Returns status, DB connectivity, recent alert counts, AI rate limit usage,
    and dead-letter queue depth. Use this first to confirm the instance is
    reachable before calling other tools.
    """
    return _get("/api/mcp/health")


@mcp.tool()
def sentinel_alerts(
    limit: int = 20,
    severity: str = "",
    since: int = 0,
) -> dict:
    """
    Return recent alerts processed by Sentinel.

    Args:
        limit:    Maximum number of alerts to return (1–100, default 20).
        severity: Filter by severity level — "critical", "warning", or "info".
                  Leave empty to return all severities.
        since:    Unix timestamp. Only return alerts newer than this value.
                  Use 0 (default) for no time filter.

    Returns a list of alerts with service name, severity, AI insight, and
    suggested actions. Requires Sentinel to be running with DB enabled.
    """
    params: dict = {"limit": max(1, min(limit, 100))}
    if severity:
        params["severity"] = severity
    if since:
        params["since"] = since
    return _get("/api/mcp/alerts", params)


@mcp.tool()
def sentinel_incidents(
    status: str = "open",
    limit: int = 10,
) -> dict:
    """
    Return incidents tracked by Sentinel's incident engine.

    Args:
        status: "open" (default) — active incidents only.
                "resolved" — recently resolved incidents.
                "all" — both open and resolved.
        limit:  Maximum number of incidents to return (1–50, default 10).

    Returns incidents with title, severity, alert count, open/resolved
    timestamps, and AI-generated resolution summary (for resolved incidents).
    Requires Sentinel to be running with DB enabled.
    """
    params: dict = {
        "status": status if status in ("open", "resolved", "all") else "open",
        "limit": max(1, min(limit, 50)),
    }
    return _get("/api/mcp/incidents", params)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    if not SENTINEL_URL:
        print("ERROR: SENTINEL_URL not set", file=sys.stderr)
        sys.exit(1)
    mcp.run()


if __name__ == "__main__":
    main()
