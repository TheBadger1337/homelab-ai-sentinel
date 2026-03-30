"""
Homelab AI Sentinel — Discord Bot
Minimal reference bot. Connects to any OpenAI-compatible backend
(Ollama, LM Studio, OpenAI, or any OpenAI-compatible API).

Full-featured version with voice, weather, web search, personas,
and Claude Code bridge integration is covered in the setup guides.

Dependencies: pip install discord.py aiohttp python-dotenv
"""

from dotenv import load_dotenv
import os, asyncio
import aiohttp
import discord

# ── Config ────────────────────────────────────────────────────────────────────
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(dotenv_path=os.path.join(_SCRIPT_DIR, ".secrets.env"))

BOT_TOKEN          = os.getenv("BOT_TOKEN")
BOT_OWNER_ID       = int(os.getenv("BOT_OWNER_ID", 0))
AI_URL             = os.getenv("AI_URL", "http://127.0.0.1:11434").rstrip("/")
AI_TOKEN           = os.getenv("AI_TOKEN", "")
AI_MODEL           = os.getenv("AI_MODEL", "llama3")
AI_MAX_TOKENS      = int(os.getenv("AI_MAX_TOKENS", 4096))
BOT_NAME           = os.getenv("BOT_NAME", "Assistant")
ALLOWED_CHANNEL_ID = int(os.getenv("ALLOWED_CHANNEL_ID", 0))
BOT_PREFIX         = os.getenv("BOT_PREFIX", "!")

MAX_CONTEXT = 40   # messages before oldest are dropped

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set — check .secrets.env")

# ── State ─────────────────────────────────────────────────────────────────────
_context: list[dict] = []
_context_lock = asyncio.Lock()

SYSTEM_PROMPT = (
    f"You are {BOT_NAME}, a helpful AI assistant running in a Discord server. "
    "Keep responses concise and direct."
)

# ── Security ──────────────────────────────────────────────────────────────────
def _is_owner(user_id: int) -> bool:
    if BOT_OWNER_ID and user_id != BOT_OWNER_ID:
        return False
    return True

# ── AI request ────────────────────────────────────────────────────────────────
async def _ask(messages: list[dict]) -> str:
    headers = {"Content-Type": "application/json"}
    if AI_TOKEN:
        headers["Authorization"] = f"Bearer {AI_TOKEN}"

    payload = {
        "model":      AI_MODEL,
        "messages":   messages,
        "max_tokens": AI_MAX_TOKENS,
        "stream":     False,
    }

    endpoint = f"{AI_URL}/v1/chat/completions"
    async with aiohttp.ClientSession() as session:
        async with session.post(endpoint, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=120)) as resp:
            if resp.status != 200:
                body = (await resp.text())[:200]
                return f"Error: AI backend returned HTTP {resp.status} — {body}"
            data = await resp.json()
            return data["choices"][0]["message"]["content"].strip()

# ── Discord bot ───────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

@client.event
async def on_ready():
    print(f"[sentinel-bot] Logged in as {client.user} ({client.user.id})")

@client.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if not _is_owner(message.author.id):
        return
    if ALLOWED_CHANNEL_ID and message.channel.id != ALLOWED_CHANNEL_ID:
        return

    content = message.content.strip()
    if not content:
        return

    # !clear — reset conversation context
    if content.lower() in (f"{BOT_PREFIX}clear", f"{BOT_PREFIX}reset"):
        async with _context_lock:
            _context.clear()
        await message.channel.send("Context cleared.")
        return

    # !help
    if content.lower() == f"{BOT_PREFIX}help":
        await message.channel.send(
            f"**{BOT_NAME}** — commands:\n"
            f"`{BOT_PREFIX}clear` — reset conversation history\n"
            f"`{BOT_PREFIX}help` — show this message\n\n"
            "Any other message is sent to the AI."
        )
        return

    # Ignore other ! commands
    if content.startswith(BOT_PREFIX):
        return

    async with message.channel.typing():
        async with _context_lock:
            _context.append({"role": "user", "content": content})

            # Trim to MAX_CONTEXT (keep most recent messages)
            if len(_context) > MAX_CONTEXT:
                del _context[:len(_context) - MAX_CONTEXT]

            messages = [{"role": "system", "content": SYSTEM_PROMPT}] + list(_context)

        reply = await _ask(messages)

        async with _context_lock:
            _context.append({"role": "assistant", "content": reply})

        # Discord has a 2000 character message limit
        if len(reply) <= 2000:
            await message.channel.send(reply)
        else:
            for i in range(0, len(reply), 1990):
                await message.channel.send(reply[i:i + 1990])

client.run(BOT_TOKEN)
