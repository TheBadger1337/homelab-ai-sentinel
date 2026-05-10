"""
Shared constants for notification clients.

Centralises severity-to-emoji and severity-to-color mappings so that all
notification platforms display consistent visual indicators without each
client maintaining its own copy.

Usage
-----
from .notify_common import SEVERITY_EMOJI, SEVERITY_COLOR

emoji = SEVERITY_EMOJI.get(alert.severity, SEVERITY_EMOJI["unknown"])
color = SEVERITY_COLOR.get(alert.severity, SEVERITY_COLOR["unknown"])
"""

# Severity → Unicode emoji circle
# Used in message subjects/headers across Slack, Email, and similar text-first platforms.
# info uses green (🟢) to match existing client behaviour and passing test assertions.
SEVERITY_EMOJI: dict[str, str] = {
    "critical": "\U0001f534",   # 🔴
    "high":     "\U0001f7e0",   # 🟠
    "warning":  "\U0001f7e1",   # 🟡
    "info":     "\U0001f7e2",   # 🟢
    "ok":       "\U0001f7e2",   # 🟢
    "unknown":  "⚪",       # ⚪
}

# Severity → integer RGB colour (Discord embed, Gotify tinting, etc.)
SEVERITY_COLOR: dict[str, int] = {
    "critical": 0xFF0000,
    "high":     0xFF8C00,
    "warning":  0xFFD700,
    "info":     0x1E90FF,
    "ok":       0x32CD32,
    "unknown":  0x808080,
}
