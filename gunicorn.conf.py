"""
Gunicorn configuration for Homelab AI Sentinel.

All settings are configurable via environment variables so you can tune
without rebuilding the Docker image. The defaults are chosen for a typical
homelab server (2-4 core CPU, Docker container).

Worker model
============
worker_class = "gthread" — each worker process gets `threads` threads, each
thread handles one request. This is the right choice for an I/O-heavy app:
our request handler spends most of its time waiting on the Gemini API (up to
30s) and on 10 parallel notification clients (up to 15s). Threads release the
GIL during network I/O, so the "concurrency" is real even under CPython.

Capacity math (defaults on a 2-core machine):
  workers = cpu_count + 1 = 3
  threads per worker = 4
  → 12 concurrent request slots

For a homelab processing a handful of alerts per hour, this is far more than
needed. For busier setups, increase WORKER_THREADS before adding workers —
threads are cheaper than processes for this workload.
"""

import multiprocessing
import os

# ---------------------------------------------------------------------------
# Workers
# ---------------------------------------------------------------------------

# (CPU count + 1) is a conservative formula for I/O-bound workloads.
# Override with WORKERS if your container has CPU limits or you need to tune.
workers = int(os.environ.get("WORKERS", multiprocessing.cpu_count() + 1))

# gthread: threads-per-worker model. Each thread handles one concurrent request.
# The GIL is released during all network I/O (requests library, smtplib),
# so I/O-bound threads genuinely run in parallel within each worker.
worker_class = "gthread"

# Threads per worker process. Default 4 gives a good balance between
# concurrency and per-thread memory overhead.
threads = int(os.environ.get("WORKER_THREADS", "4"))

# ---------------------------------------------------------------------------
# Timeouts
# ---------------------------------------------------------------------------

# Worker kill timeout. Must be longer than the worst-case request:
# Gemini (30s) + slowest notification client (15s iMessage) = ~45s.
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "60"))

# How long to wait for in-flight requests to finish during a graceful restart.
graceful_timeout = 30

# Keep-alive seconds for persistent connections from reverse proxies.
keepalive = int(os.environ.get("GUNICORN_KEEPALIVE", "5"))

# ---------------------------------------------------------------------------
# Worker recycling
# ---------------------------------------------------------------------------

# Recycle workers after N requests. Prevents memory growth if there are any
# leaks in the request pipeline. Jitter spreads restarts to avoid a
# thundering-herd effect when multiple workers hit the limit simultaneously.
max_requests = int(os.environ.get("GUNICORN_MAX_REQUESTS", "1000"))
max_requests_jitter = 100

# ---------------------------------------------------------------------------
# Binding
# ---------------------------------------------------------------------------

bind = f"0.0.0.0:{os.environ.get('PORT', '5000')}"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

# Access log: off by default (application-level logging is sufficient).
# Set GUNICORN_ACCESS_LOG="-" to write access logs to stdout.
accesslog = os.environ.get("GUNICORN_ACCESS_LOG") or None

# Error log always goes to stderr.
errorlog = "-"
loglevel = "info"
