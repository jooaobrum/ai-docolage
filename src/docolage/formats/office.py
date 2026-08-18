"""DOCX/PPTX format handler (ticket #10).

Deliberately thin: convert the office document to PDF via headless
LibreOffice, then hand the resulting PDF straight to
docolage.formats.pdf.parse. No second schema mapping and no
format-specific branch after conversion — the existing rasterize ->
recognize -> parse path is reused unchanged, so DOCX/PPTX output is
structurally identical to an equivalent PDF's.
"""

from __future__ import annotations

from pathlib import Path

from docolage.config import DocolageConfig
from docolage.formats import pdf as pdf_format
from docolage.office_convert import convert_to_pdf


def parse(path: Path, config: DocolageConfig) -> dict:
    output_dir = Path(config.output_path)
    converted_pdf_path = convert_to_pdf(
        path, output_dir, timeout_seconds=config.office_convert_timeout_seconds
    )
    return pdf_format.parse(converted_pdf_path, config)
