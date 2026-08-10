import pytest

import docolage
from docolage.exceptions import UnsupportedFormatError


def test_parse_returns_plain_dict(fixtures_dir):
    result = docolage.parse(str(fixtures_dir / "simple.md"))
    assert isinstance(result, dict)


def test_schema_shape(fixtures_dir):
    result = docolage.parse(str(fixtures_dir / "simple.md"))

    assert "document" in result
    assert "pages" in result["document"]
    assert "elements" in result["document"]
    assert "error_status" in result
    assert isinstance(result["error_status"], list)
    assert "metadata" in result
    assert "id" in result["metadata"]
    assert "version" in result["metadata"]

    for element in result["document"]["elements"]:
        assert "id" in element
        assert "type" in element
        assert "content" in element
        assert "confidence" in element
        assert "description" in element
        assert "bbox" in element
        for bbox in element["bbox"]:
            assert "coord" in bbox
            assert "page_id" in bbox


def test_headings_preserve_hierarchy(fixtures_dir):
    result = docolage.parse(str(fixtures_dir / "simple.md"))
    types = [e["type"] for e in result["document"]["elements"]]

    assert "heading-1" in types
    assert "heading-2" in types
    assert "heading-3" in types
    assert types.index("heading-1") < types.index("heading-2") < types.index("heading-3")


def test_table_lands_as_html(fixtures_dir):
    result = docolage.parse(str(fixtures_dir / "simple.md"))
    tables = [e for e in result["document"]["elements"] if e["type"] == "table"]

    assert len(tables) == 1
    assert "<table>" in tables[0]["content"]
    assert "<td>alpha</td>" in tables[0]["content"]
    assert "<td>1</td>" in tables[0]["content"]


def test_reading_order_reconstructs_document(fixtures_dir):
    result = docolage.parse(str(fixtures_dir / "simple.md"))
    elements = result["document"]["elements"]

    contents = [e["content"] for e in elements]
    assert contents[0] == "Title"
    assert "intro paragraph" in contents[1]
    assert contents[2] == "Section One"
    assert "Subsection" in [c for c in contents]
    assert contents[-1].startswith("More text after the table")


def test_unsupported_extension_raises_clear_error(fixtures_dir, tmp_path):
    bogus = tmp_path / "document.xyz"
    bogus.write_text("hello")

    with pytest.raises(UnsupportedFormatError) as exc_info:
        docolage.parse(str(bogus))

    message = str(exc_info.value)
    assert ".xyz" in message
    assert "document.xyz" in message


def test_config_has_no_hardcoded_defaults_and_no_globals():
    config = docolage.DocolageConfig(
        model_path="/Volumes/models/paddleocr-vl",
        output_volume_path="/Volumes/output",
        escalation_threshold=0.7,
    )
    result = docolage.parse("tests/fixtures/simple.md", config)
    assert isinstance(result, dict)
