"""Thin OpenAI wrapper for the example pipeline: embeddings for chunks,
and a short doc_summary for docs_metadata. Reuses the same credential
resolution as the Core's Figure captioner (OPENAI_API_KEY env var) so
this pipeline needs no separate configuration -- the same key that
captions figures during parsing is used here.

Degrades to a no-op the same way as docolage.captioner_openai: missing
key or missing `openai` package returns None instead of raising, so the
pipeline can still run (chunks with embedding=NULL, a naive filename
-based doc_summary) end to end with no external dependency configured.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
DEFAULT_CHAT_MODEL = "gpt-4o-mini"


class OpenAIPipelineClient:
    def __init__(self, api_key: str, embedding_model: str = DEFAULT_EMBEDDING_MODEL, chat_model: str = DEFAULT_CHAT_MODEL) -> None:
        from openai import OpenAI  # type: ignore[import-not-found]

        self._client = OpenAI(api_key=api_key)
        self.embedding_model = embedding_model
        self.chat_model = chat_model

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Batch embed; OpenAI's embeddings endpoint accepts a list of
        inputs in one call and returns them in the same order."""
        if not texts:
            return []
        response = self._client.embeddings.create(model=self.embedding_model, input=texts)
        return [item.embedding for item in response.data]

    def summarize_document(self, filename: str, page_markdowns: list[str]) -> str:
        """A short (2-3 sentence) description of what the document is
        about, for docs_metadata.doc_summary -- meant to make a document
        findable by a human or a coarse doc-level search, not to
        substitute for reading its chunks."""
        excerpt = "\n\n---\n\n".join(page_markdowns)[:8000]
        response = self._client.chat.completions.create(
            model=self.chat_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Summarize the document in 2-3 sentences describing what it "
                        "is and what it covers, so someone searching a document "
                        "index can tell if it's relevant without opening it."
                    ),
                },
                {"role": "user", "content": f"Filename: {filename}\n\n{excerpt}"},
            ],
        )
        return (response.choices[0].message.content or "").strip()


def build_pipeline_client() -> OpenAIPipelineClient | None:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        logger.warning("OPENAI_API_KEY not set -- embeddings and doc_summary will be skipped/naive.")
        return None
    try:
        return OpenAIPipelineClient(api_key=api_key)
    except ImportError:
        logger.warning("'openai' package not installed -- embeddings and doc_summary will be skipped/naive.")
        return None
