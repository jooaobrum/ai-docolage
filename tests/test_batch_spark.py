"""docolage.parse_udf: Spark batch entry point (ticket #11).

pyspark is an optional dependency (the ``spark`` extra) and is not
required to use ``docolage.parse()`` at all. Every test here is skipped
outright if pyspark cannot be imported, rather than failing the suite,
matching the honesty pattern used for the GPU-only tests in #6 and the
LibreOffice-binary tests in #10.
"""

from __future__ import annotations

import json

import pytest

pyspark = pytest.importorskip("pyspark")

from pyspark.sql import SparkSession  # noqa: E402
from pyspark.sql.types import StringType  # noqa: E402

import docolage  # noqa: E402
from docolage.batch import PARSED_JSON_COLUMN  # noqa: E402
from docolage.ports import FakeVLMRecognizer, LayoutElement  # noqa: E402


@pytest.fixture(scope="module")
def spark():
    try:
        session = (
            SparkSession.builder.master("local[2]")
            .appName("docolage-parse-udf-tests")
            .config("spark.ui.enabled", "false")
            .getOrCreate()
        )
    except RuntimeError as exc:
        # This sandbox's `pyspark` install is a Databricks Connect build
        # that refuses to start a local (non-Connect) SparkSession — it can
        # be imported but cannot actually run a local Spark job here. Skip
        # rather than fail: these tests need a real local/cluster Spark,
        # not just an importable pyspark module (matches #6/#10's honesty
        # pattern for infrastructure this sandbox can't provide).
        pytest.skip(f"local SparkSession unavailable in this environment: {exc}")
        return
    yield session
    session.stop()


def _binary_file_df(spark, *paths):
    return spark.read.format("binaryFile").load([str(p) for p in paths])


def _mapped(spark, df, config):
    output_schema = df.schema.add(PARSED_JSON_COLUMN, StringType())
    return df.mapInPandas(docolage.parse_udf(config), schema=output_schema)


def test_parse_udf_returns_a_mapinpandas_ready_callable(tmp_output_volume):
    config = docolage.DocolageConfig(output_volume_path=str(tmp_output_volume))
    fn = docolage.parse_udf(config)
    assert callable(fn)


def test_binary_file_dataframe_passes_through_with_no_reshaping(
    spark, fixtures_dir, tmp_output_volume
):
    df = _binary_file_df(spark, fixtures_dir / "simple.md")
    assert df.columns == ["path", "modificationTime", "length", "content"]

    config = docolage.DocolageConfig(output_volume_path=str(tmp_output_volume))
    mapped = _mapped(spark, df, config)

    # Original binaryFile columns survive untouched; only parsed_json is added.
    assert mapped.columns == ["path", "modificationTime", "length", "content", PARSED_JSON_COLUMN]
    row = mapped.collect()[0]
    assert row["length"] > 0
    assert row["content"] is not None


def test_parse_and_parse_udf_produce_identical_output_for_the_same_document(
    spark, fixtures_dir, tmp_output_volume
):
    """Explicit acceptance criterion for #11 (ADR-0004: no drift between
    the two entry points sharing one underlying implementation)."""
    fake = FakeVLMRecognizer(
        {1: [LayoutElement(type="text", content="Hello VLM", confidence=0.9, coord=[0, 0, 5, 5])]}
    )
    config = docolage.DocolageConfig(output_volume_path=str(tmp_output_volume), vlm_recognizer=fake)

    doc_path = fixtures_dir / "scanned.pdf"
    direct_result = docolage.parse(str(doc_path), config)

    df = _binary_file_df(spark, doc_path)
    mapped = _mapped(spark, df, config)
    rows = mapped.collect()
    assert len(rows) == 1
    batch_result = json.loads(rows[0][PARSED_JSON_COLUMN])

    # metadata.id is a fresh uuid4 per parse() call by design (schema.py) —
    # excluded from the identity check, everything else must match exactly.
    direct_result["metadata"].pop("id")
    batch_result["metadata"].pop("id")
    assert direct_result == batch_result


def test_parse_and_mapper_produce_identical_output_without_a_spark_session(
    fixtures_dir, tmp_output_volume
):
    """Same acceptance criterion as the Spark-session version above, but
    exercised by calling the mapper function returned by parse_udf()
    directly against a pandas DataFrame shaped like binaryFile's schema.
    This runs even when a real local/cluster SparkSession is unavailable
    (mapInPandas's contract is just "iterator of pandas DataFrames in,
    iterator of pandas DataFrames out" — no live SparkContext required to
    exercise that contract directly)."""
    import pandas as pd

    fake = FakeVLMRecognizer(
        {1: [LayoutElement(type="text", content="Hello VLM", confidence=0.9, coord=[0, 0, 5, 5])]}
    )
    config = docolage.DocolageConfig(output_volume_path=str(tmp_output_volume), vlm_recognizer=fake)

    doc_path = fixtures_dir / "scanned.pdf"
    direct_result = docolage.parse(str(doc_path), config)

    content = doc_path.read_bytes()
    batch_df = pd.DataFrame(
        [{"path": str(doc_path), "modificationTime": None, "length": len(content), "content": content}]
    )
    mapper = docolage.parse_udf(config)
    (result_df,) = list(mapper(iter([batch_df])))
    batch_result = json.loads(result_df.iloc[0][PARSED_JSON_COLUMN])

    direct_result["metadata"].pop("id")
    batch_result["metadata"].pop("id")
    assert direct_result == batch_result


