from pathlib import Path

import docolage
from docolage.ports import FakeVLMRecognizer, LayoutElement


def test_table_and_figure_elements_get_crop_files_text_and_formula_do_not(fixtures_dir, tmp_output_volume):
    fake = FakeVLMRecognizer(
        {
            1: [
                LayoutElement(type="text", content="Body paragraph.", confidence=0.9, coord=[0, 0, 50, 20]),
                LayoutElement(
                    type="table",
                    content="<table><tbody><tr><td>1</td></tr></tbody></table>",
                    confidence=0.8,
                    coord=[0, 0, 50, 20],
                ),
                LayoutElement(type="formula", content="E = mc^2", confidence=0.7, coord=[0, 0, 50, 20]),
                LayoutElement(
                    type="figure", content="", confidence=0.6, description="a photo", coord=[0, 0, 50, 20]
                ),
            ]
        }
    )
    config = docolage.DocolageConfig(output_path=str(tmp_output_volume), vlm_recognizer=fake)

    result = docolage.parse(str(Path("tests/fixtures/scanned.pdf")), config)
    elements = {e["type"]: e for e in result["document"]["elements"]}

    # Table and figure get their own crop file with a populated image_uri.
    for element_type in ("table", "figure"):
        element = elements[element_type]
        assert element["image_uri"] != ""
        crop_path = Path(element["image_uri"])
        assert crop_path.exists()
        assert crop_path.parent == tmp_output_volume

    # Text and formula get no crop file: empty image_uri, nothing written.
    for element_type in ("text", "formula"):
        element = elements[element_type]
        assert element["image_uri"] == ""

    crop_files = list(tmp_output_volume.glob("*-crop.png"))
    assert len(crop_files) == 2


def test_png_table_and_figure_elements_also_get_crop_files(fixtures_dir, tmp_output_volume):
    fake = FakeVLMRecognizer(
        {
            1: [
                LayoutElement(
                    type="table",
                    content="<table><tbody><tr><td>1</td></tr></tbody></table>",
                    confidence=0.8,
                    coord=[0, 0, 5, 5],
                ),
                LayoutElement(type="text", content="hi", confidence=0.9, coord=[0, 0, 5, 5]),
            ]
        }
    )
    config = docolage.DocolageConfig(output_path=str(tmp_output_volume), vlm_recognizer=fake)

    result = docolage.parse(str(fixtures_dir / "standalone.png"), config)
    elements = {e["type"]: e for e in result["document"]["elements"]}

    assert elements["table"]["image_uri"] != ""
    assert Path(elements["table"]["image_uri"]).exists()
    assert elements["text"]["image_uri"] == ""
