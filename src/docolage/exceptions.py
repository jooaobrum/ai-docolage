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
