"""FallbackRecognizer: page-level escalation from the primary (docling)
recognizer to a fallback (OpenAI) recognizer on low confidence.
"""

from __future__ import annotations

from docolage.ports import FakeVLMRecognizer, LayoutElement
from docolage.recognizer_fallback import FallbackRecognizer


def _primary(confidence: float) -> FakeVLMRecognizer:
    return FakeVLMRecognizer(
        {1: [LayoutElement(type="text", content="primary", confidence=confidence)]}
    )


def test_high_confidence_page_never_calls_fallback():
    primary = _primary(confidence=0.9)
    fallback = FakeVLMRecognizer({1: [LayoutElement(type="text", content="fallback", confidence=1.0)]})
    recognizer = FallbackRecognizer(primary, fallback, confidence_threshold=0.7)

    result = recognizer.recognize("page.png", 1)

    assert result.elements[0].content == "primary"
    assert fallback.calls == []


def test_low_confidence_page_calls_fallback_and_returns_its_result():
    primary = _primary(confidence=0.3)
    fallback = FakeVLMRecognizer({1: [LayoutElement(type="text", content="fallback", confidence=1.0)]})
    recognizer = FallbackRecognizer(primary, fallback, confidence_threshold=0.7)

    result = recognizer.recognize("page.png", 1)

    assert result.elements[0].content == "fallback"
    assert fallback.calls == [("page.png", 1)]


def test_no_fallback_configured_never_calls_out_regardless_of_confidence():
    primary = _primary(confidence=0.0)
    recognizer = FallbackRecognizer(primary, fallback=None, confidence_threshold=0.7)

    result = recognizer.recognize("page.png", 1)

    assert result.elements[0].content == "primary"


def test_fallback_raising_degrades_to_primary_result():
    primary = _primary(confidence=0.1)
    fallback = FakeVLMRecognizer({1: [LayoutElement(type="text", content="fallback", confidence=1.0)]}, failing_pages={1: RuntimeError("boom")})
    recognizer = FallbackRecognizer(primary, fallback, confidence_threshold=0.7)

    result = recognizer.recognize("page.png", 1)

    assert result.elements[0].content == "primary"


def test_page_with_no_elements_defaults_to_full_confidence_and_skips_fallback():
    primary = FakeVLMRecognizer({1: []})
    fallback = FakeVLMRecognizer({1: [LayoutElement(type="text", content="fallback", confidence=1.0)]})
    recognizer = FallbackRecognizer(primary, fallback, confidence_threshold=0.7)

    result = recognizer.recognize("page.png", 1)

    assert result.elements == []
    assert fallback.calls == []
