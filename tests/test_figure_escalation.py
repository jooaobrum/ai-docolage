"""Escalation (CONTEXT.md "Escalation", ADR-0003): a Figure whose VLM
output is empty or below config.escalation_threshold gets re-described
via the Figure captioner port. Tables, formulas, and charts never
escalate, whatever their confidence."""

from pathlib import Path

import docolage
from docolage.escalation import apply_escalation
from docolage.ports import FakeVLMRecognizer, LayoutElement
from docolage.ports_captioner import FakeFigureCaptioner


def _element(element_type, confidence, description="", image_uri="crop.png"):
    return {
        "id": 1,
        "type": element_type,
        "content": "",
        "confidence": confidence,
        "description": description,
        "bbox": [],
        "image_uri": image_uri,
        "escalated": False,
    }


def test_low_confidence_figure_escalates():
    fake = FakeFigureCaptioner({"crop.png": "a captioned photo"})
    elements = [_element("figure", 0.1, description="blurry")]

    result = apply_escalation(elements, fake, threshold=0.5)

    assert result[0]["description"] == "a captioned photo"
    assert result[0]["escalated"] is True
    assert fake.calls == ["crop.png"]


def test_empty_description_figure_escalates_regardless_of_confidence():
    fake = FakeFigureCaptioner({"crop.png": "a captioned photo"})
    elements = [_element("figure", 0.99, description="")]

    result = apply_escalation(elements, fake, threshold=0.5)

    assert result[0]["description"] == "a captioned photo"
    assert result[0]["escalated"] is True
    assert fake.calls == ["crop.png"]


def test_confident_nonempty_figure_does_not_escalate():
    fake = FakeFigureCaptioner({"crop.png": "should not be used"})
    elements = [_element("figure", 0.9, description="a clear photo of a cat")]

    result = apply_escalation(elements, fake, threshold=0.5)

    assert result[0]["description"] == "a clear photo of a cat"
    assert result[0]["escalated"] is False
    assert fake.calls == []


def test_table_formula_chart_never_escalate():
    fake = FakeFigureCaptioner({"crop.png": "should not be used"})
    elements = [
        _element("table", 0.0, description=""),
        _element("formula", 0.0, description=""),
        _element("chart", 0.0, description=""),
    ]

    result = apply_escalation(elements, fake, threshold=0.5)

    for element in result:
        assert element["escalated"] is False
        assert element["description"] == ""
    assert fake.calls == []


def test_no_captioner_configured_leaves_elements_unescalated():
    elements = [_element("figure", 0.0, description="")]

    result = apply_escalation(elements, None, threshold=0.5)

    assert result[0]["escalated"] is False
    assert result[0]["description"] == ""


def test_escalation_is_per_element_not_per_document():
    fake = FakeFigureCaptioner({"low.png": "low conf photo", "high.png": "unused"})
    elements = [
        _element("figure", 0.1, description="blurry", image_uri="low.png"),
        _element("figure", 0.9, description="clear photo", image_uri="high.png"),
    ]

    result = apply_escalation(elements, fake, threshold=0.5)

    assert result[0]["escalated"] is True
    assert result[0]["description"] == "low conf photo"
    assert result[1]["escalated"] is False
    assert result[1]["description"] == "clear photo"
    assert fake.calls == ["low.png"]


def test_parse_wires_escalation_end_to_end(fixtures_dir, tmp_output_volume):
    vlm = FakeVLMRecognizer(
        {
            1: [
                LayoutElement(
                    type="figure", content="", confidence=0.1, description="", coord=[0, 0, 5, 5]
                ),
            ]
        }
    )
    captioner = FakeFigureCaptioner(default="described by openai")
    config = docolage.DocolageConfig(
        output_volume_path=str(tmp_output_volume),
        vlm_recognizer=vlm,
        figure_captioner=captioner,
        escalation_threshold=0.5,
    )

    result = docolage.parse(str(fixtures_dir / "standalone.png"), config)
    figure = result["document"]["elements"][0]

    assert figure["description"] == "described by openai"
    assert figure["escalated"] is True
    assert len(captioner.calls) == 1
