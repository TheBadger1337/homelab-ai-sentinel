# Homelab AI Sentinel

**AI-powered alert enrichment for your homelab — turns raw monitoring webhooks into actionable Discord notifications.**

![Python](https://img.shields.io/badge/python-3.12-blue)
![Docker](https://img.shields.io/badge/docker-ready-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![AI](https://img.shields.io/badge/AI-Gemini%202.5%20Flash-orange)

---

## What is this?

Homelab AI Sentinel is a small Flask service that sits between your monitoring tools (like Uptime Kuma or Grafana) and your Discord server. When a service goes down or throws an alert, Sentinel receives the notification, sends it to Google's Gemini AI for analysis, and posts a rich Discord embed with:

- What the alert means (2–3 sentence AI diagnosis)
- Suggested actions (up to 5 concrete steps to investigate or fix the problem)
- Color-coded severity: red for critical, yellow for warning, green for recovery, grey for unknown

**What is a webhook?** A webhook is just an HTTP POST request that one service sends to another when something happens. When Uptime Kuma detects your Nginx is down, it POSTs a JSON payload to a URL you configure. Sentinel is the service listening at that URL.

**Why AI enrichment?** Raw alerts tell you *what* happened. Sentinel uses Gemini to help you understand *why* it probably happened and what to check first — especially useful at 2am when your NAS goes offline and you can't remember where the relevant logs live.

---

## Architecture

```
┌──────────────────┐         ┌───────────────────────────────────────┐
│  Uptime Kuma /   │         │         Homelab AI Sentinel            │
│  Grafana /       │─POST──▶ │  POST /webhook                        │
│  curl / etc.     │  JSON   │    ├─ alert_parser.py  (normalize)    │
└──────────────────┘         │    ├─ claude_client.py (Gemini call)  │
                             │    └─ discord_client.py (embed post)  │
                             └──────────┬──────────────┬─────────────┘
                                        │              │
                                        ▼              ▼
                               ┌────────────┐  ┌──────────────┐
                               │ Gemini API │  │   Discord    │
                               │ (free tier)│  │   Webhook    │
                               └────────────┘  └──────────────┘
```

---

## Quick Start

**Step 1: Create your secrets file.**

```bash
cp .secrets.env.example .secrets.env
```

If `.secrets.env.example` doesn't exist yet, create `.secrets.env` manually:

```bash
touch .secrets.env
```

**Step 2: Fill in your keys.**

```env
GEMINI_TOKEN=your_google_ai_studio_key_here
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
```

See [How to Get Your Keys](#how-to-get-your-keys) below if you don't have these yet.

**Step 3: Start the service.**

```bash
docker compose up -d
```

Sentinel is now listening on port 5000. Test it:

```bash
curl -s http://localhost:5000/health
# {"status": "ok"}
```

Send a test alert:

```bash
curl -s -X POST http://localhost:5000/webhook \
  -H "Content-Type: application/json" \
  -d '{"service": "nginx", "status": "down", "message": "Connection refused on port 80"}'
```

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Docker + Docker Compose | Docker Desktop or the standalone `docker compose` plugin |
| Google AI Studio account | Free — no billing required for Gemini 2.5 Flash on the free tier |
| Discord server + webhook URL | You need Manage Webhooks permission in at least one channel |

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `GEMINI_TOKEN` | Yes | — | Google AI Studio API key. Used to call `gemini-2.5-flash`. |
| `DISCORD_WEBHOOK_URL` | Yes | — | Full Discord webhook URL for the target channel. |
| `PORT` | No | `5000` | Port the Flask/gunicorn server binds to inside the container. |
| `DISCORD_DISABLED` | No | `false` | Set to `true` to suppress all Discord posts. Useful for testing. |

These variables are loaded from `.secrets.env` by `docker-compose.yml`. If you run Sentinel without Docker, you can use a `.env` file in the project root — `python-dotenv` will pick it up automatically via `main.py`.

---

## How to Get Your Keys

### GEMINI_TOKEN (Google AI Studio)

1. Go to [aistudio.google.com](https://aistudio.google.com)
2. Sign in with a Google account
3. Click **"Get API key"** in the left sidebar
4. Click **"Create API key"**
5. Copy the key — it starts with `AIza...`
6. Paste it as `GEMINI_TOKEN` in `.secrets.env`

The free tier is sufficient for homelab alerting volumes. There is no billing required.

### DISCORD_WEBHOOK_URL

1. Open Discord and go to the server where you want alerts posted
2. Right-click the target channel → **Edit Channel**
3. Go to **Integrations** → **Webhooks** → **New Webhook**
4. Give it a name (e.g., "Homelab Sentinel") and optionally set an avatar
5. Click **Copy Webhook URL**
6. Paste it as `DISCORD_WEBHOOK_URL` in `.secrets.env`

The URL format looks like: `https://discord.com/api/webhooks/1234567890/AbCdEfGh...`

---

## Supported Alert Sources

### Uptime Kuma

Detected automatically when the payload contains both `heartbeat` and `monitor` fields. Uptime Kuma's native webhook format is fully supported.

**Status mapping:**
- `heartbeat.status = 0` → `down` / `critical`
- `heartbeat.status = 1` → `up` / `info`

**Example payload (sent by Uptime Kuma):**

```json
{
  "heartbeat": {
    "status": 0,
    "time": "2026-03-24T03:00:00.000Z",
    "msg": "No response - Connection refused",
    "ping": null
  },
  "monitor": {
    "id": 12,
    "name": "Nginx Proxy Manager",
    "url": "http://192.168.1.10:81",
    "type": "http"
  },
  "msg": "Nginx Proxy Manager is down"
}
```

### Generic JSON

Any JSON payload that does not match the Uptime Kuma format is parsed with best-effort field mapping. You don't need to match an exact schema — Sentinel looks for common field names.

**Status field detection** (checks `status`, `state`, or `alertstate`):

| Value | Mapped to |
|---|---|
| `"firing"`, `"error"`, `"down"`, `"0"`, `"false"`, `"critical"` | `down` / critical |
| `"ok"`, `"resolved"`, `"up"`, `"1"`, `"true"`, `"normal"` | `up` / info |
| `"warning"`, `"warn"`, `"degraded"` | warning |
| anything else | unknown / warning |

**Service name detection** (checks in order): `service`, `name`, `host`, `source`

**Message detection** (checks in order): `message`, `msg`, `description`, `text`

**Example generic payload:**

```json
{
  "service": "postgres",
  "status": "warning",
  "message": "Connection pool at 87% capacity",
  "host": "db-server-01",
  "pool_size": 100,
  "active_connections": 87
}
```

All fields not used for status/service/message are passed to Gemini as additional context, so the more detail you include, the better the AI analysis.

---

## Example Discord Output

When Sentinel processes an alert, the Discord embed looks like this:

```
🔴 [CRITICAL] Nginx Proxy Manager — DOWN
─────────────────────────────────────────
Alert Message
  No response - Connection refused

Source            Severity
  Uptime Kuma       Critical

🤖 AI Insight
  Nginx Proxy Manager at 192.168.1.10:81 is not accepting TCP connections
  on port 81. This typically means the container has crashed, the host
  machine lost power, or a firewall rule changed. The null ping value
  confirms the connection is being refused at the transport layer.

⚡ Suggested Actions
  • SSH to 192.168.1.10 and run: docker ps | grep nginx
  • Check container logs: docker logs nginx-proxy-manager --tail 50
  • Verify port 81 is accessible: nc -zv 192.168.1.10 81
  • Check host-level firewall: sudo ufw status
  • Restart the container if logs show a crash: docker restart nginx-proxy-manager

                        Homelab AI Sentinel  •  2026-03-24 03:00:01 UTC
```

The embed border is color-coded:
- Red (`#ED4245`) for critical / down
- Yellow (`#FEE75C`) for warning / degraded
- Green (`#57F287`) for info / recovered
- Grey (`#99AAB5`) for unknown

> **Screenshot placeholder** — add a screenshot of a real Discord embed here after first deployment.

---

## Advanced Configuration

### Testing Without Discord

Set `DISCORD_DISABLED=true` in your `.secrets.env` to suppress all Discord posts. The webhook endpoint still processes alerts, calls Gemini, and returns the full JSON response — it just won't send anything to Discord. This is useful for validating payloads and testing Gemini integration without spamming your channel.

```env
DISCORD_DISABLED=true
```

Test with curl and inspect the returned JSON:

```bash
curl -s -X POST http://localhost:5000/webhook \
  -H "Content-Type: application/json" \
  -d '{"service": "redis", "status": "down", "message": "OOM killer triggered"}' \
  | python3 -m json.tool
```

### Port Override

To run Sentinel on a different port (e.g., if 5000 is taken by another service), set `PORT` in `.secrets.env` and update the port mapping in `docker-compose.yml`:

```env
PORT=5050
```

```yaml
# docker-compose.yml
ports:
  - "5050:5050"
```

### Running Without Docker

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Create a .env file (python-dotenv loads this automatically)
cat > .env <<EOF
GEMINI_TOKEN=your_key
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
EOF

python main.py
```

### Health Check

The `/health` endpoint is used by Docker's built-in healthcheck (configured in `docker-compose.yml`). You can also use it for external uptime monitoring — point Uptime Kuma at `http://your-host:5000/health` to monitor the sentinel itself.

---

## Connecting Uptime Kuma

1. In Uptime Kuma, go to **Settings** → **Notifications**
2. Click **Setup Notification**
3. Set the notification type to **Webhook**
4. Give it a name: `Homelab AI Sentinel`
5. Set the **Post URL** to: `http://your-sentinel-host:5000/webhook`
6. Leave **Request Body** as the default (Uptime Kuma's native format is auto-detected)
7. Click **Test** to send a test notification, then **Save**
8. On each monitor you want Sentinel to cover, go to the monitor's settings and enable this notification

**If Sentinel and Uptime Kuma are on the same Docker network**, use the container name instead of `localhost`:

```
http://homelab-ai-sentinel:5000/webhook
```

Add both services to the same Docker network in your `docker-compose.yml`:

```yaml
# In your uptime-kuma docker-compose.yml or a shared compose file:
networks:
  monitoring:
    external: true

services:
  uptime-kuma:
    networks:
      - monitoring
  sentinel:
    networks:
      - monitoring
```

Then create the network once: `docker network create monitoring`

---

## Connecting Other Tools

### Grafana

1. In Grafana, go to **Alerting** → **Contact points** → **Add contact point**
2. Set the integration type to **Webhook**
3. Set the URL to `http://your-sentinel-host:5000/webhook`
4. Grafana sends an `alertstate` field (`"firing"` or `"ok"`) which Sentinel's generic parser handles automatically

For more detailed analysis, customize the Grafana webhook body template to include `service`, `message`, and any relevant labels.

### Generic curl Example

Any script, cron job, or monitoring tool can POST to Sentinel:

```bash
#!/bin/bash
# Example: alert Sentinel when a disk exceeds 90% usage
USAGE=$(df / | awk 'NR==2 {print $5}' | tr -d '%')

if [ "$USAGE" -gt 90 ]; then
  curl -s -X POST http://localhost:5000/webhook \
    -H "Content-Type: application/json" \
    -d "{
      \"service\": \"disk-root\",
      \"status\": \"warning\",
      \"message\": \"Root disk at ${USAGE}% capacity\",
      \"disk_usage_pct\": ${USAGE},
      \"host\": \"$(hostname)\"
    }"
fi
```

### Generic Python Example

```python
import requests

requests.post("http://localhost:5000/webhook", json={
    "service": "backup-job",
    "status": "error",
    "message": "Nightly backup failed: rsync exit code 23",
    "target": "/mnt/nas/backups",
    "exit_code": 23,
})
```

---

## Switching AI Providers

The AI integration lives entirely in `app/claude_client.py` (the filename is a historical artifact — it currently calls Gemini). To swap providers:

### Switch to Claude (Anthropic)

1. Install the SDK: add `anthropic>=0.25` to `requirements.txt`
2. Replace the contents of `app/claude_client.py` with an implementation using `anthropic.Anthropic().messages.create()`
3. Change the environment variable from `GEMINI_TOKEN` to `ANTHROPIC_API_KEY`
4. The rest of the system is unchanged — `get_ai_insight()` just needs to return `{"insight": str, "suggested_actions": list[str]}`

### Switch to OpenAI

Same pattern: replace the requests call in `claude_client.py` with the `openai` SDK, point it at `gpt-4o` or similar, and update the env var name.

### Modify the Prompt

The system prompt and user prompt template are at the top of `app/claude_client.py` as `_SYSTEM_PROMPT` and `_USER_TEMPLATE`. Edit these directly to change the AI's persona, response format, or the fields it receives. The response schema (JSON with `insight` and `suggested_actions`) is enforced by the prompt — if you change the schema, update `app/discord_client.py` accordingly to handle new fields.

---

## Code Structure for Extension

```
app/
├── __init__.py        # Flask app factory, registers blueprints
├── webhook.py         # POST /webhook route — orchestrates the pipeline
├── alert_parser.py    # Format detection + normalization → NormalizedAlert
├── claude_client.py   # AI provider integration → {insight, suggested_actions}
└── discord_client.py  # Discord embed builder + poster
main.py                # Entry point, loads .env, creates WSGI app
```

**Adding a new alert source parser:**

1. Add a `_is_yourformat(data: dict) -> bool` detection function in `alert_parser.py`
2. Add a `_parse_yourformat(data: dict) -> NormalizedAlert` parser
3. Add a branch in `parse_alert()`: `if _is_yourformat(data): return _parse_yourformat(data)`
4. Set `source="your_format"` in the returned `NormalizedAlert`

**Adding a new notification target (Slack, Teams, PagerDuty):**

1. Create `app/slack_client.py` (or similar) with a `post_alert(alert, ai) -> None` function
2. Import and call it in `app/webhook.py` alongside the existing `discord_client.post_alert()` call
3. Add the relevant env var(s) and document them in the table above

**Changing severity thresholds:**

Status-to-severity mapping for Uptime Kuma is in `_uptime_kuma_status()` in `alert_parser.py`. Generic mapping is in the `if/elif` block inside `_parse_generic()`. Both return `(status, severity)` tuples that flow through to Discord embed color selection.

---

## Roadmap

- Additional alert sources: Prometheus Alertmanager native format, Zabbix, Checkmk
- Slack and Microsoft Teams notification targets
- Alert deduplication — suppress repeat alerts for the same service within a configurable window
- Severity thresholds — configurable per-service: suppress info-level Discord posts for noisy monitors
- Persistent alert log — SQLite or flat file for alert history and audit trail
- Web UI — minimal dashboard showing recent alerts and AI insights

---

## License

MIT. See LICENSE file or [opensource.org/licenses/MIT](https://opensource.org/licenses/MIT).
