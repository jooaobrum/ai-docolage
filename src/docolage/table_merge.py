"""Merge a table's <tbody> rows across a page-break split (spec story 23).

A table spanning a page break arrives from the VLM recognizer as two
separate table elements, one per page. This concatenates their body
rows into a single HTML table rather than requiring callers to stitch
split tables themselves downstream.
"""

from __future__ import annotations

import re

_TBODY_RE = re.compile(r"<tbody>(.*?)</tbody>", re.DOTALL)


def merge_table_html(first_html: str, second_html: str) -> str:
    first_rows = _TBODY_RE.search(first_html)
    second_rows = _TBODY_RE.search(second_html)

    if not first_rows or not second_rows:
        return first_html + second_html

    merged_body = first_rows.group(1) + second_rows.group(1)
    return first_html[: first_rows.start(1)] + merged_body + first_html[first_rows.end(1) :]
