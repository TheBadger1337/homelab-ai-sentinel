"""
Startup configuration validator.

Runs once at app startup. Catches misconfigured env vars immediately
with clear error messages instead of letting them fail silently at
dispatch time.

Checks:
  - Partial platform configs (e.g. TELEGRAM_BOT_TOKEN set but CHAT_ID missing)
  - Invalid URL formats for webhook/API URLs
  - Bad numeric values for rate limits, thresholds, etc.
  - Weak WEBHOOK_SECRET (< 16 chars)
  - Invalid SENTINEL_MODE / AI_PROVIDER values
  - DB-dependent features configured without DB

Does NOT block startup — logs warnings so operators see exactly what's
wrong in their container logs.
"""

import logging
import os
import re
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Platform required vars + URL vars that need format validation
# ---------------------------------------------------------------------------

_PLATFORM_REQUIRED: dict[str, list[str]] = {
    "Discord":  ["DISCORD_WEBHOOK_URL"],
    "Slack":    ["SLACK_WEBHOOK_URL"],
    "Telegram": ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"],
    "Ntfy":     ["NTFY_URL"],
    "Email":    ["SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"],
    "WhatsApp": ["WHATSAPP_TOKEN", "WHATSAPP_PHONE_ID", "WHATSAPP_TO"],
    "Signal":   ["SIGNAL_API_URL", "SIGNAL_SENDER", "SIGNAL_RECIPIENT"],
    "Gotify":   ["GOTIFY_URL", "GOTIFY_APP_TOKEN"],
    "Matrix":   ["MATRIX_HOMESERVER", "MATRIX_ACCESS_TOKEN", "MATRIX_ROOM_ID"],
    "iMessage": ["IMESSAGE_URL", "IMESSAGE_PASSWORD", "IMESSAGE_TO"],
}

# Env vars that must be valid URLs when set
_URL_VARS: list[str] = [
    "DISCORD_WEBHOOK_URL",
    "SLACK_WEBHOOK_URL",
    "NTFY_URL",
    "SIGNAL_API_URL",
    "GOTIFY_URL",
    "MATRIX_HOMESERVER",
    "IMESSAGE_URL",
    "OPENAI_BASE_URL",
    "WATCHDOG_URL",
]

# Env vars that must be positive integers when set
_POSITIVE_INT_VARS: dict[str, str] = {
    "WEBHOOK_RATE_LIMIT": "rate limiting",
    "WEBHOOK_RATE_WINDOW": "rate limit window",
    "DEDUP_TTL_SECONDS": "dedup TTL",
    "COOLDOWN_SECONDS": "cooldown",
    "STORM_WINDOW": "storm buffer window",
    "STORM_THRESHOLD": "storm threshold",
    "RETENTION_DAYS": "data retention",
    "HOUSEKEEP_INTERVAL": "housekeeping interval",
    "DLQ_MAX_RETRIES": "DLQ retries",
    "AI_CONCURRENCY": "AI backpressure",
    "MAX_PROMPT_CHARS": "prompt budget",
    "GEMINI_RPM": "Gemini rate limit",
    "GEMINI_RETRIES": "Gemini retries",
    "ANTHROPIC_RPM": "Anthropic rate limit",
    "ANTHROPIC_TIMEOUT": "Anthropic timeout",
    "OPENAI_RPM": "OpenAI rate limit",
    "OPENAI_TIMEOUT": "OpenAI timeout",
    "WATCHDOG_INTERVAL": "watchdog interval",
    "ESCALATION_THRESHOLD": "escalation threshold",
    "ESCALATION_WINDOW": "escalation window",
    "SMTP_PORT": "SMTP port",
}


def _is_valid_url(url: str) -> bool:
    """Return True if the string looks like a valid http(s) URL."""
    try:
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https") and bool(parsed.hostname)
    except Exception:
        return False


