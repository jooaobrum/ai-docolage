"""docolage — self-hosted document parsing on Databricks GPU compute.

Public interface: two entry points over one shared implementation
(ADR-0004).
"""

from docolage.batch import parse_udf
from docolage.config import DocolageConfig
from docolage.parser import parse


def __getattr__(name: str):
    # Lazy: PaddleOCRVLRecognizer pulls in the optional `paddleocr` /
    # `paddlex` / PaddlePaddle-GPU dependency stack, which markdown-only or
    # test installs should not be forced to import at package-import time.
    if name == "PaddleOCRVLRecognizer":
        from docolage.vlm_paddleocr import PaddleOCRVLRecognizer

        return PaddleOCRVLRecognizer
    # Lazy: OpenAICaptioner/build_openai_captioner pull in the optional
    # `openai` client, which installs that never escalate figures
    # shouldn't be forced to import at package-import time (ticket #9).
    if name in ("OpenAICaptioner", "build_openai_captioner", "resolve_openai_api_key"):
        from docolage import captioner_openai

        return getattr(captioner_openai, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "parse",
    "parse_udf",
    "DocolageConfig",
    "PaddleOCRVLRecognizer",
    "OpenAICaptioner",
    "build_openai_captioner",
    "resolve_openai_api_key",
]