def test_model_is_loaded_once_per_partition_not_per_document(fixtures_dir, tmp_output_volume):
    """Exercise the mapper's generator shape directly (no Spark session
    needed for this one): the recognizer-loading step must run before the
    per-row loop, once per call to the mapper, however many rows/batches
    it is handed."""
    import pandas as pd

    fake = FakeVLMRecognizer(
        {1: [LayoutElement(type="text", content="x", confidence=0.9, coord=[0, 0, 1, 1])]}
    )

    load_count = {"n": 0}

    def _fake_resolve(config):
        load_count["n"] += 1
        return fake

    import docolage.batch as batch_module

    original_resolve = batch_module._resolve_recognizer
    batch_module._resolve_recognizer = _fake_resolve
    try:
        config = docolage.DocolageConfig(output_volume_path=str(tmp_output_volume))
        mapper = docolage.parse_udf(config)

        doc_path = fixtures_dir / "scanned.pdf"
        content = doc_path.read_bytes()
        batch_df = pd.DataFrame(
            [
                {"path": str(doc_path), "modificationTime": None, "length": len(content), "content": content},
                {"path": str(doc_path), "modificationTime": None, "length": len(content), "content": content},
            ]
        )

        # Two batches handed to the same mapper call -> resolve must run
        # exactly once, not once per batch and not once per row.
        results = list(mapper(iter([batch_df, batch_df])))
        assert len(results) == 2
        assert load_count["n"] == 1
    finally:
        batch_module._resolve_recognizer = original_resolve


def test_one_bad_document_does_not_fail_the_partition(spark, fixtures_dir, tmp_output_volume):
    """Document-level error isolation: an unsupported/corrupt document in
    the batch must degrade to an error_status entry, not raise and take
    down sibling documents in the same partition."""
    import shutil

    bad_path = tmp_output_volume / "not_really_a.pdf"
    bad_path.write_bytes(b"this is not a valid pdf")
    good_path = fixtures_dir / "simple.md"

    fake = FakeVLMRecognizer({})
    config = docolage.DocolageConfig(output_volume_path=str(tmp_output_volume), vlm_recognizer=fake)

    df = _binary_file_df(spark, bad_path, good_path)
    mapped = _mapped(spark, df, config)
    rows = {row["path"]: json.loads(row[PARSED_JSON_COLUMN]) for row in mapped.collect()}

    assert len(rows) == 2
    bad_result = next(v for k, v in rows.items() if "not_really_a" in k)
    good_result = next(v for k, v in rows.items() if "simple.md" in k)

    assert bad_result["error_status"]
    assert bad_result["error_status"][0]["page_id"] is None
    assert good_result["error_status"] == []
    assert good_result["document"]["elements"]


def test_error_status_survives_batch_path_for_per_page_failures(
    spark, fixtures_dir, tmp_output_volume
):
    fake = FakeVLMRecognizer(
        {
            1: [LayoutElement(type="text", content="Page one", confidence=0.9, coord=[0, 0, 1, 1])],
            2: [LayoutElement(type="text", content="Page two", confidence=0.9, coord=[0, 0, 1, 1])],
        },
        failing_pages={2: RuntimeError("GPU OOM on page 2")},
    )
    config = docolage.DocolageConfig(output_volume_path=str(tmp_output_volume), vlm_recognizer=fake)

    df = _binary_file_df(spark, fixtures_dir / "scanned_multi_page.pdf")
    mapped = _mapped(spark, df, config)
    row = mapped.collect()[0]
    result = json.loads(row[PARSED_JSON_COLUMN])

    assert len(result["error_status"]) == 1
    assert result["error_status"][0]["page_id"] == 2
    assert "GPU OOM" in result["error_status"][0]["error_message"]
    assert [e["content"] for e in result["document"]["elements"]] == ["Page one"]


def test_one_bad_document_does_not_fail_the_partition_without_a_spark_session(
    fixtures_dir, tmp_output_volume
):
    """Document-level isolation, exercised the same way as the
    no-SparkSession identical-output test above: a corrupt/unsupported
    document in the batch must not raise out of the mapper and take
    sibling rows (in the same batch) down with it."""
    import pandas as pd

    bad_path = tmp_output_volume / "not_really_a.pdf"
    bad_path.write_bytes(b"this is not a valid pdf")
    good_path = fixtures_dir / "simple.md"

    fake = FakeVLMRecognizer({})
    config = docolage.DocolageConfig(output_volume_path=str(tmp_output_volume), vlm_recognizer=fake)

    batch_df = pd.DataFrame(
        [
            {
                "path": str(p),
                "modificationTime": None,
                "length": len(p.read_bytes()),
                "content": p.read_bytes(),
            }
            for p in (bad_path, good_path)
        ]
    )
    mapper = docolage.parse_udf(config)
    (result_df,) = list(mapper(iter([batch_df])))

    bad_result = json.loads(result_df.iloc[0][PARSED_JSON_COLUMN])
    good_result = json.loads(result_df.iloc[1][PARSED_JSON_COLUMN])

    assert bad_result["error_status"]
    assert bad_result["error_status"][0]["page_id"] is None
    assert good_result["error_status"] == []
    assert good_result["document"]["elements"]
