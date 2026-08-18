"""Standalone element crops (spec "Element crops", CONTEXT.md "Element crop").

Table and Figure elements get their own cropped image file under the
configured output path, cut from the already-rasterized page PNG at
the element's bbox coordinates. Text and formula elements never call
this — they stay represented by content + bbox into the page PNG alone.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

CROPPABLE_TYPES = frozenset({"table", "figure"})


def crop_element(
    page_image_path: str, coord: list, output_dir: Path, doc_stem: str, page_id: int, element_id: int
) -> str:
    """Crop coord (a [left, top, right, bottom] pixel box on the page PNG
    at page_image_path) out to its own file under output_dir. Returns the
    crop's path as a string, to be used as the element's image_uri."""
    output_dir.mkdir(parents=True, exist_ok=True)
    left, top, right, bottom = coord
    with Image.open(page_image_path) as page_image:
        cropped = page_image.crop((left, top, right, bottom))
        crop_path = output_dir / f"{doc_stem}-page-{page_id}-element-{element_id}-crop.png"
        cropped.save(crop_path)
    return str(crop_path)
