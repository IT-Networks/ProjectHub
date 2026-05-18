"""Item enrichment — generate context_summary + embedding (T2.5).

A single async helper used by three call-sites:

    routers/knowledge.create_item   — new items get enriched before INSERT
    routers/knowledge.update_item   — re-enrich when title/content changed
    routers/knowledge._run_backfill — retroactive enrichment of an entire
                                      project (T2.6)

The function is **failure-tolerant by design**: each enrichment stage
(contextual snippet, embedding) is independently try/except'd so a bad
LLM proxy can never block a user-facing create. Items missing either
piece can be re-enriched later by the backfill endpoint.

Settings gating:

    brain_contextual_retrieval_enabled  → run contextual.generate_context
    brain_embedding_enabled             → run embedder.embed_one + pack

Both default OFF — without explicit opt-in this function is a no-op
beyond a couple of cheap settings lookups.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from models.knowledge import KnowledgeItem
from models.project import Project
from services.embedding.protocol import EmbeddingError
from services.retrieval import contextual
from services.retrieval.hybrid import pack_vector

logger = logging.getLogger("projecthub.enrichment")


# What we feed into the embedder. Title + context snippet + body-head
# gives the embedding both topical (title) and detail (body) anchors.
_EMBED_BODY_CHARS = 2000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_flags() -> tuple[bool, bool]:
    """Return ``(contextual_enabled, embedding_enabled)`` from settings.

    Defensive against test-isolation where ``config`` can't be imported.
    """
    try:
        from config import settings

        return (
            bool(getattr(settings, "brain_contextual_retrieval_enabled", False)),
            bool(getattr(settings, "brain_embedding_enabled", False)),
        )
    except Exception:  # pragma: no cover — defensive
        return (False, False)


def _build_embed_text(item: KnowledgeItem) -> str:
    """Concatenate title + context_summary + body-head for the embedder.

    Empty parts are filtered so the model isn't fed leading/trailing
    separator garbage. Returns ``""`` if there's nothing to embed.
    """
    parts = [
        (item.title or "").strip(),
        (item.context_summary or "").strip(),
        (item.content_plain or "").strip()[:_EMBED_BODY_CHARS],
    ]
    return "\n\n".join(p for p in parts if p)


async def enrich_item(
    item: KnowledgeItem,
    project: Project | None,
    *,
    contextual_enabled: bool | None = None,
    embedding_enabled: bool | None = None,
    ai_assist: Any | None = None,
    embedder: Any | None = None,
    model: str | None = None,
) -> dict:
    """Generate context_summary and/or embedding for an item, mutating in-place.

    Args:
        item: row to enrich (caller commits).
        project: parent project — used by ``generate_context`` for framing.
        contextual_enabled / embedding_enabled: explicit overrides for
            settings flags. ``None`` (default) reads ``config.settings``.
        ai_assist / embedder: dependency-injection seams for tests.

    Returns:
        ``{"context_set": bool, "embedding_set": bool, "errors": list[str]}``
        — caller can use this for SSE progress or test assertions.
    """
    if contextual_enabled is None or embedding_enabled is None:
        flag_ctx, flag_emb = _read_flags()
        if contextual_enabled is None:
            contextual_enabled = flag_ctx
        if embedding_enabled is None:
            embedding_enabled = flag_emb

    stats: dict = {"context_set": False, "embedding_set": False, "errors": []}

    # ── Tier 1: contextual snippet ────────────────────────────────────
    if contextual_enabled:
        try:
            snippet = await contextual.generate_context(
                item, project, ai_assist=ai_assist, model=model,
            )
            if snippet:
                item.context_summary = snippet
                stats["context_set"] = True
        except Exception as e:  # noqa: BLE001 — enrichment must never bubble
            logger.warning("enrich_item: contextual failed: %s", e)
            stats["errors"].append(f"context:{type(e).__name__}")

    # ── Tier 2: embedding ─────────────────────────────────────────────
    if embedding_enabled:
        if embedder is None:
            try:
                from services.embedding import get_default_embedder

                embedder = get_default_embedder()
            except Exception:  # pragma: no cover — defensive
                embedder = None
        if embedder is not None:
            text = _build_embed_text(item)
            if text:
                try:
                    vec = await embedder.embed_one(text)
                except EmbeddingError as e:
                    logger.warning("enrich_item: embed failed: %s", e)
                    stats["errors"].append(f"embedding:{type(e).__name__}")
                    vec = []
                except Exception as e:  # noqa: BLE001 — defensive
                    logger.warning("enrich_item: embed crashed: %s", e)
                    stats["errors"].append(f"embedding:{type(e).__name__}")
                    vec = []
                if vec:
                    item.embedding = pack_vector(vec)
                    item.embedding_model = (
                        getattr(embedder, "model_id", "") or "default"
                    )
                    item.embedded_at = _now()
                    stats["embedding_set"] = True

    return stats
