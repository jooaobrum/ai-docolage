"""Shared OpenAI plumbing for the two External OpenAI API ports
(captioner_openai.OpenAICaptioner, recognizer_openai.OpenAIRecognizer).

Both ports call the same API with the same credential and the same image
encoding; this module is that shared layer so neither port imports from
the other. The default model name is read from docolage.yaml's "openai"
section (settings_loader.py) — it is only a fallback for direct Core use
with no explicit ``model=``; the Service always passes
``Settings.openai_model`` explicitly, itself sourced from the same file.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Optional

from docolage.settings_loader import section

DEFAULT_MODEL: str = section("openai")["model"]


def resolve_openai_api_key() -> Optional[str]:
    """Resolve the OpenAI API key from the ``OPENAI_API_KEY`` environment
    variable.

    Returns None — never raises — when no key is set, so a key-less run
    degrades to "no captioning"/"no recognition fallback" rather than
    failing.
    """
    return os.environ.get("OPENAI_API_KEY") or None


def encode_image_base64(image_path: str) -> str:
    data = Path(image_path).read_bytes()
    return base64.b64encode(data).decode("ascii")
