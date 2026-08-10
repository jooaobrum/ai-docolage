"""Per-page error isolation (ADR-0006, ticket #5).

One bad page must never discard GPU work already spent on its siblings,
and error_status must stay a queryable array even with zero errors.
"""

import docolage
from docolage.ports import FakeVLMRecognizer, LayoutElement


def test_one_failing_page_is_isolated_and_siblings_still_produce_elements(
    fixtures_dir, tmp_output_volume
):
    fake = FakeVLMRecognizer(
        {
            1: [
                LayoutElement(
                    type="text", content="Page one content", confidence=0.9, coord=[0, 0, 10, 10]
                )
            ],
            2: [
                LayoutElement(
                    type="text", content="Page two content", confidence=0.9, coord=[0, 0, 10, 10]
                )
            ],
        },
        failing_pages={2: TimeoutError("VLM recognizer timed out after 30s")},
    )
    config = docolage.DocolageConfig(output_volume_path=str(tmp_output_volume), vlm_recognizer=fake)

    result = docolage.parse(str(fixtures_dir / "scanned_multi_page.pdf"), config)

    error_status = result["error_status"]
    assert isinstance(error_status, list)
    assert len(error_status) == 1
    assert error_status[0]["page_id"] == 2
    assert "timed out" in error_status[0]["error_message"]

    elements = result["document"]["elements"]
    assert len(elements) == 1
    assert elements[0]["content"] == "Page one content"
    page_ids = {bbox["page_id"] for e in elements for bbox in e["bbox"]}
    assert page_ids == {1}

    # Page 1 was rasterized and still recorded even though page 2 failed.
    page_ids_recorded = {p["id"] for p in result["document"]["pages"]}
    assert 1 in page_ids_recorded


def test_error_status_is_always_an_array_with_no_failures(fixtures_dir, tmp_output_volume):
    fake = FakeVLMRecognizer(
        {1: [LayoutElement(type="text", content="Hello", confidence=0.9, coord=[0, 0, 1, 1])]}
    )
    config = docolage.DocolageConfig(output_volume_path=str(tmp_output_volume), vlm_recognizer=fake)

    result = docolage.parse(str(fixtures_dir / "scanned.pdf"), config)

    assert result["error_status"] == []
    assert isinstance(result["error_status"], list)


def test_error_status_entries_are_json_serializable_and_sql_findable(
    fixtures_dir, tmp_output_volume
):
    import json

    fake = FakeVLMRecognizer(
        {1: []},
        failing_pages={1: RuntimeError("GPU OOM while recognizing page")},
    )
    config = docolage.DocolageConfig(output_volume_path=str(tmp_output_volume), vlm_recognizer=fake)

    result = docolage.parse(str(fixtures_dir / "scanned.pdf"), config)

    # Round-trips through JSON (Variant-friendly), and the predicate a data
    # engineer would write in SQL — "does error_status have entries" —
    # translates directly to a non-empty-array check here.
    serialized = json.dumps(result)
    reloaded = json.loads(serialized)
    assert len(reloaded["error_status"]) > 0
    assert reloaded["error_status"][0]["error_message"] == "GPU OOM while recognizing page"
    assert reloaded["error_status"][0]["page_id"] == 1


def test_failing_page_recognizer_does_not_prevent_other_pages_from_being_attempted(
    fixtures_dir, tmp_output_volume
):
    """A failure on an earlier page must not short-circuit later pages —
    GPU work already spent rasterizing/recognizing them must survive."""
    fake = FakeVLMRecognizer(
        {
            2: [
                LayoutElement(
                    type="text", content="Second page survives", confidence=0.9, coord=[0, 0, 1, 1]
                )
            ]
        },
        failing_pages={1: RuntimeError("corrupt render on page 1")},
    )
    config = docolage.DocolageConfig(output_volume_path=str(tmp_output_volume), vlm_recognizer=fake)

    result = docolage.parse(str(fixtures_dir / "scanned_multi_page.pdf"), config)

    assert {e["page_id"] for e in result["error_status"]} == {1}
    contents = [e["content"] for e in result["document"]["elements"]]
    assert contents == ["Second page survives"]
