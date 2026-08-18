"""docolage — self-hosted document parsing.

Public interface: a single entry point, ``docolage.parse`` (ADR-0011
supersedes ADR-0004's dual-entrypoint design; the Spark/Databricks
``parse_udf`` batch entry point has been retired in favour of the
Service).
"""

from docolage.config import DocolageConfig
from docolage.parser import parse


def __getattr__(name: str):
    if name in ("OpenAICaptioner", "build_openai_captioner", "resolve_openai_api_key"):
        from docolage import captioner_openai

        return getattr(captioner_openai, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "parse",
    "DocolageConfig",
    "OpenAICaptioner",
    "build_openai_captioner",
    "resolve_openai_api_key",
]
