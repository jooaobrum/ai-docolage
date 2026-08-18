"""Turn one page's structured elements (docolage.schema.make_element shape:
id/type/content/confidence/description/bbox/image_uri/captioned) into a
list of semantically-scoped chunks, ready for embedding.

Two things make a chunk "semantic" rather than an arbitrary character
window:

1. **Section-scoped splitting.** Elements are already in reading order
   (the Recognizer port's contract), so a new chunk starts at every
   heading, and every chunk records the heading text it falls under
   (`section`) -- so embedding "section + content" together (see
   pipeline.py) keeps a chunk meaningful even read in isolation.
2. **Standalone image chunks.** A figure with a caption is promoted to
   its own chunk rather than only appearing as an inline placeholder
   inside its surrounding text chunk -- a caption buried in the middle
   of a long section chunk is hard to retrieve on; as its own chunk, a
   query matching the caption's meaning finds it directly. A table
   still renders as an inline `[TABLE]` placeholder in the surrounding
   text chunk (tables rarely have a caption to search on independently,
   and their HTML content is already searchable inline).
"""

from __future__ import annotations

from dataclasses import dataclass

_HEADING_TYPES = {"heading-1", "heading-2", "heading-3", "title"}

# Boilerplate repeated on every page, no semantic content for retrieval.
_SKIP_TYPES = {"header", "footer"}


@dataclass
class Chunk:
    page_number: int
    chunk_index: int
    chunk_type: str  # "text" | "image"
    section: str | None
    content: str


def _render_table(element: dict) -> str:
    path = element.get("image_uri") or "<no crop stored>"
    content = (element.get("content") or "").strip()
    return f"[TABLE]\npath={path}\n{content}\n[/TABLE]"


def _render_image_chunk(element: dict, section: str | None) -> str:
    path = element.get("image_uri") or "<no crop stored>"
    caption = (element.get("description") or "").strip()
    caption_line = f'caption="{caption}"' if caption else "(no caption)"
    section_line = f"section={section}\n" if section else ""
    return f"[IMAGE]\n{section_line}path={path}\n{caption_line}\n[/IMAGE]"


def build_page_chunks(page_number: int, elements: list[dict]) -> list[Chunk]:
    """Walk a page's elements in order, splitting a new text chunk at
    every heading and promoting captioned figures to their own image
    chunk. `chunk_index` is assigned sequentially in emission order so
    it's stable regardless of chunk_type."""
    chunks: list[Chunk] = []
    current_section: str | None = None
    current_lines: list[str] = []
    chunk_index = 0

    def _flush_text_chunk() -> None:
        nonlocal chunk_index
        content = "\n\n".join(part for part in current_lines if part)
        if content:
            chunks.append(
                Chunk(
                    page_number=page_number,
                    chunk_index=chunk_index,
                    chunk_type="text",
                    section=current_section,
                    content=content,
                )
            )
            chunk_index += 1
        current_lines.clear()

    for element in elements:
        element_type = element.get("type", "text")
        if element_type in _SKIP_TYPES:
            continue

        if element_type in _HEADING_TYPES:
            _flush_text_chunk()
            current_section = (element.get("content") or "").strip() or current_section
            heading_prefix = "# " if element_type in ("heading-1", "title") else "## "
            current_lines.append(f"{heading_prefix}{current_section}")
            continue

        if element_type == "figure":
            caption = (element.get("description") or "").strip()
            if caption:
                # Meaningful on its own -- promote to a standalone chunk
                # rather than burying the caption inline. Flush any
                # pending text first so reading order is preserved: the
                # image chunk must land between what precedes and what
                # follows it, not always at the end.
                _flush_text_chunk()
                chunks.append(
                    Chunk(
                        page_number=page_number,
                        chunk_index=chunk_index,
                        chunk_type="image",
                        section=current_section,
                        content=_render_image_chunk(element, current_section),
                    )
                )
                chunk_index += 1
            else:
                # No caption to search on independently -- keep it
                # inline so the surrounding text still notes it exists.
                current_lines.append(_render_image_chunk(element, current_section))
            continue

        if element_type == "table":
            current_lines.append(_render_table(element))
            continue

        content = (element.get("content") or "").strip()
        if content:
            current_lines.append(content)

    _flush_text_chunk()
    return chunks
