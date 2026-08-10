"""Markdown -> typed elements, in reading order.

Markdown bypasses rasterization, the Router, and the GPU entirely (spec
"Ingestion and format normalization"): its existing structure already
maps onto the element schema, so this module reads the source text
directly rather than rendering it to an image first.

Supported blocks: ATX headings (``#`` through ``######``, hierarchy
preserved via a ``heading-<level>`` type), GitHub-flavored pipe tables
(rendered to HTML), and everything else as plain text paragraphs.
"""

from __future__ import annotations

import re

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_TABLE_ROW_RE = re.compile(r"^\s*\|?(.+?)\|?\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def _split_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.strip() == "":
            if current:
                blocks.append("\n".join(current))
                current = []
        else:
            current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def _is_table_block(lines: list[str]) -> bool:
    if len(lines) < 2:
        return False
    return "|" in lines[0] and bool(_TABLE_SEP_RE.match(lines[1]))


def _split_row(line: str) -> list[str]:
    m = _TABLE_ROW_RE.match(line)
    inner = m.group(1) if m else line
    return [cell.strip() for cell in inner.split("|")]


def _table_to_html(lines: list[str]) -> str:
    header = _split_row(lines[0])
    body_rows = [_split_row(row) for row in lines[2:]]
    parts = ["<table>", "<thead>", "<tr>"]
    parts.extend(f"<th>{cell}</th>" for cell in header)
    parts.append("</tr>")
    parts.append("</thead>")
    parts.append("<tbody>")
    for row in body_rows:
        parts.append("<tr>")
        parts.extend(f"<td>{cell}</td>" for cell in row)
        parts.append("</tr>")
    parts.append("</tbody>")
    parts.append("</table>")
    return "".join(parts)


def parse_markdown(text: str) -> list[dict]:
    """Return an ordered list of ``{"type": ..., "content": ...}`` blocks."""
    blocks: list[dict] = []
    for block in _split_blocks(text):
        lines = block.splitlines()
        heading_match = _HEADING_RE.match(lines[0]) if lines else None

        if heading_match and len(lines) == 1:
            level = len(heading_match.group(1))
            blocks.append({"type": f"heading-{level}", "content": heading_match.group(2).strip()})
            continue

        if _is_table_block(lines):
            blocks.append({"type": "table", "content": _table_to_html(lines)})
            continue

        blocks.append({"type": "text", "content": block.strip()})

    return blocks
