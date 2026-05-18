"""kb_fts — local ProjectHub-Knowledge-Base provider.

Brain-consumer: when ``brain_embedding_enabled`` is on we route through
``services/retrieval/hybrid.py`` (RRF of FTS5 + cosine over the project's
embedded items). When the flag is off, hybrid_search degrades silently
to FTS5-only, so the provider doesn't need a second code path — same
function, just the embedder argument is conditional.

Latency is dominated by the embedder round-trip when embeddings are on;
the FTS5-only path is sub-100 ms for any project that fits the no-FAISS
budget. Either way it's the cheapest provider in the catalogue and the
one the planner always reaches for first.
"""
from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

from sqlalchemy import select

from config import settings
from database import async_session
from models.knowledge import KnowledgeItem

from services.research_providers.base import (
    Finding,
    ProviderHealth,
    SearchProgress,
    _now_iso,
    make_snippet,
)

logger = logging.getLogger("projecthub.research.kb_fts")


class KBFtsProvider:
    """Searches the project's KnowledgeItems.

    Stream contract: one ``status`` event ("searching"), zero or more
    ``finding`` events, exactly one terminal ``done`` event. Errors map
    to a ``done`` with a non-empty ``status_text`` rather than ``error``
    so a local-DB hiccup doesn't fail the whole run.
    """

    key = "kb_fts"
    description = (
        "Lokale Projekt-Knowledge-Base. Schnellste Quelle; ideal um "
        "vorhandenes Projektwissen wieder zu finden bevor extern gesucht wird."
    )
    typical_latency = "fast"
    side_effect = "read"
    default_enabled = True

    async def health(self) -> ProviderHealth:
        """Local provider — always reachable. We still ping the DB so a
        broken connection surfaces here instead of mid-stream."""
        try:
            async with async_session() as db:
                # cheap sanity probe — count is fine, table exists since v1.0.
                await db.execute(select(KnowledgeItem.id).limit(1))
            return ProviderHealth(ok=True, detail="connected", last_checked_at=_now_iso())
        except Exception as e:  # noqa: BLE001 — health never raises
            logger.warning("kb_fts health check failed: %s", e)
            return ProviderHealth(
                ok=False, detail=f"db_error: {e!s}"[:120], last_checked_at=_now_iso()
            )

    async def stream(
        self,
        query: str,
        provider_settings: dict,
        cancel: asyncio.Event,
        *,
        project_id: str,
    ) -> AsyncIterator[SearchProgress]:
        """Yield findings for ``query`` against this project's KB.

        Note: ``project_id`` is a keyword-only kw the orchestrator
        injects — it is not in the abstract Protocol because not every
        provider needs it (e.g. ``web`` works against any project), but
        local providers always do.
        """
        max_results = int(provider_settings.get("max_results", 10))
        mode = provider_settings.get("mode")  # None → auto-pick below

        if cancel.is_set():
            yield SearchProgress(kind="done", status_text="cancelled")
            return

        yield SearchProgress(kind="status", status_text=f"Suche im Projekt-KB ({query[:60]})")

        try:
            embedder = self._embedder()
            # Default: hybrid when an embedder is wired, fts otherwise.
            search_mode = mode or ("hybrid" if embedder is not None else "fts")

            async with async_session() as db:
                # Local import keeps this module testable even if the brain
                # retrieval package isn't installed (defensive — it always is
                # in v1.4.0+, but a missing module would crash imports otherwise).
                from services.retrieval.hybrid import hybrid_search

                hits = await hybrid_search(
                    db,
                    project_id,
                    query,
                    top_k=max_results,
                    pool_size=min(30, max_results * 3),
                    embedder=embedder,
                    mode=search_mode,
                )
        except Exception as e:  # noqa: BLE001 — soft-fail per provider
            logger.warning("kb_fts.stream failed: %s", e)
            yield SearchProgress(kind="error", error=f"kb_fts: {e!s}"[:200])
            yield SearchProgress(kind="done", status_text="error")
            return

        for hit in hits:
            if cancel.is_set():
                yield SearchProgress(kind="done", status_text="cancelled")
                return
            item: KnowledgeItem = hit.item
            yield SearchProgress(
                kind="finding",
                finding=Finding(
                    provider_key=self.key,
                    source_ref=f"kb:{item.id}",
                    title=item.title or "(ohne Titel)",
                    snippet=make_snippet(item.content_plain or ""),
                    full_content=item.content_plain or None,
                    url=None,
                    timestamp=item.updated_at,
                    author=None,
                    score=hit.score,
                    raw_metadata={
                        "category": item.category,
                        "source_type": item.source_type,
                        "confidence": item.confidence,
                        "rerank_sources": sorted(hit.source),  # {"fts","cosine"}
                    },
                ),
            )

        yield SearchProgress(kind="done", status_text="ok")

    # ── internals ──────────────────────────────────────────────────────

    def _embedder(self):
        """Construct (or skip) the embedder based on the brain flag.

        Constructing the embedder costs essentially nothing (it's just
        config holders); the network round-trip only happens on the
        first ``embed`` call. Returning ``None`` triggers the FTS-only
        path in hybrid_search.
        """
        if not getattr(settings, "brain_embedding_enabled", False):
            return None
        from services.embedding.litellm_router import LiteLLMEmbedder

        return LiteLLMEmbedder()
