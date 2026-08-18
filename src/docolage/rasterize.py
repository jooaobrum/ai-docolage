"""Page rasterization via pypdfium2, at a fixed 200 DPI (spec).

Every page becomes a PNG under the configured output path — this is
where image_uri comes from for free, ahead of the Recognizer's GPU work.
"""

from __future__ import annotations

from pathlib import Path

import pypdfium2 as pdfium

_POINTS_PER_INCH = 72


def rasterize_page(
    pdf: "pdfium.PdfDocument", page_index: int, output_dir: Path, doc_stem: str, dpi: int
) -> str:
    """Render a single page of pdf to a PNG under output_dir. Raises on
    render failure so callers can isolate it per page (ADR-0006)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    scale = dpi / _POINTS_PER_INCH
    page = pdf[page_index]
    bitmap = page.render(scale=scale)
    pil_image = bitmap.to_pil()
    image_path = output_dir / f"{doc_stem}-page-{page_index + 1}.png"
    pil_image.save(image_path)
    return str(image_path)


def rasterize_pdf(pdf: "pdfium.PdfDocument", output_dir: Path, doc_stem: str, dpi: int) -> list[str]:
    """Render every page of pdf to a PNG under output_dir. Returns image
    paths (as strings) in page order, 1 per page."""
    return [
        rasterize_page(pdf, page_index, output_dir, doc_stem, dpi)
        for page_index in range(len(pdf))
    ]
