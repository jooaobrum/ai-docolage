"""docolage.parse — the one public seam (spec "The seam").

Format normalization dispatches on file extension to a per-format
handler, each of which returns the ai_parse_document-shaped dict
assembled via docolage.schema.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from docolage.config import DocolageConfig
from docolage.exceptions import UnsupportedFormatError
from docolage.formats import markdown as markdown_format

_HANDLERS: dict[str, Callable[[Path, DocolageConfig], dict]] = {
    ".md": markdown_format.parse,
    ".markdown": markdown_format.parse,
}


def _register_pdf_and_image_handlers() -> None:
    """Deferred import: pdf/image handlers depend on pypdfium2/Pillow,
    which markdown-only installs shouldn't be forced to exercise at
    import time."""
    from docolage.formats import pdf as pdf_format
    from docolage.formats import image as image_format
    from docolage.formats import office as office_format

    _HANDLERS.setdefault(".pdf", pdf_format.parse)
    _HANDLERS.setdefault(".png", image_format.parse)
    _HANDLERS.setdefault(".docx", office_format.parse)
    _HANDLERS.setdefault(".pptx", office_format.parse)


_register_pdf_and_image_handlers()


def parse(path: str, config: Optional[DocolageConfig] = None) -> dict:
    """Parse a single document into a plain Python dict.

    Args:
        path: filesystem path to the document.
        config: explicit DocolageConfig; defaults to DocolageConfig()
            when omitted (sufficient for formats that need no model
            path or output volume, e.g. Markdown).
    """
    config = config or DocolageConfig()
    file_path = Path(path)
    extension = file_path.suffix.lower()

    handler = _HANDLERS.get(extension)
    if handler is None:
        raise UnsupportedFormatError(extension, str(path), _HANDLERS.keys())

    return handler(file_path, config)
