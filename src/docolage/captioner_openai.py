"""Real FigureCaptioner (CONTEXT.md "Figure captioner", "External OpenAI
API") backed by the OpenAI API.

Degradation is the point of this module as much as captioning is. With no
key resolvable (docolage.openai_client.resolve_openai_api_key), or when a
call to the OpenAI API raises, the caller (docolage.captioning.
caption_figures) leaves the figure without a description — this module
never raises out of ``caption`` in a way that would be allowed to fail a
page or a document; see caption_figures for where that boundary is
enforced.

The raw key is never printed, logged, or embedded in any exception message
anywhere in this module.
"""

from __future__ import annotations

from typing import Optional

from docolage.openai_client import DEFAULT_MODEL, encode_image_base64, resolve_openai_api_key
from docolage.settings_loader import section

DEFAULT_PROMPT: str = section("openai")["figure_caption_prompt"]

__all__ = [
    "DEFAULT_MODEL",
    "DEFAULT_PROMPT",
    "resolve_openai_api_key",
    "build_openai_captioner",
    "OpenAICaptioner",
]


def build_openai_captioner(
    model: str = DEFAULT_MODEL, prompt: str = DEFAULT_PROMPT
) -> Optional["OpenAICaptioner"]:
    """Resolve a key from the environment and build a captioner, or return
    None when no key is configured — the "no captioning" branch. This is
    the intended entry point for wiring ``DocolageConfig.figure_captioner``:

        config.figure_captioner = docolage.build_openai_captioner()

    ``config.figure_captioner`` stays None when this returns None, and
    ``caption_figures`` already treats a None captioner as "skip
    captioning" (ADR-0010 — optional end to end).
    """
    api_key = resolve_openai_api_key()
    if not api_key:
        return None
    return OpenAICaptioner(api_key=api_key, model=model, prompt=prompt)


class OpenAICaptioner:
    """Real ``FigureCaptioner`` (docolage.ports_captioner) backed by the
    External OpenAI API (CONTEXT.md).

    Constructed with an already-resolved key — use ``build_openai_captioner``
    to get one via the credential chain instead of instantiating this
    directly with a hardcoded key.
    """

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, prompt: str = DEFAULT_PROMPT) -> None:
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
        self._prompt = prompt
        # The client holds the key internally; nothing here ever reads it
        # back out, prints it, or includes it in a log/exception message.
        self._client = OpenAI(api_key=api_key)

    def caption(self, image_path: str) -> str:
        """Caption the figure crop at image_path via the OpenAI API.

        Raises on failure (network error, API error, bad image, ...) —
        callers (docolage.captioning.caption_figures) are responsible for
        catching and leaving the figure without a description; this method
        does not swallow errors itself so that failure is observable in
        direct unit tests of this class.
        """
        image_b64 = encode_image_base64(image_path)
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": self._prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{image_b64}"},
                        },
                    ],
                }
            ],
        )
        return (response.choices[0].message.content or "").strip()
