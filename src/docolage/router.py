"""The Router: born-digital status alone, no table/figure pre-check.

Detecting tables or figures cheaply enough to matter would require
running layout inference, defeating the point of a CPU-only path
(CONTEXT.md "Router"). A page routes to the text-layer path only when
it has a usable embedded text layer; everything else goes to the VLM.
"""

from __future__ import annotations


def is_born_digital(pdfium_page) -> bool:
    """True if the page has an extractable embedded text layer."""
    textpage = pdfium_page.get_textpage()
    try:
        return textpage.count_chars() > 0
    finally:
        textpage.close()
