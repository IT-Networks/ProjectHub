"""Embedding-backend layer for the Brain (T2.2).

Pluggable via the ``Embedder`` Protocol (see ``protocol.py``) so the rest
of the Brain code is backend-agnostic. Two concrete implementations land
under P2:

    LiteLLMEmbedder (default)   — routes through AI-Assist's /api/embed,
                                   which itself wraps the configured
                                   LiteLLM proxy. Zero new deps in
                                   ProjectHub-backend.
    ONNXEmbedder    (fallback)  — local CPU ONNX runtime; lands in a
                                   later commit when an offline path is
                                   wanted.

Singleton accessor ``get_default_embedder()`` is what knowledge.py +
synapse_*.py call. ``reset_default_embedder()`` is exposed for tests.
"""

from services.embedding.litellm_router import LiteLLMEmbedder
from services.embedding.protocol import Embedder

__all__ = [
    "Embedder",
    "LiteLLMEmbedder",
    "get_default_embedder",
    "reset_default_embedder",
]


_default_embedder: Embedder | None = None


def get_default_embedder() -> Embedder | None:
    """Return the configured embedder, or ``None`` when embedding is off.

    Reads ``settings.brain_embedding_enabled`` lazily. The instance is
    cached for the life of the process; tests clear it via
    ``reset_default_embedder``.
    """
    global _default_embedder
    try:
        from config import settings
    except Exception:  # pragma: no cover — defensive (test isolation)
        return None

    if not getattr(settings, "brain_embedding_enabled", False):
        return None
    if _default_embedder is None:
        # Phase-1: only LiteLLMEmbedder is wired. Adding ONNXEmbedder is
        # a one-liner here when settings gain a ``brain_embedding_backend``
        # selector.
        _default_embedder = LiteLLMEmbedder()
    return _default_embedder


def reset_default_embedder() -> None:
    global _default_embedder
    _default_embedder = None
