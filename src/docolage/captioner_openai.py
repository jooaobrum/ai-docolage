"""Real FigureCaptioner (CONTEXT.md "Figure captioner", "External OpenAI
API") backed by the OpenAI API, plus the credential resolution chain that
keeps the key out of notebook source (ticket #9).

Credential resolution, in order:
  1. A Databricks secret scope, via ``dbutils.secrets.get`` — guarded so it
     degrades cleanly when ``dbutils`` is unavailable (off-platform, e.g.
     in tests or a plain Python process).
  2. The ``OPENAI_API_KEY`` environment variable.
  3. No escalation at all: ``resolve_openai_api_key`` returns ``None`` and
     callers must not construct a captioner.

Degradation is the point of this module as much as captioning is. With no
key resolvable anywhere, or when a call to the OpenAI API raises, the
caller (docolage.escalation.apply_escalation) keeps the VLM's original
description — this module never raises out of ``caption`` in a way that
would be allowed to fail a page or a document; see apply_escalation for
where that boundary is enforced.

The raw key is never printed, logged, or embedded in any exception message
anywhere in this module.
"""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_SECRET_KEY = "openai-api-key"
DEFAULT_MODEL = "gpt-4o-mini"


def _get_dbutils() -> Optional[Any]:
    """Best-effort lookup of the notebook-injected ``dbutils`` global.

    docolage is an importable package, not notebook source, so it has no
    direct reference to the notebook's global namespace. ``dbutils`` is
    only ever reachable through IPython's user namespace when running
    inside a Databricks notebook kernel. Off-platform (plain Python,
    pytest, a laptop REPL) there is no IPython kernel, or no ``dbutils``
    in it, and this returns None rather than raising — the whole point of
    the fallback chain in ``resolve_openai_api_key``.
    """
    try:
        from IPython import get_ipython  # type: ignore[import-not-found]
    except ImportError:
        return None

    ip = get_ipython()
    if ip is None:
        return None
    return ip.user_ns.get("dbutils")


def resolve_openai_api_key(
    secret_scope: Optional[str] = None,
    secret_key: str = DEFAULT_SECRET_KEY,
) -> Optional[str]:
    """Resolve the OpenAI API key via the documented chain (spec 0001,
    "The OpenAI key resolves from a Databricks secret scope, falling back
    to the OPENAI_API_KEY environment variable, falling back to no
    escalation.").

    Returns None — never raises — when no key is found anywhere, so a
    key-less run degrades to "no escalation" rather than failing.
    """
    if secret_scope:
        dbutils = _get_dbutils()
        if dbutils is not None:
            try:
                key = dbutils.secrets.get(scope=secret_scope, key=secret_key)
            except Exception:
                # Scope/key misconfigured, or dbutils.secrets unavailable
                # in this runtime. Fall through to the env var rather than
                # failing setup over an optional dependency.
                logger.debug(
                    "Databricks secret scope lookup failed for scope=%r; "
                    "falling back to OPENAI_API_KEY env var.",
                    secret_scope,
                )
            else:
                if key:
                    return str(key)

    env_key = os.environ.get("OPENAI_API_KEY")
    if env_key:
        return env_key

    return None


def build_openai_captioner(
    secret_scope: Optional[str] = None,
    secret_key: str = DEFAULT_SECRET_KEY,
    model: str = DEFAULT_MODEL,
) -> Optional["OpenAICaptioner"]:
    """Resolve a key via the fallback chain and build a captioner, or
    return None when no key is configured anywhere — the "no escalation"
    branch. This is the intended entry point for wiring
    ``DocolageConfig.figure_captioner``:

        config.figure_captioner = docolage.build_openai_captioner(
            secret_scope="my-scope"
        )

    ``config.figure_captioner`` stays None when this returns None, and
    ``apply_escalation`` already treats a None captioner as "skip
    escalation" (CONTEXT.md "Escalation" — optional end to end).
    """
    api_key = resolve_openai_api_key(secret_scope=secret_scope, secret_key=secret_key)
    if not api_key:
        return None
    return OpenAICaptioner(api_key=api_key, model=model)


class OpenAICaptioner:
    """Real ``FigureCaptioner`` (docolage.ports_captioner) backed by the
    External OpenAI API (CONTEXT.md).

    Constructed with an already-resolved key — use ``build_openai_captioner``
    to get one via the credential chain instead of instantiating this
    directly with a hardcoded key.
    """

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL) -> None:
        try:
            from openai import OpenAI  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ImportError(
                "OpenAICaptioner requires the 'openai' package, which is not "
                "installed. Install the optional extra before selecting the "
                "real captioner via DocolageConfig, e.g. "
                "`pip install ai-docolage[openai]`. (FakeFigureCaptioner "
                "remains available for testing without the dependency.)"
            ) from exc

        self._model = model
        # The client holds the key internally; nothing here ever reads it
        # back out, prints it, or includes it in a log/exception message.
        self._client = OpenAI(api_key=api_key)

    def caption(self, image_path: str) -> str:
        """Caption the figure crop at image_path via the OpenAI API.

        Raises on failure (network error, API error, bad image, ...) —
        callers (docolage.escalation.apply_escalation) are responsible for
        catching and keeping the VLM's original description; this method
        does not swallow errors itself so that failure is observable in
        direct unit tests of this class.
        """
        image_b64 = _encode_image(image_path)
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Describe this figure in one or two concise sentences.",
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{image_b64}"},
                        },
                    ],
                }
            ],
        )
        return (response.choices[0].message.content or "").strip()


def _encode_image(image_path: str) -> str:
    data = Path(image_path).read_bytes()
    return base64.b64encode(data).decode("ascii")
