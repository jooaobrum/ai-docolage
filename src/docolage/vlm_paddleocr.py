"""Real VLMRecognizer backed by PaddleOCR's own ``PaddleOCRVL`` pipeline.

ADR-0005: this module MUST drive the official ``paddleocr.PaddleOCRVL``
pipeline object, never prompt the underlying VLM directly — that pipeline
object is what supplies layout detection (PP-DocLayoutV3, PaddlePaddle),
cross-page table merge, and heading-hierarchy refinement. Skipping it and
talking to the VLM raw would silently drop all three per PaddleOCR-VL's own
docs.

The VL recognition sub-stage runs through the pipeline's "native" backend,
which is PaddleOCR-VL's in-process ``transformers``-based execution (ADR-0005
calls this "the transformers backend"; the pipeline's own parameter name for
it is ``vl_rec_backend="native"``). The deferred vLLM upgrade path is exposed
here only as passthrough constructor parameters (``vl_rec_backend``,
``vl_rec_server_url``) — selecting ``vl_rec_backend="vllm-server"`` plus a
running ``vllm serve`` process is the entire migration; no other code in this
module or in docolage needs to change.

Environment note: this class is written against the documented/public
``paddleocr.PaddleOCRVL`` constructor and ``predict()`` API (verified by
downloading and reading the installed ``paddleocr`` 3.7.0 wheel's
``_pipelines/paddleocr_vl.py`` source directly). The exact shape of a
*prediction result* (``parsing_res_list`` block dicts: keys, per-block score
presence) could NOT be verified in this sandbox — the ``paddleocr`` package
here has no ``paddlex`` runtime dependency installed (deliberately: it pulls
in PaddlePaddle/CUDA), so no pipeline could actually be constructed or run.
The result-parsing code below is a best-effort mapping onto PP-DocLayoutV3's
documented block-label set and PaddleX's documented ``parsing_res_list``
shape, marked inline. It has not been exercised against real model output.

CONFIDENCE IS THE RISKIEST ASSUMPTION IN THIS DESIGN. #8's escalation logic
thresholds Figures on ``LayoutElement.confidence``. PaddleOCR-VL's *layout
detection* stage (PP-DocLayoutV3) does emit a per-box detection score, but
the VL *recognition* stage (the part that actually produces the figure
description or OCR'd text) is not documented to emit its own per-block
confidence — recognition is generative decoding, not a scored classifier.
This adapter falls back to the layout-detection box score, by position, when
a block carries no explicit score of its own, and defaults to 1.0 (i.e. "no
usable signal, don't threshold on it") when neither is present. That fallback
chain is a guess, not a verified fact: it has not been checked against a real
pipeline run because none was available here. Before building #8's escalation
threshold on this value, run it on a GPU against a real scanned page with a
low-quality figure and confirm the resulting confidence actually differs from
a clean, well-recognized figure. If it doesn't move, the ticket's own
documented fallback — a description length/emptiness heuristic — should be
used for escalation instead.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from docolage.ports import LayoutElement

# PP-DocLayoutV3's documented label set, mapped onto the small type
# vocabulary docolage's schema/tests use (see tests/test_vlm_path.py). Labels
# not listed here fall back to "text". Heading level is not derivable from
# the label alone (PP-DocLayoutV3 does not expose a title level), so all
# title-like blocks map to "heading-1" — a known simplification, not a bug.
_LABEL_TYPE_MAP: dict[str, str] = {
    "doc_title": "heading-1",
    "paragraph_title": "heading-1",
    "figure_title": "heading-1",
    "table_title": "heading-1",
    "text": "text",
    "abstract": "text",
    "content": "text",
    "reference": "text",
    "reference_content": "text",
    "footnote": "text",
    "algorithm": "text",
    "aside_text": "text",
    "number": "text",
    "table": "table",
    "formula": "formula",
    "formula_number": "formula",
    "image": "figure",
    "figure": "figure",
    "chart": "figure",
    "seal": "figure",
    "header": "header",
    "header_image": "header",
    "footer": "footer",
    "footer_image": "footer",
}

_EXPECTED_SUBDIRS = ("layout_detection", "vl_rec")


class ModelWeightsNotFoundError(RuntimeError):
    """Raised at construction time: config.model_path is missing, empty, or
    does not look like a valid PaddleOCR-VL weights directory.

    Raised early and specifically so a misconfigured Volume path fails at
    setup with an actionable message, not partway through a batch job.
    """


class PaddleOCRVLRecognizer:
    """Real ``VLMRecognizer`` (docolage.ports) backed by PaddleOCR-VL-1.6.

    Drives ``paddleocr.PaddleOCRVL`` — never raw model prompting (ADR-0005).
    Weights load once, at construction, from a Unity Catalog Volume path
    (``model_path``); nothing is downloaded at runtime. See module docstring
    for the confidence-signal caveat: it has not been verified on a GPU.
    """

    def __init__(
        self,
        model_path: str,
        layout_detection_model_dir: Optional[str] = None,
        vl_rec_model_dir: Optional[str] = None,
        vl_rec_backend: str = "native",
        vl_rec_server_url: Optional[str] = None,
        pipeline_version: str = "v1.6",
        device: Optional[str] = None,
    ) -> None:
        layout_dir = layout_detection_model_dir or str(Path(model_path) / "layout_detection")
        vl_rec_dir = vl_rec_model_dir or str(Path(model_path) / "vl_rec")

        self._validate_weights_present(model_path, layout_dir, vl_rec_dir)

        try:
            from paddleocr import PaddleOCRVL
        except ImportError as exc:
            raise ImportError(
                "PaddleOCRVLRecognizer requires the 'paddleocr' package (plus its "
                "'paddlex' runtime and a matching PaddlePaddle build), none of which "
                "are installed in this environment. Install them on the GPU cluster "
                "before selecting the real recognizer via DocolageConfig, e.g.: "
                "`pip install \"paddleocr[doc-parser]\"` and a CUDA-matched "
                "`paddlepaddle-gpu` per the PaddlePaddle install guide. "
                "(FakeVLMRecognizer remains available for CPU-only testing.)"
            ) from exc

        try:
            self._pipeline = PaddleOCRVL(
                pipeline_version=pipeline_version,
                layout_detection_model_dir=layout_dir,
                vl_rec_model_dir=vl_rec_dir,
                vl_rec_backend=vl_rec_backend,
                vl_rec_server_url=vl_rec_server_url,
                device=device,
            )
        except Exception as exc:
            raise RuntimeError(
                f"Failed to construct the PaddleOCRVL pipeline from "
                f"model_path={model_path!r} "
                f"(layout_detection_model_dir={layout_dir!r}, "
                f"vl_rec_model_dir={vl_rec_dir!r}, vl_rec_backend={vl_rec_backend!r}). "
                "The weight files were found on disk but PaddleOCR-VL rejected them "
                "— check that they match the PaddleOCR-VL-1.6 layout expected by the "
                "installed paddleocr/paddlex version."
            ) from exc

    @staticmethod
    def _validate_weights_present(model_path: str, layout_dir: str, vl_rec_dir: str) -> None:
        if not model_path:
            raise ModelWeightsNotFoundError(
                "DocolageConfig.model_path is empty. PaddleOCRVLRecognizer needs a "
                "UC Volume path to the PaddleOCR-VL-1.6 weights, e.g. "
                "'/Volumes/catalog/schema/models/paddleocr-vl-1.6'."
            )

        root = Path(model_path)
        if not root.is_dir():
            raise ModelWeightsNotFoundError(
                f"config.model_path {model_path!r} does not exist or is not a "
                "directory. Upload the PaddleOCR-VL-1.6 weights to a UC Volume and "
                "point model_path at that Volume path before constructing "
                "PaddleOCRVLRecognizer."
            )

        for expected, resolved in zip(_EXPECTED_SUBDIRS, (layout_dir, vl_rec_dir)):
            path = Path(resolved)
            if not path.is_dir() or not any(path.iterdir()):
                raise ModelWeightsNotFoundError(
                    f"Expected PaddleOCR-VL weights for {expected!r} at {resolved!r}, "
                    "but that directory is missing or empty. model_path "
                    f"({model_path!r}) must contain a 'layout_detection' subdirectory "
                    "(PP-DocLayoutV3 weights) and a 'vl_rec' subdirectory "
                    "(PaddleOCR-VL recognition weights) — or pass explicit "
                    "layout_detection_model_dir / vl_rec_model_dir if your Volume "
                    "layout differs."
                )

    def recognize(self, image_path: str, page_id: int) -> list[LayoutElement]:
        """Run one rasterized page PNG through the PaddleOCRVL pipeline.

        Returns elements in the pipeline's own reading order (PaddleOCR-VL's
        post-processing already sorts ``parsing_res_list`` into reading
        order; this method does not re-sort).
        """
        results = self._pipeline.predict(image_path)
        if not results:
            return []

        result = results[0]
        blocks = _get(result, "parsing_res_list", [])
        layout_boxes = _get(_get(result, "layout_det_res", {}), "boxes", [])

        elements: list[LayoutElement] = []
        for index, block in enumerate(blocks):
            label = _get(block, "block_label", "text")
            content = _get(block, "block_content", "") or ""
            coord = list(_get(block, "block_bbox", []) or [])
            element_type = _LABEL_TYPE_MAP.get(label, "text")

            confidence = _extract_confidence(block, layout_boxes, index)

            description = content if element_type == "figure" else ""

            elements.append(
                LayoutElement(
                    type=element_type,
                    content=content,
                    confidence=confidence,
                    coord=coord,
                    description=description,
                )
            )

        return elements


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Fetch ``key`` from either a dict-like or attribute-like result object.

    PaddleX pipeline results support both mapping and attribute access in
    different versions; without a live install to check against, this stays
    defensive rather than committing to one API shape.
    """
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _extract_confidence(block: Any, layout_boxes: list, index: int) -> float:
    """Best-effort per-block confidence (see module docstring caveat).

    Prefers a score carried directly on the block; falls back to the
    layout-detection box at the same position; defaults to 1.0 (no signal)
    if neither is present.
    """
    direct = _get(block, "block_score", None)
    if direct is None:
        direct = _get(block, "score", None)
    if direct is not None:
        return float(direct)

    if index < len(layout_boxes):
        box_score = _get(layout_boxes[index], "score", None)
        if box_score is not None:
            return float(box_score)

    return 1.0
