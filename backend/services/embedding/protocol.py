"""Embedder protocol — pluggable embedding backends for the Brain.

The Protocol mirrors the ``GroundingChecker`` pattern used by the
synapse-validation pipeline. Two existing benefits applied here:

    1. Hot-swap backends without touching consumers
       (synapse_incremental, retrieval/hybrid, ...).
    2. Tests inject deterministic stub embedders without standing up an
       HTTP server.

Embeddings are returned as plain ``list[float]`` — callers persist them
as packed BLOBs (see ``services/embedding/codec.py`` once introduced) or
keep them in memory for cosine math. Mixed-dimension responses are an
upstream bug; backends MUST raise ``EmbeddingError`` rather than return
an inconsistent batch.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


class EmbeddingError(Exception):
    """Raised by an ``Embedder`` when the backend can't produce embeddings.

    Distinct from ``Exception`` so callers can ``except EmbeddingError`` and
    fall back to FTS5-only retrieval cleanly.
    """


@runtime_checkable
class Embedder(Protocol):
    """A pluggable embedding-backend."""

    #: Short, stable identifier (e.g. ``"litellm-router"`` or ``"onnx-minilm"``).
    name: str

    #: Embedding vector dimensionality. ``0`` until the first ``embed`` call —
    #: backends MAY learn it from the first response and pin it.
    dim: int

    #: Model identifier passed through to the upstream backend (e.g. ``"bge-m3"``).
    #: Persisted alongside the vector so re-embedding is gated on model change.
    model_id: str

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding per text, same order.

        Raises ``EmbeddingError`` on backend failure; never returns a
        shorter list than the input.
        """
        ...

    async def embed_one(self, text: str) -> list[float]:
        """Convenience wrapper — single-text variant of ``embed``."""
        ...
