"""Real OpenAI recognizer, credential resolution, and graceful degradation
(page-level quality fallback). Mirrors tests/test_openai_captioner.py's
credential-resolution coverage -- no real network call, no real API key,
ever.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from docolage.recognizer_openai import OpenAIRecognizer, build_openai_recognizer


def test_build_openai_recognizer_returns_none_with_no_key_anywhere(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    recognizer = build_openai_recognizer()

    assert recognizer is None


def test_build_openai_recognizer_returns_recognizer_with_env_var(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    fake_openai_module = MagicMock()

    with patch.dict("sys.modules", {"openai": fake_openai_module}):
        recognizer = build_openai_recognizer()

    assert isinstance(recognizer, OpenAIRecognizer)


def test_openai_recognizer_requires_openai_package():
    with patch.dict("sys.modules", {"openai": None}):
        with pytest.raises(ImportError):
            OpenAIRecognizer(api_key="sk-test-key")


def test_recognize_parses_elements_and_markdown(tmp_path):
    image_path = tmp_path / "page.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\nfake-png-bytes")

    payload = {
        "elements": [
            {"type": "heading-1", "content": "Title", "coord": [0, 0, 100, 20], "description": ""},
            {"type": "figure", "content": "", "coord": [0, 30, 100, 130], "description": "a chart"},
        ],
    }
    fake_response = MagicMock()
    fake_response.choices = [MagicMock(message=MagicMock(content=json.dumps(payload)))]

    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = fake_response

    fake_openai_module = MagicMock()
    fake_openai_module.OpenAI.return_value = fake_client

    with patch.dict("sys.modules", {"openai": fake_openai_module}):
        recognizer = OpenAIRecognizer(api_key="sk-test-key")
        result = recognizer.recognize(str(image_path), page_id=1)

    # markdown is derived from elements' content, not a separate model
    # field, so it can never disagree with elements (recognizer_openai.py).
    assert result.markdown == "Title"
    assert len(result.elements) == 2
    assert result.elements[0].type == "heading-1"
    # Fallback elements deliberately don't claim full confidence -- a
    # successful call means "some structured answer," not "correct answer"
    # (recognizer_openai.py's _FALLBACK_CONFIDENCE).
    assert result.elements[0].confidence < 1.0
    assert result.elements[1].type == "figure"
    assert result.elements[1].description == "a chart"

    call_kwargs = fake_client.chat.completions.create.call_args.kwargs
    assert call_kwargs["response_format"] == {"type": "json_object"}
    content = call_kwargs["messages"][0]["content"]
    assert any(part.get("type") == "image_url" for part in content)


def test_recognize_raises_on_malformed_json(tmp_path):
    image_path = tmp_path / "page.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\nfake-png-bytes")

    fake_response = MagicMock()
    fake_response.choices = [MagicMock(message=MagicMock(content="not json"))]

    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = fake_response

    fake_openai_module = MagicMock()
    fake_openai_module.OpenAI.return_value = fake_client

    with patch.dict("sys.modules", {"openai": fake_openai_module}):
        recognizer = OpenAIRecognizer(api_key="sk-test-key")
        with pytest.raises(json.JSONDecodeError):
            recognizer.recognize(str(image_path), page_id=1)
