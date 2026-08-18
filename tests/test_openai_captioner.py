"""Real OpenAI captioner, credential resolution, and graceful degradation
(ticket #9, CONTEXT.md "External OpenAI API", "Figure captioner").

Credential resolution is two states: OPENAI_API_KEY env var set, or unset
(no captioning). Both are covered here with mocks — no real network call,
no real API key, ever.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from docolage.captioner_openai import (
    OpenAICaptioner,
    build_openai_captioner,
    resolve_openai_api_key,
)
from docolage.captioning import caption_figures


def _element(element_type="figure", confidence=0.0, description="", image_uri="crop.png"):
    return {
        "id": 1,
        "type": element_type,
        "content": "",
        "confidence": confidence,
        "description": description,
        "bbox": [],
        "image_uri": image_uri,
        "captioned": False,
    }


# --- Credential resolution: two states --------------------------------------


def test_env_var_used_when_configured(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")

    key = resolve_openai_api_key()

    assert key == "sk-from-env"


def test_no_key_anywhere_returns_none(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    key = resolve_openai_api_key()

    assert key is None


# --- build_openai_captioner: the config-wiring entry point -----------------


def test_build_openai_captioner_returns_none_with_no_key_anywhere(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    captioner = build_openai_captioner()

    assert captioner is None


def test_build_openai_captioner_returns_captioner_with_env_var(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    fake_openai_module = MagicMock()

    with patch.dict("sys.modules", {"openai": fake_openai_module}):
        captioner = build_openai_captioner()

    assert isinstance(captioner, OpenAICaptioner)


# --- Key never leaks ---------------------------------------------------------


def test_key_does_not_appear_in_captioner_repr_or_str(monkeypatch):
    fake_openai_module = MagicMock()
    with patch.dict("sys.modules", {"openai": fake_openai_module}):
        captioner = OpenAICaptioner(api_key="sk-super-secret-value")

    assert "sk-super-secret-value" not in repr(captioner)
    assert "sk-super-secret-value" not in str(captioner.__dict__)


# --- OpenAICaptioner.caption: calls the client with the crop image ---------


def test_caption_calls_openai_client_with_image(tmp_path):
    image_path = tmp_path / "crop.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\nfake-png-bytes")

    fake_response = MagicMock()
    fake_response.choices = [MagicMock(message=MagicMock(content="a red bar chart"))]

    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = fake_response

    fake_openai_module = MagicMock()
    fake_openai_module.OpenAI.return_value = fake_client

    with patch.dict("sys.modules", {"openai": fake_openai_module}):
        captioner = OpenAICaptioner(api_key="sk-test-key")
        result = captioner.caption(str(image_path))

    assert result == "a red bar chart"
    fake_client.chat.completions.create.assert_called_once()
    call_kwargs = fake_client.chat.completions.create.call_args.kwargs
    # The crop image must be sent as part of the message content.
    content = call_kwargs["messages"][0]["content"]
    assert any(part.get("type") == "image_url" for part in content)


def test_openai_captioner_requires_openai_package(monkeypatch):
    with patch.dict("sys.modules", {"openai": None}):
        with pytest.raises(ImportError):
            OpenAICaptioner(api_key="sk-test-key")


# --- Failing calls: captioning degrades silently, does not crash -----------


def test_caption_figures_leaves_description_empty_when_captioner_raises():
    failing_captioner = MagicMock()
    failing_captioner.caption.side_effect = TimeoutError("OpenAI API timed out")
    elements = [_element(description="", confidence=0.0)]

    result = caption_figures(elements, failing_captioner)

    assert result[0]["description"] == ""
    assert result[0]["captioned"] is False
    failing_captioner.caption.assert_called_once_with("crop.png")


def test_caption_figures_does_not_raise_when_captioner_raises():
    failing_captioner = MagicMock()
    failing_captioner.caption.side_effect = RuntimeError("network error")
    elements = [_element(description="stale docling caption", confidence=0.0)]

    # Must not propagate -- the whole point of #9's degradation story.
    result = caption_figures(elements, failing_captioner)

    # The figure keeps its pre-existing description rather than being
    # overwritten, but is not marked as captioned.
    assert result[0]["description"] == "stale docling caption"
    assert result[0]["captioned"] is False


# --- No key configured anywhere: parse() completes normally ----------------


def test_no_captioner_configured_end_to_end(fixtures_dir, tmp_output_volume):
    import docolage
    from docolage.ports import FakeVLMRecognizer, LayoutElement

    vlm = FakeVLMRecognizer(
        {
            1: [
                LayoutElement(
                    type="figure", content="", confidence=0.1, description="", coord=[0, 0, 5, 5]
                ),
            ]
        }
    )
    config = docolage.DocolageConfig(
        output_path=str(tmp_output_volume),
        vlm_recognizer=vlm,
        figure_captioner=None,
    )

    result = docolage.parse(str(fixtures_dir / "standalone.png"), config)

    figure = result["document"]["elements"][0]
    assert figure["captioned"] is False
    assert result["error_status"] == []