def validate_config() -> list[str]:
    """
    Validate all Sentinel configuration at startup.

    Returns a list of warning messages (empty = all good).
    Each problem is also logged at WARNING level.
    """
    warnings: list[str] = []

    def warn(msg: str) -> None:
        warnings.append(msg)
        logger.warning("Config: %s", msg)

    # -----------------------------------------------------------------
    # 1. Partial platform configs — some vars set, others missing
    # -----------------------------------------------------------------
    for platform, required in _PLATFORM_REQUIRED.items():
        set_vars = [v for v in required if os.environ.get(v)]
        missing_vars = [v for v in required if not os.environ.get(v)]

        if set_vars and missing_vars:
            warn(
                f"{platform} is partially configured — "
                f"set: {', '.join(set_vars)} — "
                f"missing: {', '.join(missing_vars)}. "
                f"Platform will be skipped until all required vars are set."
            )

    # -----------------------------------------------------------------
    # 2. URL format validation
    # -----------------------------------------------------------------
    for var in _URL_VARS:
        val = os.environ.get(var, "")
        if val and not _is_valid_url(val):
            warn(
                f"{var}={val!r} is not a valid URL — "
                f"must start with http:// or https:// and have a hostname."
            )

    # -----------------------------------------------------------------
    # 3. Numeric env vars — must be non-negative integers
    # -----------------------------------------------------------------
    for var, description in _POSITIVE_INT_VARS.items():
        val = os.environ.get(var)
        if val is not None and val != "":
            try:
                n = int(val)
                if n < 0:
                    warn(f"{var}={val} is negative — {description} requires a non-negative integer.")
            except ValueError:
                warn(f"{var}={val!r} is not a valid integer — {description} will use its default value.")

    # -----------------------------------------------------------------
    # 4. WEBHOOK_SECRET strength
    # -----------------------------------------------------------------
    secret = os.environ.get("WEBHOOK_SECRET", "")
    if secret and len(secret) < 16:
        warn(
            f"WEBHOOK_SECRET is only {len(secret)} chars — "
            f"recommend at least 32 chars (openssl rand -hex 32). "
            f"Short secrets are vulnerable to brute force."
        )

    # -----------------------------------------------------------------
    # 5. SENTINEL_MODE validation
    # -----------------------------------------------------------------
    mode = os.environ.get("SENTINEL_MODE", "")
    if mode and mode.lower() not in ("minimal", "reactive", "predictive"):
        warn(
            f"SENTINEL_MODE={mode!r} is not valid — "
            f"must be minimal, reactive, or predictive. Defaulting to predictive."
        )

    # -----------------------------------------------------------------
    # 6. AI_PROVIDER validation
    # -----------------------------------------------------------------
    provider = os.environ.get("AI_PROVIDER", "")
    if provider and provider.lower() not in ("gemini", "anthropic", "openai"):
        warn(
            f"AI_PROVIDER={provider!r} is not valid — "
            f"must be gemini, anthropic, or openai. Defaulting to gemini."
        )

    # -----------------------------------------------------------------
    # 7. OpenAI provider: all three must be set together
    # -----------------------------------------------------------------
    if provider.lower() == "openai":
        openai_vars = ["OPENAI_BASE_URL", "OPENAI_API_KEY", "OPENAI_MODEL"]
        set_vars = [v for v in openai_vars if os.environ.get(v)]
        missing = [v for v in openai_vars if not os.environ.get(v)]
        if set_vars and missing:
            warn(
                f"AI_PROVIDER=openai but missing: {', '.join(missing)}. "
                f"All three (OPENAI_BASE_URL, OPENAI_API_KEY, OPENAI_MODEL) are required."
            )

    # -----------------------------------------------------------------
    # 8. QUIET_HOURS format
    # -----------------------------------------------------------------
    quiet = os.environ.get("QUIET_HOURS", "").strip()
    if quiet:
        if not re.match(r"^\d{1,2}:\d{2}-\d{1,2}:\d{2}$", quiet):
            warn(
                f"QUIET_HOURS={quiet!r} doesn't match expected format HH:MM-HH:MM. "
                f"Example: QUIET_HOURS=22:00-08:00"
            )

    # -----------------------------------------------------------------
    # 9. UI_PASSWORD strength (when set via env var)
    # -----------------------------------------------------------------
    ui_pw = os.environ.get("UI_PASSWORD", "")
    if ui_pw and len(ui_pw) < 8:
        warn(
            f"UI_PASSWORD is only {len(ui_pw)} chars — "
            f"must be at least 8 characters for security."
        )

    # -----------------------------------------------------------------
    # 10. AI provider API key presence — warn when the selected provider
    #     has no credentials, so operators don't wonder why AI is silent.
    # -----------------------------------------------------------------
    effective_provider = (provider or "gemini").lower()
    if effective_provider == "gemini" and not os.environ.get("GEMINI_TOKEN"):
        warn(
            "AI_PROVIDER=gemini (default) but GEMINI_TOKEN is not set — "
            "AI enrichment will return a fallback response for every alert."
        )
    elif effective_provider == "anthropic" and not os.environ.get("ANTHROPIC_API_KEY"):
        warn(
            "AI_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set — "
            "AI enrichment will return a fallback response for every alert."
        )

    # -----------------------------------------------------------------
    # 11. AI_PROVIDER_FALLBACK — must be a valid provider and differ from primary
    # -----------------------------------------------------------------
    fallback = os.environ.get("AI_PROVIDER_FALLBACK", "").lower()
    if fallback:
        valid_providers = ("gemini", "anthropic", "openai")
        if fallback not in valid_providers:
            warn(
                f"AI_PROVIDER_FALLBACK={fallback!r} is not valid — "
                f"must be gemini, anthropic, or openai. Failover will not work."
            )
        elif fallback == effective_provider:
            warn(
                f"AI_PROVIDER_FALLBACK={fallback!r} is the same as AI_PROVIDER — "
                f"failover to the same provider provides no redundancy."
            )

    # -----------------------------------------------------------------
    # 12. Severity-valued env vars — must be info, warning, or critical
    # -----------------------------------------------------------------
    _valid_severities = ("info", "warning", "critical")
    for var, default in [("MIN_SEVERITY", "info"), ("QUIET_HOURS_MIN_SEVERITY", "critical")]:
        val = os.environ.get(var, "").lower()
        if val and val not in _valid_severities:
            warn(
                f"{var}={val!r} is not valid — "
                f"must be info, warning, or critical. Defaulting to {default}."
            )

    # -----------------------------------------------------------------
    # 13. Boolean env vars — only "true" (case-insensitive) works.
    #     Common alternatives like "1", "yes", "on" are silently ignored
    #     by the code, so warn the operator immediately.
    # -----------------------------------------------------------------
    _BOOL_VARS = [
        "DB_DISABLED", "SENTINEL_DEBUG",
        "DISCORD_DISABLED", "SLACK_DISABLED", "TELEGRAM_DISABLED",
        "NTFY_DISABLED", "EMAIL_DISABLED", "WHATSAPP_DISABLED",
        "SIGNAL_DISABLED", "GOTIFY_DISABLED", "MATRIX_DISABLED",
        "IMESSAGE_DISABLED", "MORNING_BRIEF_ENABLED",
    ]
    for var in _BOOL_VARS:
        val = os.environ.get(var, "")
        if val and val.lower() != "true" and val.lower() != "false":
            warn(
                f"{var}={val!r} is not recognized — "
                f"only 'true' or 'false' (case-insensitive) are accepted. "
                f"Currently {var} is NOT active."
            )

    # -----------------------------------------------------------------
    # 14. MORNING_BRIEF_TIME format — must be HH:MM
    # -----------------------------------------------------------------
    brief_time = os.environ.get("MORNING_BRIEF_TIME", "").strip()
    if brief_time:
        if not re.match(r"^\d{1,2}:\d{2}$", brief_time):
            warn(
                f"MORNING_BRIEF_TIME={brief_time!r} doesn't match expected format HH:MM. "
                f"Example: MORNING_BRIEF_TIME=07:00"
            )
        else:
            try:
                h, m = int(brief_time.split(":")[0]), int(brief_time.split(":")[1])
                if not (0 <= h <= 23 and 0 <= m <= 59):
                    warn(
                        f"MORNING_BRIEF_TIME={brief_time!r} is out of range — "
                        f"hour must be 0-23, minute must be 0-59."
                    )
            except ValueError:
                pass  # already caught by regex above

    # -----------------------------------------------------------------
    # 15. REVERSE_TRIAGE_* script paths — must exist and be files
    # -----------------------------------------------------------------
    triage_prefix = "REVERSE_TRIAGE_"
    triage_skip = {"REVERSE_TRIAGE_TIMEOUT"}
    for key, val in os.environ.items():
        if key.startswith(triage_prefix) and key not in triage_skip and val:
            import pathlib
            path = pathlib.Path(val)
            if not path.is_absolute():
                warn(
                    f"{key}={val!r} is not an absolute path — "
                    f"reverse triage scripts must use absolute paths."
                )
            elif not path.is_file():
                warn(
                    f"{key}={val!r} does not exist or is not a file — "
                    f"reverse triage will be skipped for this service until the path is valid."
                )

    # -----------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------
    if warnings:
        logger.warning(
            "Config validation found %d issue(s) — review above warnings", len(warnings)
        )
    else:
        logger.info("Config validation passed — all settings look good")

    return warnings
