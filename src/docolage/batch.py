"""docolage.parse_udf — the batch entry point (ticket #11, ADR-0004).

``parse_udf`` is a thin wrapper over ``docolage.parser.parse``: every
document a caller reads through ``spark.read.format("binaryFile")`` and
runs through the function returned here goes through the exact same
code path as a single ``docolage.parse(path)`` call. The two must never
drift (ADR-0004) — this module reimplements no parsing logic, it only
adapts ``parse()`` to ``mapInPandas`` batch semantics:

- materialize each binaryFile row's ``content`` bytes to a temp file
  (docolage's format handlers dispatch on file extension and read from
  a filesystem path, not from bytes directly);
- resolve/load the VLM recognizer once per partition/executor call,
  not once per row, by doing that work before the ``for batch in
  batches`` loop (mapInPandas generator semantics: code before the
  loop runs once per partition, code inside runs once per batch);
- isolate document-level failures the same way #5 isolates page-level
  failures, so one unparseable document degrades to an error_status
  entry instead of failing the whole partition/job;
- serialize each result to JSON so callers can land it as a Delta
  VARIANT column via ``to_json()`` -> ``parse_json()``.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterator, Optional

from docolage import schema
from docolage.config import DocolageConfig
from docolage.parser import parse

if TYPE_CHECKING:
    import pandas as pd  # type: ignore[import-untyped]

PARSED_JSON_COLUMN = "parsed_json"


def _parse_content_bytes(path_uri: str, content: bytes, config: DocolageConfig) -> dict:
    """Write one binaryFile row's bytes to a temp file and hand it to the
    same ``parse()`` the single-document entry point uses.

    The temp file keeps the original basename (not a random name): format
    handlers derive output filenames (page PNGs, element crops) from the
    path's stem, so preserving it keeps ``parse_udf`` output identical to
    calling ``parse()`` directly on the same file (ADR-0004), rather than
    merely equivalent modulo filenames.
    """
    filename = Path(path_uri).name or "document"
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir) / filename
        tmp_path.write_bytes(content)
        return parse(str(tmp_path), config)


def _parse_row_isolated(path_uri: str, content: bytes, config: DocolageConfig) -> dict:
    """Document-level error isolation: a document that fails before or
    outside per-page isolation (unsupported format, corrupt container,
    missing LibreOffice, ...) must not raise out of the partition and
    take down sibling documents/the whole job."""
    try:
        return _parse_content_bytes(path_uri, content, config)
    except Exception as exc:  # noqa: BLE001 - deliberate document-level isolation boundary
        return schema.build_document(
            pages=[],
            elements=[],
            error_status=[
                {"page_id": None, "error_message": f"{type(exc).__name__}: {exc}"}
            ],
        )


def _resolve_recognizer(config: DocolageConfig) -> Optional[Any]:
    """Load the real VLM recognizer once, if configured and not already
    injected (e.g. a fake in tests)."""
    if config.vlm_recognizer is not None or not config.model_path:
        return config.vlm_recognizer
    from docolage.vlm_paddleocr import PaddleOCRVLRecognizer

    return PaddleOCRVLRecognizer(config.model_path)


def parse_udf(
    config: Optional[DocolageConfig] = None,
) -> Callable[[Iterator["pd.DataFrame"]], Iterator["pd.DataFrame"]]:
    """Return a function usable directly with ``df.mapInPandas(fn, schema)``.

    The returned function consumes the ``binaryFile`` schema (``path``,
    ``modificationTime``, ``length``, ``content``) with no reshaping and
    adds one column, ``parsed_json`` (string), holding the same dict
    ``docolage.parse()`` returns, JSON-encoded. Land it as a Delta
    VARIANT column with::

        from pyspark.sql.functions import col, parse_json

        parsed_df = df.mapInPandas(
            docolage.parse_udf(config), schema=df.schema.add("parsed_json", "string")
        )
        parsed_df.withColumn("parsed", parse_json(col("parsed_json"))).write.format(
            "delta"
        ).saveAsTable("catalog.schema.parsed_documents")

    The VLM recognizer (or any other model configured on ``config``) is
    resolved/loaded exactly once per partition, before any row is
    processed, so its load cost is amortized across the whole batch
    rather than paid per document.
    """
    base_config = config or DocolageConfig()

    def _mapper(batches: Iterator["pd.DataFrame"]) -> Iterator["pd.DataFrame"]:
        # Runs once per partition/executor call, before the first batch:
        # this is the "load once per executor, not per document" seam.
        recognizer = _resolve_recognizer(base_config)
        effective_config = (
            base_config
            if recognizer is base_config.vlm_recognizer
            else replace(base_config, vlm_recognizer=recognizer)
        )

        for batch in batches:
            parsed = [
                json.dumps(
                    _parse_row_isolated(row.path, bytes(row.content), effective_config)
                )
                for row in batch.itertuples(index=False)
            ]
            result = batch.copy()
            result[PARSED_JSON_COLUMN] = parsed
            yield result

    return _mapper
