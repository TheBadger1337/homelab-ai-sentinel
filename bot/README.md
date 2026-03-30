# Sentinel Discord Bot

Minimal Discord bot that connects to any OpenAI-compatible AI backend.

Responds to messages, maintains conversation context, and resets on `!clear`.

## Setup

```bash
pip install discord.py aiohttp python-dotenv
```

Create `.secrets.env` in this directory:

```env
BOT_TOKEN=your-discord-bot-token
BOT_OWNER_ID=your-discord-user-id
AI_URL=http://127.0.0.1:11434
AI_TOKEN=
AI_MODEL=llama3
BOT_NAME=Assistant
ALLOWED_CHANNEL_ID=0
```

- `AI_URL` — any OpenAI-compatible endpoint (Ollama, LM Studio, OpenAI, etc.)
- `AI_TOKEN` — leave blank for Ollama; required for OpenAI/hosted providers
- `BOT_OWNER_ID` — your Discord user ID (right-click your name with Developer Mode on). Leave `0` to allow all users.
- `ALLOWED_CHANNEL_ID` — restrict to one channel. Leave `0` for all channels.

## Run

```bash
python bot.py
```

## Commands

| Command | Description |
|---------|-------------|
| `!clear` | Reset conversation history |
| `!help` | Show available commands |
| *(any other message)* | Sent to the AI |

## Full-featured version

The full bot (voice, weather, web search, personas, task management, Claude Code bridge) is documented in `docs/ops/platforms/discord.md`.
