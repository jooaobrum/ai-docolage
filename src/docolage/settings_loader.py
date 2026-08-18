"""Loads docolage.yaml — the single source of truth for every tunable
parameter (models, thresholds, prompts, timeouts, DPI, queue/sync knobs)
used across both the Core (``DocolageConfig``, docolage/config.py) and
the Service (``Settings``, docolage_service/config.py). Neither config
object hardcodes a tunable default itself; both read it from here at
class-definition time, grouped into the same sections as the YAML file.

Secrets and per-deployment connection info never live in this file —
those stay in environment variables (docolage_service/config.py's
DOCOLAGE_* prefix), which is also how the file's own location can be
overridden (DOCOLAGE_CONFIG_PATH), e.g. to bind-mount a production copy
over the packaged default without rebuilding the image.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_PACKAGED_CONFIG_PATH = Path(__file__).resolve().parent / "docolage.yaml"


def _config_path() -> Path:
    override = os.environ.get("DOCOLAGE_CONFIG_PATH")
    return Path(override) if override else _PACKAGED_CONFIG_PATH


@lru_cache
def load_config() -> dict[str, Any]:
    """Parse docolage.yaml once per process and cache the result.

    Cached because every DocolageConfig/Settings class body reads from
    this at import time; re-parsing the file on every import would be
    wasted work for a file that is never expected to change mid-process.
    """
    with _config_path().open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def section(name: str) -> dict[str, Any]:
    """One top-level section of docolage.yaml (e.g. "recognition",
    "openai", "queue") as a plain dict, or {} if the section is absent."""
    return load_config().get(name) or {}
