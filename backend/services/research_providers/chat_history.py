"""chat_history — search previous single-shot chat-research results.

Uses the existing ``research_results`` table (the legacy single-shot
chat-research log; see ``ResearchResult`` in ``models/research.py``).
That table holds (query, result, model, agent_team, session_id) per
historical chat-research call — perfect "have we asked this before?"
hint for the planner.

LIKE-based substring scan, same tokeniser as ``project_notes``. The
``query`` field is what the user typed; ``result`` is the answer. We
match against both so a sub-question can find both a similar previous
question and a previous answer that mentions the topic.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import AsyncIterator

from sqlalchemy import and_, func as sa_func, or_, select

from database import async_session
from models.research import ResearchResult

from services.research_providers.base import (
    Finding,
    ProviderHealth,
    SearchProgress,
    _now_iso,
    make_snippet,
)

logger = logging.getLogger("projecthub.research.chat_history")

_STOPWORDS = {
    # DE
    "der", "die", "das", "und", "oder", "in", "mit", "für", "auf", "von",
    "ist", "im", "zu", "den", "ein", "eine", "wie", "was", "wer", "wann",
    # EN
    "the", "and", "or", "in", "with", "for", "on", "of", "is", "to", "a",
    "an", "how", "what", "who", "when", "why",
}


def _tokenize_query(query: str) -> list[str]:
    """Same shape as project_notes._tokenize_query, kept inline so the
    two providers don't depend on each other's internals."""
    raw = re.findall(r"[\wäöüÄÖÜß]+", query.lower())
    cleaned = [t for t in raw if len(t) >= 2 and t not in _STOPWORDS]
    return cleaned or raw


class ChatHistoryProvider:
    """Searches past chat-research entries from the same project.

    Returns at most ``max_results`` rows ordered newest-first. The
    ``query`` field becomes the Finding title (so the planner sees
    "we asked X before"); the ``result`` becomes the snippet.
    """

    key = "chat_history"
    description = (
        "Frühere Chat-Recherchen im selben Projekt. Verhindert Doppel-Arbeit "
        "wenn die Frage (oder eine ähnliche) schon einmal beantwortet wurde."
    )
    typical_latency = "fast"
    side_effect = "read"
    default_enabled = True

    async def health(self) -> ProviderHealth:
        try:
            async with async_session() as db:
                await db.execute(select(ResearchResult.id).limit(1))
            return ProviderHealth(ok=True, detail="connected", last_checked_at=_now_iso())
        except Exception as e:  # noqa: BLE001
            logger.warning("chat_history health check failed: %s", e)
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
        max_results = int(provider_settings.get("max_results", 6))

        if cancel.is_set():
            yield SearchProgress(kind="done", status_text="cancelled")
            return

        terms = _tokenize_query(query)
        if not terms:
            yield SearchProgress(kind="done", status_text="empty_query")
            return

        yield SearchProgress(
            kind="status",
            status_text=f"Suche in vorherigen Chat-Recherchen ({len(terms)} Begriffe)",
        )

        try:
            async with async_session() as db:
                conds = []
                for term in terms:
                    needle = f"%{term}%"
                    conds.append(
                        or_(
                            sa_func.lower(ResearchResult.query).like(needle),
                            sa_func.lower(ResearchResult.result).like(needle),
                        )
                    )
                stmt = (
                    select(ResearchResult)
                    .where(ResearchResult.project_id == project_id)
                    .where(and_(*conds))
                    .order_by(ResearchResult.created_at.desc())
                    .limit(max_results)
                )
                rows = (await db.execute(stmt)).scalars().all()
        except Exception as e:  # noqa: BLE001
            logger.warning("chat_history.stream failed: %s", e)
            yield SearchProgress(kind="error", error=f"chat_history: {e!s}"[:200])
            yield SearchProgress(kind="done", status_text="error")
            return

        for row in rows:
            if cancel.is_set():
                yield SearchProgress(kind="done", status_text="cancelled")
                return
            yield SearchProgress(
                kind="finding",
                finding=Finding(
                    provider_key=self.key,
                    source_ref=f"chat:{row.id}",
                    title=(row.query or "(leere Frage)")[:300],
                    snippet=make_snippet(row.result or ""),
                    full_content=row.result or None,
                    url=None,
                    timestamp=row.created_at,
                    author=None,
                    score=None,
                    raw_metadata={
                        "model_used": row.model_used,
                        "agent_team": row.agent_team,
                        "session_id": row.session_id,
                    },
                ),
            )

        yield SearchProgress(kind="done", status_text="ok")
