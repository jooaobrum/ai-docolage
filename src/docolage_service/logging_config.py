"""Shared JSON logging setup for the API and Worker processes.

Both processes call `configure_logging(service_name)` once at startup. The
output is JSON-to-stdout -- a portable baseline that can be shipped to any
log platform (Datadog agent, Loki, a self-hosted sink) via the deploy-time
log driver, with no application change needed later.

`job_id` (and any other field) can be attached to an individual log call
via `extra={"job_id": ...}`, which the formatter promotes into the JSON
output -- this is the correlation key used to follow one job's lifecycle
across both the API and worker logs.
"""

from __future__ import annotations

import logging

try:
    from pythonjsonlogger.json import JsonFormatter  # python-json-logger >= 3.x
except ImportError:
    from pythonjsonlogger.jsonlogger import JsonFormatter  # python-json-logger 2.x


def configure_logging(service_name: str, level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        JsonFormatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s",
            rename_fields={"asctime": "timestamp", "levelname": "level", "name": "logger"},
            static_fields={"service": service_name},
        )
    )

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
