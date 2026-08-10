"""Errors raised directly to the caller of docolage.parse.

Per-page failures during parsing are not raised — they are recorded in
the output's error_status array (ADR-0006, per-page error isolation).
These exceptions are for problems that make parsing a document
impossible from the start.
"""

from __future__ import annotations


class UnsupportedFormatError(ValueError):
    def __init__(self, extension: str, path: str, supported_extensions):
        self.extension = extension
        self.path = path
        supported = ", ".join(sorted(supported_extensions))
        super().__init__(
            f"Unsupported file format {extension!r} for {path!r}. "
            f"docolage currently supports: {supported}."
        )


class LibreOfficeNotFoundError(RuntimeError):
    """Raised when the ``soffice`` binary is not on PATH.

    DOCX/PPTX support (ticket #10) normalizes those formats to PDF via
    headless LibreOffice before they reach the standard rasterize ->
    Router -> parse path. LibreOffice cannot be pip-installed; it must
    be present on the cluster (see docs/office-formats.md), which makes
    a missing binary a setup problem rather than a bug in a specific
    document. This surfaces that as an actionable message instead of a
    raw FileNotFoundError/subprocess trace.
    """

    def __init__(self, path: str):
        self.path = path
        super().__init__(
            f"Cannot convert {path!r}: LibreOffice ('soffice') is not installed "
            "or not on PATH. docolage requires headless LibreOffice to parse "
            "DOCX/PPTX files by converting them to PDF. Install LibreOffice "
            "(e.g. `apt-get install libreoffice` on Debian/Ubuntu, `brew install "
            "--cask libreoffice` on macOS, or add it to your Databricks cluster "
            "init script) and ensure `soffice` is on PATH."
        )


class DocumentConversionError(RuntimeError):
    """Raised when LibreOffice is present but fails to convert a specific
    file (corrupt file, LibreOffice crash, non-zero exit code)."""

    def __init__(self, path: str, detail: str):
        self.path = path
        self.detail = detail
        super().__init__(
            f"LibreOffice failed to convert {path!r} to PDF: {detail}. "
            "The file may be corrupt or unsupported by LibreOffice; try "
            "opening it in LibreOffice/Word/PowerPoint directly to confirm "
            "it is valid."
        )
