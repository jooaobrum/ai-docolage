"""Figure captioning (CONTEXT.md "Figure captioner", ADR-0010): every
type="figure" element is captioned unconditionally when a Figure
captioner is configured, and carries no description when one is not.
There is no confidence threshold and nothing to escalate from (spec
user stories 48-50)."""

from __future__ import annotations

from docolage.captioning import caption_figures
from docolage.ports import FakeVLMRecognizer, LayoutElement
from docolage.ports_captioner import FakeFigureCaptioner


def _element(element_type, description="", image_uri="crop.png"):
    return {
        "id": 1,
        "type": element_type,
        "content": "",
        "confidence": 0.0,
        "description": description,
        "bbox": [],
        "image_uri": image_uri,
        "captioned": False,
    }


def test_every_figure_is_captioned_when_captioner_configured():
    fake = FakeFigureCaptioner({"a.png": "a photo of a cat", "b.png": "a bar chart"})
    elements = [
        _element("figure", image_uri="a.png"),
        _element("figure", image_uri="b.png"),
    ]

    result = caption_figures(elements, fake)

    assert result[0]["description"] == "a photo of a cat"
    assert result[0]["captioned"] is True
    assert result[1]["description"] == "a bar chart"
    assert result[1]["captioned"] is True
    assert fake.calls == ["a.png", "b.png"]


def test_confident_nonempty_figure_is_still_captioned():
    """Unlike the old escalation rule, a figure with a description is not
    exempt — docling produces no per-figure confidence to threshold on,
    so captioning is unconditional (ADR-0010)."""
    fake = FakeFigureCaptioner({"crop.png": "the new description"})
    elements = [_element("figure", description="docling's own caption")]

    result = caption_figures(elements, fake)

    assert result[0]["description"] == "the new description"
    assert fake.calls == ["crop.png"]


def test_table_formula_chart_never_captioned():
    fake = FakeFigureCaptioner({"crop.png": "should not be used"})
    elements = [
        _element("table", description=""),
        _element("formula", description=""),
        _element("chart", description=""),
    ]

    result = caption_figures(elements, fake)

    for element in result:
        assert element["captioned"] is False
        assert element["description"] == ""
    assert fake.calls == []


def test_no_captioner_configured_leaves_figures_without_description():
    elements = [_element("figure", description="")]

    result = caption_figures(elements, None)

    assert result[0]["captioned"] is False
    assert result[0]["description"] == ""


def test_captioner_failure_on_one_figure_does_not_prevent_others():
    class FlakyCaptioner:
        def __init__(self):
            self.calls: list[str] = []

        def caption(self, image_path: str) -> str:
            self.calls.append(image_path)
            if image_path == "bad.png":
                raise TimeoutError("OpenAI API timed out")
            return f"described {image_path}"

    flaky = FlakyCaptioner()
    elements = [
        _element("figure", image_uri="good1.png"),
        _element("figure", image_uri="bad.png"),
        _element("figure", image_uri="good2.png"),
    ]

    result = caption_figures(elements, flaky)

    assert result[0]["description"] == "described good1.png"
    assert result[0]["captioned"] is True
    assert result[1]["description"] == ""
    assert result[1]["captioned"] is False
    assert result[2]["description"] == "described good2.png"
    assert result[2]["captioned"] is True
    assert flaky.calls == ["good1.png", "bad.png", "good2.png"]


def test_parse_wires_captioning_end_to_end(fixtures_dir, tmp_output_volume):
    import docolage

    vlm = FakeVLMRecognizer(
        {
            1: [
                LayoutElement(
                    type="figure", content="", confidence=0.0, description="", coord=[0, 0, 5, 5]
                ),
            ]
        }
    )
    captioner = FakeFigureCaptioner(default="described by openai")
    config = docolage.DocolageConfig(
        output_path=str(tmp_output_volume),
        vlm_recognizer=vlm,
        figure_captioner=captioner,
    )

    result = docolage.parse(str(fixtures_dir / "standalone.png"), config)
    figure = result["document"]["elements"][0]

    assert figure["description"] == "described by openai"
    assert figure["captioned"] is True
    assert len(captioner.calls) == 1


def test_parse_leaves_figure_without_description_when_no_captioner_configured(
    fixtures_dir, tmp_output_volume
):
    import docolage

    vlm = FakeVLMRecognizer(
        {
            1: [
                LayoutElement(
                    type="figure", content="", confidence=0.0, description="", coord=[0, 0, 5, 5]
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

    assert figure["description"] == ""
    assert figure["captioned"] is False
    assert result["error_status"] == []
