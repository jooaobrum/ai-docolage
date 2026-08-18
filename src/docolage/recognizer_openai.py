"""Real ``VLMRecognizer`` (docolage.ports) backed by the OpenAI API, used as
a page-level quality fallback (ADR-0010: "a cheap pre-filter can be
reintroduced later behind the same port").

Docling's standard pipeline (recognizer_docling.py, ADR-0010) is the
primary recognizer. This module is the escalation target for pages where
docling's own confidence report is low: FallbackRecognizer
(recognizer_fallback.py) calls this recognizer instead when that happens.

Credential resolution and the "no key configured" degradation follow the
exact same shape as the Figure captioner (captioner_openai.py): both
ports share docolage.openai_client rather than one importing from the
other, so the recognition fallback and figure captioning are independent
consumers of the same OpenAI plumbing — they merely happen to default to
the same model, and are both silently absent under the same condition
(no ``OPENAI_API_KEY``).
"""

from __future__ import annotations

import json
from typing import Optional

from docolage.openai_client import DEFAULT_MODEL, encode_image_base64, resolve_openai_api_key
from docolage.ports import LayoutElement, RecognitionResult
from docolage.settings_loader import section

_openai_section = section("openai")

DEFAULT_PROMPT: str = _openai_section["recognition_prompt"]

# See docolage.yaml's "openai.fallback_result_confidence" comment for why
# this is deliberately below FallbackRecognizer's default
# confidence_threshold: a successful API call is evidence the model
# produced *some* structured answer, not that the answer is correct, so
# elements from this recognizer must not claim full confidence.
DEFAULT_FALLBACK_CONFIDENCE: float = _openai_section["fallback_result_confidence"]


def build_openai_recognizer(
    model: str = DEFAULT_MODEL,
    prompt: str = DEFAULT_PROMPT,
    result_confidence: float = DEFAULT_FALLBACK_CONFIDENCE,
) -> Optional["OpenAIRecognizer"]:
    """Resolve a key from the environment and build a recognizer, or return
    None when no key is configured — the "no fallback" branch. Intended
    entry point for wiring the Worker's recognition fallback:

        openai_recognizer = build_openai_recognizer()

    A ``None`` result means ``FallbackRecognizer`` never escalates, no
    matter how low docling's confidence is (spec: fallback must not run
    when OpenAI is not configured).
    """
    api_key = resolve_openai_api_key()
    if not api_key:
        return None
    return OpenAIRecognizer(api_key=api_key, model=model, prompt=prompt, result_confidence=result_confidence)


class OpenAIRecognizer:
    """Real ``VLMRecognizer`` (docolage.ports) backed by the OpenAI API.

    Constructed with an already-resolved key — use ``build_openai_recognizer``
    to get one via the credential chain instead of instantiating this
    directly with a hardcoded key.
    """

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        prompt: str = DEFAULT_PROMPT,
        result_confidence: float = DEFAULT_FALLBACK_CONFIDENCE,
    ) -> None:
        try:
            from openai import OpenAI  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ImportError(
                "OpenAIRecognizer requires the 'openai' package, which is not "
                "installed. Install the optional extra before selecting the "
                "recognition fallback, e.g. `pip install ai-docolage[openai]`."
            ) from exc

        self._model = model
        self._prompt = prompt
        self._result_confidence = result_confidence
        self._client = OpenAI(api_key=api_key)

    def recognize(self, image_path: str, page_id: int) -> RecognitionResult:
        """Recognize one rasterized page PNG via the OpenAI API.

        Raises on failure (network error, API error, bad image, malformed
        response) — same contract as OpenAICaptioner.caption; the caller
        (FallbackRecognizer) is responsible for catching and degrading to
        the primary recognizer's result.
        """
        image_b64 = encode_image_base64(image_path)
        response = self._client.chat.completions.create(
            model=self._model,
            response_format={"type": "json_object"},
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
        content = response.choices[0].message.content or "{}"
        payload = json.loads(content)

        elements = [
            LayoutElement(
                type=raw.get("type", "text"),
                content=raw.get("content", ""),
                confidence=self._result_confidence,
                coord=[round(v) for v in raw.get("coord", [])],
                description=raw.get("description", ""),
            )
            for raw in payload.get("elements", [])
        ]
        # markdown is derived from elements rather than asked for as a
        # separate field in the prompt: a second, independently-generated
        # field could disagree with elements (seen in practice — the model
        # has returned populated elements with an empty markdown string),
        # and nothing downstream would catch that mismatch. Joining content
        # guarantees the two stay consistent by construction, matching
        # FakeVLMRecognizer's synthesized-markdown fallback (docolage.ports).
        markdown = "\n\n".join(el.content for el in elements if el.content)
        return RecognitionResult(elements=elements, markdown=markdown)
