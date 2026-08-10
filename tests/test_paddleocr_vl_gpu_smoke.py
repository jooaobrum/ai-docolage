"""GPU smoke test for the real PaddleOCRVLRecognizer (ticket #6).

Marked `@pytest.mark.gpu` and excluded from the default `pytest` run via
`addopts = "-m 'not gpu'"` in pyproject.toml — it needs an actual GPU,
PaddleOCR-VL-1.6 weights on a real (UC Volume-shaped) path, and the
`paddleocr`/`paddlex`/PaddlePaddle-GPU stack installed, none of which exist
in this sandbox.

This test was written but NOT run here — there is no GPU, no downloaded
weights, and `paddlex`/PaddlePaddle-GPU are not installed. Run it explicitly
on a GPU cluster with weights staged at a real path:

    pytest -m gpu tests/test_paddleocr_vl_gpu_smoke.py \
        --model-path=/Volumes/catalog/schema/models/paddleocr-vl-1.6 \
        --doc-path=/path/to/a/real/scanned.pdf

(model/doc paths below default to env vars for the same reason — no CLI
option plumbing was worth adding for a test that can't run here.)
"""

from __future__ import annotations

import os

import pytest

import docolage
from docolage.vlm_paddleocr import PaddleOCRVLRecognizer

MODEL_PATH = os.environ.get("DOCOLAGE_GPU_TEST_MODEL_PATH", "")
DOC_PATH = os.environ.get("DOCOLAGE_GPU_TEST_DOC_PATH", "")


@pytest.mark.gpu
def test_parses_real_scanned_document_end_to_end(tmp_path):
    assert MODEL_PATH, "set DOCOLAGE_GPU_TEST_MODEL_PATH to PaddleOCR-VL-1.6 weights"
    assert DOC_PATH, "set DOCOLAGE_GPU_TEST_DOC_PATH to a real scanned document"

    recognizer = PaddleOCRVLRecognizer(model_path=MODEL_PATH)
    config = docolage.DocolageConfig(
        model_path=MODEL_PATH,
        output_volume_path=str(tmp_path),
        vlm_recognizer=recognizer,
    )

    result = docolage.parse(DOC_PATH, config)

    elements = result["document"]["elements"]
    assert elements, "expected at least one recognized element"

    # Same contract as FakeVLMRecognizer (test_vlm_path.py): types, content,
    # confidence, bbox, reading order all populated.
    for element in elements:
        assert isinstance(element["type"], str) and element["type"]
        assert isinstance(element["content"], str)
        assert isinstance(element["confidence"], float)
        assert 0.0 <= element["confidence"] <= 1.0
        assert element["bbox"], "expected at least one bbox entry"
        for bbox in element["bbox"]:
            assert isinstance(bbox["page_id"], int)
            assert len(bbox["coord"]) == 4

    # Cross-page table merge and heading refinement, if this fixture spans
    # pages / has headings, should already be exercised by the shared
    # merge/build path used for both the fake and real recognizers — no
    # extra assertion needed beyond "did it run end to end without error".

    # THE CONFIDENCE CHECK THIS TICKET CALLS OUT AS RISKIEST: run this
    # manually against one clean page and one degraded/noisy page of the
    # same document, and confirm confidence is measurably lower on the
    # degraded page. This assertion is deliberately NOT encoded here — it
    # requires a document pair no fixture in this repo provides, and
    # asserting a specific threshold would be fabricating a result this
    # sandbox cannot produce.
