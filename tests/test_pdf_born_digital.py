from pathlib import Path

import docolage


class _ExplodingVLMRecognizer:
    """Fails the test loudly if the GPU/VLM path is ever entered."""

    def recognize(self, image_path: str, page_id: int):
        raise AssertionError(
            f"VLM recognizer was called for page {page_id} of a born-digital fixture"
        )


def _config(output_dir: Path) -> docolage.DocolageConfig:
    return docolage.DocolageConfig(
        output_volume_path=str(output_dir),
        vlm_recognizer=_ExplodingVLMRecognizer(),
    )


def test_born_digital_single_page_extracts_text_with_no_gpu(fixtures_dir, tmp_output_volume):
    result = docolage.parse(
        str(fixtures_dir / "born_digital_single.pdf"), _config(tmp_output_volume)
    )

    assert result["error_status"] == []
    elements = result["document"]["elements"]
    assert len(elements) > 0
    assert all(e["type"] == "text" for e in elements)

    full_text = " ".join(e["content"] for e in elements)
    assert "Born Digital Fixture" in full_text
    assert "embedded text layer" in full_text


def test_page_rasterizes_to_png_with_image_uri(fixtures_dir, tmp_output_volume):
    result = docolage.parse(
        str(fixtures_dir / "born_digital_single.pdf"), _config(tmp_output_volume)
    )

    pages = result["document"]["pages"]
    assert len(pages) == 1
    assert pages[0]["id"] == 1
    image_path = Path(pages[0]["image_uri"])
    assert image_path.exists()
    assert image_path.suffix == ".png"


def test_elements_carry_correct_page_id_bboxes(fixtures_dir, tmp_output_volume):
    result = docolage.parse(
        str(fixtures_dir / "born_digital_single.pdf"), _config(tmp_output_volume)
    )

    for element in result["document"]["elements"]:
        for bbox in element["bbox"]:
            assert bbox["page_id"] == 1
            assert len(bbox["coord"]) == 4


def test_multi_page_pdf_produces_one_page_record_per_page(fixtures_dir, tmp_output_volume):
    result = docolage.parse(
        str(fixtures_dir / "born_digital_multi.pdf"), _config(tmp_output_volume)
    )

    pages = result["document"]["pages"]
    assert [p["id"] for p in pages] == [1, 2, 3]

    referenced_page_ids = {
        bbox["page_id"] for e in result["document"]["elements"] for bbox in e["bbox"]
    }
    assert referenced_page_ids == {1, 2, 3}

    page_ids = {p["id"] for p in pages}
    assert referenced_page_ids.issubset(page_ids)
