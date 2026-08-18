"""Pure, DB-free tests for examples/rag_pipeline/chunking.py -- section
splitting and standalone image-chunk promotion."""

from examples.rag_pipeline.chunking import build_page_chunks


def _el(type, content="", description="", image_uri=""):
    return {"type": type, "content": content, "description": description, "image_uri": image_uri}


def test_single_section_with_text_becomes_one_chunk():
    elements = [
        _el("heading-1", "Introduction"),
        _el("text", "This is the first paragraph."),
        _el("text", "This is the second paragraph."),
    ]
    chunks = build_page_chunks(1, elements)
    assert len(chunks) == 1
    assert chunks[0].chunk_type == "text"
    assert chunks[0].section == "Introduction"
    assert "# Introduction" in chunks[0].content
    assert "first paragraph" in chunks[0].content
    assert "second paragraph" in chunks[0].content


def test_new_heading_starts_a_new_chunk():
    elements = [
        _el("heading-1", "Section A"),
        _el("text", "A content."),
        _el("heading-1", "Section B"),
        _el("text", "B content."),
    ]
    chunks = build_page_chunks(1, elements)
    assert len(chunks) == 2
    assert chunks[0].section == "Section A"
    assert "A content." in chunks[0].content
    assert chunks[1].section == "Section B"
    assert "B content." in chunks[1].content


def test_captioned_figure_becomes_standalone_image_chunk():
    elements = [
        _el("heading-1", "Results"),
        _el("text", "See the chart below."),
        _el("figure", description="Bar chart showing revenue growth", image_uri="s3://bucket/fig1.png"),
    ]
    chunks = build_page_chunks(1, elements)
    types = [c.chunk_type for c in chunks]
    assert types == ["text", "image"]
    image_chunk = chunks[1]
    assert image_chunk.section == "Results"
    assert "s3://bucket/fig1.png" in image_chunk.content
    assert "Bar chart showing revenue growth" in image_chunk.content
    assert "[IMAGE]" in image_chunk.content


def test_uncaptioned_figure_stays_inline():
    elements = [
        _el("heading-1", "Results"),
        _el("text", "See the chart below."),
        _el("figure", description="", image_uri="s3://bucket/fig1.png"),
    ]
    chunks = build_page_chunks(1, elements)
    assert len(chunks) == 1
    assert chunks[0].chunk_type == "text"
    assert "[IMAGE]" in chunks[0].content
    assert "s3://bucket/fig1.png" in chunks[0].content


def test_table_renders_inline_as_placeholder():
    elements = [
        _el("heading-1", "Data"),
        _el("table", content="<table><tr><td>1</td></tr></table>", image_uri="s3://bucket/table1.png"),
    ]
    chunks = build_page_chunks(1, elements)
    assert len(chunks) == 1
    assert "[TABLE]" in chunks[0].content
    assert "s3://bucket/table1.png" in chunks[0].content
    assert "<table>" in chunks[0].content


def test_header_and_footer_are_dropped():
    elements = [
        _el("header", "Company Confidential"),
        _el("heading-1", "Body"),
        _el("text", "content"),
        _el("footer", "Page 1 of 10"),
    ]
    chunks = build_page_chunks(1, elements)
    assert len(chunks) == 1
    assert "Confidential" not in chunks[0].content
    assert "Page 1 of 10" not in chunks[0].content


def test_no_elements_produces_no_chunks():
    assert build_page_chunks(1, []) == []


def test_text_before_any_heading_has_no_section():
    elements = [_el("text", "orphan paragraph, no heading yet")]
    chunks = build_page_chunks(1, elements)
    assert len(chunks) == 1
    assert chunks[0].section is None


def test_chunk_index_is_sequential_across_mixed_types():
    elements = [
        _el("heading-1", "Intro"),
        _el("text", "para"),
        _el("figure", description="a captioned figure"),
        _el("heading-1", "Next"),
        _el("text", "more"),
    ]
    chunks = build_page_chunks(1, elements)
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
