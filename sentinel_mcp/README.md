# Homelab AI Sentinel — MCP Server

Query your running Sentinel instance from Claude, ChatGPT, and any MCP-compatible AI assistant.

## What it does

The MCP server exposes three tools:

| Tool | What it returns |
|------|----------------|
| `sentinel_health` | Operational state — DB connectivity, alert counts, AI rate limit usage, DLQ depth |
| `sentinel_alerts` | Recent alerts with service name, severity, AI insight, and suggested actions |
| `sentinel_incidents` | Open or resolved incidents with alert count and AI summary |

Once installed, you can ask Claude things like:
- *"What alerts has Sentinel fired in the last hour?"*
- *"Are there any open incidents?"*
- *"Is nginx having issues today?"*
- *"What's the most critical alert right now?"*

## Requirements

- Homelab AI Sentinel v2.0+ running and reachable over HTTP
- Python 3.10+
- `pip install mcp httpx`

## Install

```bash
pip install homelab-ai-sentinel-mcp
```

Or with uv:
```bash
uv add homelab-ai-sentinel-mcp
```

## Configure

Two environment variables:

| Variable | Required | Description |
|----------|----------|-------------|
| `SENTINEL_URL` | Yes | Base URL of your Sentinel instance, e.g. `http://192.168.1.x:5000` |
| `SENTINEL_TOKEN` | Only if `WEBHOOK_SECRET` is set in Sentinel | Your `WEBHOOK_SECRET` value |

## Add to Claude Desktop

Edit `~/.claude/claude_desktop_config.json`:

```json
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
```

Restart Claude Desktop. The Sentinel tools appear automatically in the tools panel.

## Add to Claude Code

Add to `~/.claude/settings.json`:

```json
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
```

## Sentinel-side setup

The MCP endpoints (`/api/mcp/*`) are enabled automatically when Sentinel v2.0+ starts. No extra config required.

If `WEBHOOK_SECRET` is set in your Sentinel `.secrets.env`, set the same value as `SENTINEL_TOKEN` in the MCP server config.

To verify the endpoints are live:

```bash
# No auth (WEBHOOK_SECRET not set)
curl http://your-host:5000/api/mcp/health

# With auth
curl -H "Authorization: Bearer your_secret" http://your-host:5000/api/mcp/health
```

## Run directly

```bash
SENTINEL_URL=http://localhost:5000 python -m sentinel_mcp.server
```

## Smithery / MCPT / OpenTools

The server is compatible with all major MCP registries. To list it:

**Smithery** (`smithery.ai`): Submit via the Smithery CLI pointing to this repo.  
**MCPT**: Add `sentinel_mcp/pyproject.toml` details to the MCPT registry submission.  
**OpenTools**: Submit the GitHub repo URL.
