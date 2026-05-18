"""project_notes — search the project's Tiptap/HTML/Markdown notes.

The ``notes`` table is small per project (rarely >100 entries) and not
FTS5-indexed, so a SQL LIKE scan is fast enough and avoids reaching
into Brain (whose hybrid_search is scoped to ``knowledge_items``).

Match logic mirrors a basic search box: case-insensitive substring on
``title`` and ``content`` with term-wise ANDing. Pinned notes are
surfaced first within the result set — the user has flagged those as
important; the Auto-Mode planner deserves the same hint.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import AsyncIterator

from sqlalchemy import and_, or_, select

from database import async_session
from models.note import Note

from services.research_providers.base import (
    Finding,
    ProviderHealth,
    SearchProgress,
    _now_iso,
    make_snippet,
)

logger = logging.getLogger("projecthub.research.project_notes")

# Stopwords we never use as a search term — too noisy for substring LIKE.
_STOPWORDS = {
    # DE
    "der", "die", "das", "und", "oder", "in", "mit", "für", "auf", "von",
    "ist", "im", "zu", "den", "ein", "eine", "wie", "was", "wer", "wann",
    # EN
    "the", "and", "or", "in", "with", "for", "on", "of", "is", "to", "a",
    "an", "how", "what", "who", "when", "why",
}


def _tokenize_query(query: str) -> list[str]:
    """Split a free-text query into substring search terms.

    Drops punctuation, lowercases, removes stopwords and single-char
    tokens. With <2 surviving terms we fall back to whatever's left so
    a one-word query still searches.
    """
    raw = re.findall(r"[\wäöüÄÖÜß]+", query.lower())
    cleaned = [t for t in raw if len(t) >= 2 and t not in _STOPWORDS]
    return cleaned or raw  # never return empty when the user typed something


def _strip_html_lite(text: str) -> str:
    """Cheap HTML-tag removal for the snippet preview.

    Notes can be HTML/Tiptap; we don't import bs4 just for a preview.
    Whitespace is collapsed by make_snippet downstream.
    """
    if not text:
        return ""
    return re.sub(r"<[^>]+>", " ", text)


class ProjectNotesProvider:
    """LIKE-based substring search over the project's Notes."""

    key = "project_notes"
    description = (
        "Notizen des Projekts (Tiptap/HTML/Markdown). Gut für Festgehaltenes "
        "aus Meetings, Brainstorms oder offene To-Do-Gedanken."
    )
    typical_latency = "fast"
    side_effect = "read"
    default_enabled = True

    async def health(self) -> ProviderHealth:
        try:
            async with async_session() as db:
                await db.execute(select(Note.id).limit(1))
            return ProviderHealth(ok=True, detail="connected", last_checked_at=_now_iso())
        except Exception as e:  # noqa: BLE001
            logger.warning("project_notes health check failed: %s", e)
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

        if cancel.is_set():
            yield SearchProgress(kind="done", status_text="cancelled")
            return

        terms = _tokenize_query(query)
        if not terms:
            yield SearchProgress(kind="done", status_text="empty_query")
            return

        yield SearchProgress(
            kind="status", status_text=f"Suche in Projekt-Notizen ({len(terms)} Begriffe)"
        )

        try:
            async with async_session() as db:
                # Build: (title LIKE %t1% OR content LIKE %t1%) AND (... OR ...) ...
                # SQLite ICOLLATE doesn't apply to LIKE by default — Note.content
                # is mixed-case so we normalize via LOWER() on both sides.
                from sqlalchemy import func as sa_func

                conds = []
                for term in terms:
                    needle = f"%{term}%"
                    conds.append(
                        or_(
                            sa_func.lower(Note.title).like(needle),
                            sa_func.lower(Note.content).like(needle),
                        )
                    )
                stmt = (
                    select(Note)
                    .where(Note.project_id == project_id)
                    .where(and_(*conds))
                    # Pinned first, then most-recent.
                    .order_by(Note.is_pinned.desc(), Note.updated_at.desc())
                    .limit(max_results)
                )
                rows = (await db.execute(stmt)).scalars().all()
        except Exception as e:  # noqa: BLE001
            logger.warning("project_notes.stream failed: %s", e)
            yield SearchProgress(kind="error", error=f"project_notes: {e!s}"[:200])
            yield SearchProgress(kind="done", status_text="error")
            return

        for note in rows:
            if cancel.is_set():
                yield SearchProgress(kind="done", status_text="cancelled")
                return
            plain = _strip_html_lite(note.content or "")
            yield SearchProgress(
                kind="finding",
                finding=Finding(
                    provider_key=self.key,
                    source_ref=f"notes:{note.id}",
                    title=note.title or "(ohne Titel)",
                    snippet=make_snippet(plain),
                    full_content=plain or None,
                    url=None,
                    timestamp=note.updated_at,
                    author=None,
                    score=None,  # LIKE doesn't carry a score; ordering is pinned/recency
                    raw_metadata={
                        "is_pinned": bool(note.is_pinned),
                        "content_format": note.content_format,
                    },
                ),
            )

        yield SearchProgress(kind="done", status_text="ok")
