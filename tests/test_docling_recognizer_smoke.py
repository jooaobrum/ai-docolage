"""Smoke test for the real DoclingRecognizer (ticket #14).

Marked `@pytest.mark.docling` and excluded from the default `pytest` run
via `addopts = "-m 'not gpu and not service and not docling'"` in
pyproject.toml: this recognizer downloads and runs real docling models
(layout detection, TableFormer, OCR), which is slow and network-dependent
and has no place in the default laptop test run.

Runs against ``data/paper.pdf`` rather than one of the tiny synthetic
``tests/fixtures/*.pdf`` PDFs deliberately: those fixtures are minimal by
design (built cheaply, for the now-deleted Router's born-digital/
text-layer logic) and their "scanned" pages carry no OCR-legible text,
so a real docling pass over them correctly finds nothing to recognize.
This test needs a real, richly-laid-out document to make "at least one
element, with a plausible type/bbox/confidence" a meaningful assertion,
so it goes straight at ``DoclingRecognizer.recognize()`` on one
rasterized page, bypassing ``docolage.parse()`` entirely.

Run it explicitly:

    pytest -m docling tests/test_docling_recognizer_smoke.py
"""

from __future__ import annotations

from pathlib import Path

import pypdfium2 as pdfium
import pytest

from docolage.rasterize import rasterize_page
from docolage.recognizer_docling import DoclingRecognizer

DATA_DIR = Path(__file__).parent.parent / "data"

@pytest.mark.docling
def test_recognize_returns_layout_elements_for_a_real_page(tmp_path):
    pdf = pdfium.PdfDocument(str(DATA_DIR / "paper.pdf"))
    image_path = rasterize_page(pdf, 0, tmp_path, "docling-smoke", dpi=200)

    recognizer = DoclingRecognizer()
    result = recognizer.recognize(image_path, page_id=1)
    elements = result.elements

    assert elements, "expected at least one recognized element on a real page"
    assert any(element.type in ("text", "heading-1") for element in elements)
    assert isinstance(result.markdown, str) and result.markdown.strip()

    for element in elements:
        assert isinstance(element.type, str) and element.type
        assert isinstance(element.content, str)
        assert isinstance(element.confidence, float)
        assert 0.0 <= element.confidence <= 1.0
        assert len(element.coord) == 4
        assert all(isinstance(c, int) for c in element.coord)

@pytest.mark.docling
def test_recognizer_reuses_loaded_models_across_pages(tmp_path):
    """Constructing once and calling recognize() twice must not error or
    reload models — the port implementation is meant to be constructed
    once per Worker and reused for every page (spec story 42)."""
    pdf = pdfium.PdfDocument(str(DATA_DIR / "paper.pdf"))
    image_path_1 = rasterize_page(pdf, 0, tmp_path, "docling-smoke", dpi=200)
    image_path_2 = rasterize_page(pdf, 1, tmp_path, "docling-smoke", dpi=200)

    recognizer = DoclingRecognizer()
    first = recognizer.recognize(image_path_1, page_id=1)
    second = recognizer.recognize(image_path_2, page_id=2)

    assert isinstance(first.elements, list)
    assert isinstance(second.elements, list)
