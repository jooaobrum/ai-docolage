import docolage
from docolage.ports import FakeVLMRecognizer, LayoutElement


def test_scanned_pdf_routes_to_vlm_and_produces_ocrd_content(fixtures_dir, tmp_output_volume):
    fake = FakeVLMRecognizer(
        {
            1: [
                LayoutElement(type="text", content="Hello scanned world", confidence=0.95, coord=[10, 10, 200, 40]),
            ]
        }
    )
    config = docolage.DocolageConfig(output_path=str(tmp_output_volume), vlm_recognizer=fake)

    result = docolage.parse(str(fixtures_dir / "scanned.pdf"), config)

    assert fake.calls == [(result["document"]["pages"][0]["image_uri"], 1)]
    elements = result["document"]["elements"]
    assert len(elements) == 1
    assert elements[0]["type"] == "text"
    assert elements[0]["content"] == "Hello scanned world"
    assert elements[0]["confidence"] == 0.95
    assert elements[0]["bbox"][0]["page_id"] == 1


def test_standalone_png_parses_as_single_page_through_vlm(fixtures_dir, tmp_output_volume):
    fake = FakeVLMRecognizer(
        {1: [LayoutElement(type="text", content="Scanned page content", confidence=0.9, coord=[0, 0, 10, 10])]}
    )
    config = docolage.DocolageConfig(output_path=str(tmp_output_volume), vlm_recognizer=fake)

    result = docolage.parse(str(fixtures_dir / "standalone.png"), config)

    assert len(result["document"]["pages"]) == 1
    assert result["document"]["pages"][0]["id"] == 1
    assert fake.calls == [(str(fixtures_dir / "standalone.png"), 1)]
    assert result["document"]["elements"][0]["content"] == "Scanned page content"


def test_element_types_and_content_shapes(fixtures_dir, tmp_output_volume):
    fake = FakeVLMRecognizer(
        {
            1: [
                LayoutElement(type="heading-1", content="Report Title", confidence=0.99, coord=[0, 0, 1, 1]),
                LayoutElement(type="text", content="Body paragraph.", confidence=0.9, coord=[0, 0, 1, 1]),
                LayoutElement(
                    type="table",
                    content="<table><tbody><tr><td>1</td></tr></tbody></table>",
                    confidence=0.8,
                    coord=[0, 0, 1, 1],
                ),
                LayoutElement(type="formula", content="E = mc^2", confidence=0.7, coord=[0, 0, 1, 1]),
                LayoutElement(type="figure", content="", confidence=0.6, description="a photo", coord=[0, 0, 1, 1]),
                LayoutElement(type="header", content="Page Header", confidence=0.95, coord=[0, 0, 1, 1]),
                LayoutElement(type="footer", content="Page 1 of 1", confidence=0.95, coord=[0, 0, 1, 1]),
            ]
        }
    )
    config = docolage.DocolageConfig(output_path=str(tmp_output_volume), vlm_recognizer=fake)

    result = docolage.parse(str(fixtures_dir / "scanned.pdf"), config)
    elements = result["document"]["elements"]

    types = [e["type"] for e in elements]
    assert types == ["heading-1", "text", "table", "formula", "figure", "header", "footer"]

    table = elements[2]
    assert "<table>" in table["content"]

    formula = elements[3]
    assert formula["content"] == "E = mc^2"

    figure = elements[4]
    assert figure["description"] == "a photo"

    for element in elements:
        assert isinstance(element["confidence"], float)


def test_reading_order_matches_recognizer_order(fixtures_dir, tmp_output_volume):
    fake = FakeVLMRecognizer(
        {
            1: [
                LayoutElement(type="heading-1", content="First", confidence=0.9, coord=[0, 0, 1, 1]),
                LayoutElement(type="text", content="Second", confidence=0.9, coord=[0, 0, 1, 1]),
                LayoutElement(type="text", content="Third", confidence=0.9, coord=[0, 0, 1, 1]),
            ]
        }
    )
    config = docolage.DocolageConfig(output_path=str(tmp_output_volume), vlm_recognizer=fake)

    result = docolage.parse(str(fixtures_dir / "scanned.pdf"), config)
    contents = [e["content"] for e in result["document"]["elements"]]

    assert contents == ["First", "Second", "Third"]


def test_page_markdown_is_carried_through_to_the_final_document(fixtures_dir, tmp_output_volume):
    """Ticket #16: every page result carries rendered markdown alongside
    its structured elements (spec user stories 45/46)."""
    fake = FakeVLMRecognizer(
        {
            1: [LayoutElement(type="heading-1", content="Page One", confidence=0.9, coord=[0, 0, 1, 1])],
            2: [LayoutElement(type="text", content="Page two body", confidence=0.9, coord=[0, 0, 1, 1])],
        },
        markdown_by_page={
            1: "# Page One",
            2: "Page two body",
        },
    )
    config = docolage.DocolageConfig(output_path=str(tmp_output_volume), vlm_recognizer=fake)

    result = docolage.parse(str(fixtures_dir / "scanned_multi_page.pdf"), config)
    pages = {p["id"]: p for p in result["document"]["pages"]}

    assert pages[1]["markdown"] == "# Page One"
    assert pages[2]["markdown"] == "Page two body"


def test_table_spanning_page_break_merges_into_one_element(fixtures_dir, tmp_output_volume):
    fake = FakeVLMRecognizer(
        {
            1: [
                LayoutElement(
                    type="table",
                    content="<table><tbody><tr><td>row1</td></tr></tbody></table>",
                    confidence=0.9,
                    coord=[0, 0, 1, 1],
                )
            ],
            2: [
                LayoutElement(
                    type="table",
                    content="<table><tbody><tr><td>row2</td></tr></tbody></table>",
                    confidence=0.85,
                    coord=[0, 0, 1, 1],
                )
            ],
        }
    )
    config = docolage.DocolageConfig(output_path=str(tmp_output_volume), vlm_recognizer=fake)

    result = docolage.parse(str(fixtures_dir / "scanned_multi_page.pdf"), config)
    elements = result["document"]["elements"]

    assert len(elements) == 1
    merged = elements[0]
    assert merged["type"] == "table"
    assert "row1" in merged["content"]
    assert "row2" in merged["content"]
    page_ids = {bbox["page_id"] for bbox in merged["bbox"]}
    assert page_ids == {1, 2}
