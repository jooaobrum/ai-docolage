"""Example downstream RAG-prep pipeline: Load -> Process -> Chunk -> Store
embeddings, built on top of already-parsed docolage_service jobs.

Explicitly out of the core spec's scope (docs/spec/0002, "Out of Scope":
"Chunking and any downstream RAG-prep enhancement remain out of scope").
This package shows one way to build that layer on top of the parse
result rather than baking it into the API/worker.
"""
