"""
Prometheus-compatible metrics endpoint.

Thread-safe counters exposed as text/plain at GET /metrics. No external
dependencies — pure Python counters formatted to Prometheus exposition format.

Protected by WEBHOOK_SECRET (optional) like /health. Exposes request volume
and error rates — no secrets, no alert content.
"""

import threading
import time

_lock = threading.Lock()
_start_time = time.monotonic()

# Counter storage: name → value (int)
_counters: dict[str, int] = {}

# Label-based counters: (name, label_name, label_value) → value
_label_counters: dict[tuple[str, str, str], int] = {}


def inc(name: str, amount: int = 1) -> None:
    """Increment a counter by amount (default 1)."""
    with _lock:
        _counters[name] = _counters.get(name, 0) + amount


def inc_labeled(name: str, label_name: str, label_value: str, amount: int = 1) -> None:
    """Increment a labeled counter."""
    key = (name, label_name, label_value)
    with _lock:
        _label_counters[key] = _label_counters.get(key, 0) + amount


def format_prometheus() -> str:
    """Return all metrics in Prometheus text exposition format."""
    lines: list[str] = []
    with _lock:
        # Plain counters
        for name, value in sorted(_counters.items()):
            lines.append(f"# TYPE {name} counter")
            lines.append(f"{name} {value}")

        # Labeled counters — group by metric name
        grouped: dict[str, list[tuple[str, str, int]]] = {}
        for (name, label_name, label_value), value in sorted(_label_counters.items()):
            grouped.setdefault(name, []).append((label_name, label_value, value))

        for name, entries in sorted(grouped.items()):
            lines.append(f"# TYPE {name} counter")
            for label_name, label_value, value in entries:
                lines.append(f'{name}{{{label_name}="{label_value}"}} {value}')

    # Uptime gauge
    uptime = time.monotonic() - _start_time
    lines.append("# TYPE sentinel_uptime_seconds gauge")
    lines.append(f"sentinel_uptime_seconds {uptime:.1f}")

    lines.append("")  # trailing newline
    return "\n".join(lines)


def reset() -> None:
    """Reset all counters. Used by tests."""
    with _lock:
        _counters.clear()
        _label_counters.clear()
