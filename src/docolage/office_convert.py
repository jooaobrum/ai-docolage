"""DOCX/PPTX -> PDF normalization via headless LibreOffice (ticket #10).

LibreOffice is an undeclared system dependency: it cannot be
pip-installed and must be present on the cluster (see
docs/office-formats.md). This module's only job is to shell out to
`soffice --headless --convert-to pdf` and turn its two failure modes
(binary missing, conversion failing for a specific file) into
actionable docolage exceptions instead of raw subprocess traces. Once
a PDF exists on disk, callers hand it to the existing
docolage.formats.pdf.parse path unchanged.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from docolage.exceptions import DocumentConversionError, LibreOfficeNotFoundError

_SOFFICE_BINARY = "soffice"
_CONVERT_TIMEOUT_SECONDS = 120


def convert_to_pdf(path: Path, output_dir: Path) -> Path:
    """Convert a DOCX/PPTX file at path to a PDF under output_dir via
    headless LibreOffice. Returns the path to the converted PDF.

    Raises:
        LibreOfficeNotFoundError: the ``soffice`` binary is not on PATH.
        DocumentConversionError: LibreOffice ran but failed to produce
            a PDF for this specific file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        result = subprocess.run(
            [
                _SOFFICE_BINARY,
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                str(output_dir),
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=_CONVERT_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise LibreOfficeNotFoundError(str(path)) from exc
    except subprocess.TimeoutExpired as exc:
        raise DocumentConversionError(str(path), "LibreOffice timed out during conversion") from exc

    converted_path = output_dir / f"{path.stem}.pdf"

    if result.returncode != 0 or not converted_path.exists():
        detail = (result.stderr or result.stdout or "no output produced").strip()
        raise DocumentConversionError(str(path), detail or f"exit code {result.returncode}")

    return converted_path
