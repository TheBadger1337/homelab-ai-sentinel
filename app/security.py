"""
Prompt injection detection.

Scans normalized alert fields for known injection patterns before the AI call.
Detection is informational only — alerts are never blocked based on pattern
matches alone. The structural mitigations in gemini_client.py (XML delimiters,
field caps, output validation) already limit blast radius; this layer makes
attempts *visible* so operators can see if they're being probed.

Detected events are written to the security_events DB table and surfaced in
the /health endpoint. A single detected event is not cause for alarm — it may
be a poorly formatted monitoring message. A pattern of events from the same
service over time is worth investigating.
"""

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .alert_parser import NormalizedAlert

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Injection patterns
# ---------------------------------------------------------------------------
# Tuned for low false-positive rate. Legitimate monitoring alerts don't contain
# these phrases. Each entry is (compiled_pattern, event_name).

_INJECTION_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Classic "ignore previous instructions" family
    (re.compile(r"ignore\s+(previous|all|above)\s+instruction", re.I), "ignore-instructions"),
    # Attempts to close or open our XML delimiters and inject after/before
    (re.compile(r"</?(?:alert_data|alert_history|system)\b", re.I), "xml-delimiter-manipulation"),
    # Persona hijacking ("you are now a", "you are now an")
    (re.compile(r"you\s+are\s+now\s+(?:a|an|the)\s+\w", re.I), "persona-override"),
    # Newline + chat-completion role injection ("system:", "assistant:", "user:")
    (re.compile(r"\n\s*(?:system|assistant|user)\s*:", re.I), "role-injection"),
]


def scan_for_injection(alert: "NormalizedAlert") -> list[str]:
    """
    Scan alert fields for prompt injection patterns.

    Returns a list of matched pattern names (empty = nothing detected).
    Each name appears at most once regardless of how many fields matched it.

    Scanned fields: service_name, message, string values in details.
    Not scanned: source, status — low injection surface, typically enum-like.
    """
    fields = [alert.service_name, alert.message]
    if alert.details:
        fields.extend(str(v) for v in alert.details.values() if isinstance(v, str))

    detected: list[str] = []
    for pattern, name in _INJECTION_PATTERNS:
        for field in fields:
            if pattern.search(field):
                detected.append(name)
                break  # one detection per pattern — don't count same pattern twice

    return detected
