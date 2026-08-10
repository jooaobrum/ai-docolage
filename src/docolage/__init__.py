"""docolage — self-hosted document parsing on Databricks GPU compute.

Public interface: two entry points over one shared implementation
(ADR-0004).
"""

from docolage.config import DocolageConfig
from docolage.parser import parse


def __getattr__(name: str):
    # Lazy: PaddleOCRVLRecognizer pulls in the optional `paddleocr` /
    # `paddlex` / PaddlePaddle-GPU dependency stack, which markdown-only or
    # test installs should not be forced to import at package-import time.
    if name == "PaddleOCRVLRecognizer":
        from docolage.vlm_paddleocr import PaddleOCRVLRecognizer

        return PaddleOCRVLRecognizer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["parse", "DocolageConfig", "PaddleOCRVLRecognizer"]
