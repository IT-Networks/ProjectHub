"""project_documents — extracted content from docx/pdf scans.

Document content is stored as ``KnowledgeItem`` rows with
``source_type="document"`` after the project-document scanner pipeline
runs (``services/document_scanner.py``). This provider just searches
those rows specifically — gives the planner a clear "look in the
attached documents" route without polluting the kb_fts hit list.

When ``brain_embedding_enabled`` is on, we use the same
``hybrid_search`` orchestrator as kb_fts but post-filter the hits to
the document subset. When off, we hit FTS5 directly with the same
post-filter. Either way the heavy lifting lives in Brain — this
provider is the source-type filter, not a parallel implementation.
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

logger = logging.getLogger("projecthub.research.project_documents")

# Source types that count as "document" content. ``confluence`` and
# ``research`` are KB items too but conceptually separate sources, so
# the planner gets them via their own providers.
_DOCUMENT_SOURCE_TYPES = {"document"}


class ProjectDocumentsProvider:
    """Searches KnowledgeItems extracted from project documents.

    Lifecycle: scanner extracts → KnowledgeItem(source_type='document').
    This provider returns the subset matching the query.
    """

    key = "project_documents"
    description = (
        "Inhalt der angehängten Projekt-Dokumente (docx/pdf). Sinnvoll für "
        "Spezifikationen, Konzepte oder Architektur-Doku die als Datei im "
        "Projekt liegen."
    )
    typical_latency = "fast"
    side_effect = "read"
    default_enabled = True

    async def health(self) -> ProviderHealth:
        try:
            async with async_session() as db:
                await db.execute(
                    select(KnowledgeItem.id)
                    .where(KnowledgeItem.source_type.in_(_DOCUMENT_SOURCE_TYPES))
                    .limit(1)
                )
            return ProviderHealth(ok=True, detail="connected", last_checked_at=_now_iso())
        except Exception as e:  # noqa: BLE001
            logger.warning("project_documents health check failed: %s", e)
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
        max_results = int(provider_settings.get("max_results", 8))
        # Pool larger so the source-type filter still leaves us enough hits.
        pool_size = max(20, max_results * 4)

        if cancel.is_set():
            yield SearchProgress(kind="done", status_text="cancelled")
            return

        yield SearchProgress(kind="status", status_text="Suche in Projekt-Dokumenten")

        try:
            embedder = self._embedder()
            search_mode = "hybrid" if embedder is not None else "fts"

            async with async_session() as db:
                from services.retrieval.hybrid import hybrid_search

                hits = await hybrid_search(
                    db,
                    project_id,
                    query,
                    top_k=pool_size,  # over-fetch, we filter below
                    pool_size=pool_size,
                    embedder=embedder,
                    mode=search_mode,
                )

                # Post-filter to document sources, preserving fused order.
                doc_hits = [h for h in hits if h.item.source_type in _DOCUMENT_SOURCE_TYPES]
                doc_hits = doc_hits[:max_results]
        except Exception as e:  # noqa: BLE001
            logger.warning("project_documents.stream failed: %s", e)
            yield SearchProgress(kind="error", error=f"project_documents: {e!s}"[:200])
            yield SearchProgress(kind="done", status_text="error")
            return

        for hit in doc_hits:
            if cancel.is_set():
                yield SearchProgress(kind="done", status_text="cancelled")
                return
            item: KnowledgeItem = hit.item
            yield SearchProgress(
                kind="finding",
                finding=Finding(
                    provider_key=self.key,
                    source_ref=f"document:{item.id}",
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
                        "rerank_sources": sorted(hit.source),
                    },
                ),
            )

        yield SearchProgress(kind="done", status_text="ok")

    def _embedder(self):
        if not getattr(settings, "brain_embedding_enabled", False):
            return None
        from services.embedding.litellm_router import LiteLLMEmbedder

        return LiteLLMEmbedder()
