"""Prometheus metrics shared by the API and Worker processes.

Single-host docker-compose today has no autoscaler reading these -- the
near-term payoff is visibility: queue depth, per-page latency, the OpenAI
fallback rate, and error/retry rate are otherwise only observable by
manually polling GET /jobs?stale=true. Exposing them here is what lets a
later Datadog agent or self-hosted Prometheus/Grafana stack scrape them
without any further application change.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

JOBS_SUBMITTED_TOTAL = Counter(
    "docolage_jobs_submitted_total", "Jobs accepted by POST /submit", ["delivery"]
)

PAGE_JOBS_TOTAL = Counter(
    "docolage_page_jobs_total", "Page jobs processed by a Worker", ["result"]
)

PAGE_PROCESSING_SECONDS = Histogram(
    "docolage_page_processing_seconds", "Wall time of one page's rasterize+recognize+caption call"
)

OPENAI_FALLBACK_TOTAL = Counter(
    "docolage_openai_fallback_total", "Pages re-recognized via the OpenAI fallback"
)

QUEUE_DEPTH = Gauge(
    "docolage_queue_depth", "Page jobs currently waiting to be claimed"
)
