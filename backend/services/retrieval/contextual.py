"""Contextual-Retrieval snippet generator (T2.4).

Anthropic-style "Contextual Retrieval" (Sep 2024) — every chunk gets a
one-sentence context line prepended *before* it's indexed. The retrieval
side (FTS5 here, and hybrid-cosine in ``hybrid.py``) sees the augmented
text and lands on more relevant items, especially for items whose body
is too short or too noisy on its own.

The contract this module exposes is intentionally narrow:

    async generate_context(item, project, *, ai_assist=None) -> str
    async backfill_project(db, project_id, *, ai_assist=None,
                            sleep_between_seconds=1.0) -> dict

Both ALWAYS succeed at the call-site level — LLM failures return ``""``
so callers can skip the augmentation rather than blocking the whole
create/update flow. The generator never raises into engine code.

The FTS5 schema-level extension that adds ``context_summary`` as a
fourth indexed column lives in T2.5 (the create/update hook). M1
migration already added the *column* on knowledge_items; reindexing the
FTS5 virtual table to pick it up is a separate concern handled by the
hook.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.knowledge import KnowledgeItem
from models.project import Project

logger = logging.getLogger("projecthub.contextual")


# ── Prompt ──────────────────────────────────────────────────────────────

_CONTEXT_PROMPT = """Du formulierst EINEN prägnanten Kontext-Satz, der einem Such-Algorithmus hilft, das folgende Wissens-Item korrekt einzuordnen.

Projekt: {project_title}
Titel: {item_title}
Kategorie: {item_category}
Bestehende Tags: {item_tags}

---
{item_content}
---

Anforderungen:
- GENAU EIN SATZ, max 200 Zeichen.
- Nennt das übergeordnete Thema UND den Bezug zum Projekt.
- WIEDERHOLT NICHT den Titel.
- Keine Anführungszeichen, kein Markdown, keine Erklärung.

Antwort (nur der eine Satz):"""


# Truncate the item body for the prompt — context snippets are about
# big-picture framing, not full content. Saves tokens on big items.
_MAX_ITEM_PROMPT_CHARS = 1500

# Hard cap on the returned snippet. The FTS5 column can hold more, but
# 200 is the user-facing-prompt cap and what the design doc specifies.
_MAX_SNIPPET_CHARS = 200


# ── AI-Assist call protocol ─────────────────────────────────────────────

class AIAssistProtocol(Protocol):
    """The slice of ``services.ai_assist_client.ai_assist`` we depend on.

    Tests inject a stub matching this; production passes the real client.
    """

    async def agent_call(
        self, *, session_id: str, message: str, model: str | None = None,
        auto_detect: bool = False, project_path: str | None = None,
    ) -> dict | None: ...


# ── Result types ────────────────────────────────────────────────────────

@dataclass
class BackfillStats:
    total: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0


# ── Helpers ─────────────────────────────────────────────────────────────

def _clean_snippet(raw: str) -> str:
    """Normalise an LLM response into a single, capped, line-free sentence."""
    if not raw:
        return ""
    s = raw.strip()
    # Strip ```fences``` and leading "Antwort:" prefixes the model adds.
    if s.startswith("```"):
        s = s.split("```")[1] if len(s.split("```")) > 1 else s
        s = s.strip()
    for prefix in ("Antwort:", "antwort:", "Context:", "Kontext:"):
        if s.lower().startswith(prefix.lower()):
            s = s[len(prefix):].lstrip(" :-")
            break
    # Collapse to a single line — multi-line responses are not what we asked for.
    s = " ".join(s.split())
    if len(s) > _MAX_SNIPPET_CHARS:
        s = s[: _MAX_SNIPPET_CHARS - 1].rstrip() + "…"
    # Strip any wrapping quotation marks the model might add.
    if len(s) >= 2 and s[0] in {'"', "'", "„", "«"} and s[-1] in {'"', "'", "“", "»"}:
        s = s[1:-1].strip()
    return s


def _project_title(project: Project | None) -> str:
    if project is None:
        return "(unbekanntes Projekt)"
    return getattr(project, "name", None) or "(unbenanntes Projekt)"


# ── Public API ──────────────────────────────────────────────────────────

async def generate_context(
    item: KnowledgeItem,
    project: Project | None,
    *,
    ai_assist: AIAssistProtocol | None = None,
    model: str | None = None,
) -> str:
    """Generate one context sentence (≤200 chars) for a KnowledgeItem.

    Returns ``""`` when the LLM is unreachable, returns garbage, or the
    response can't be coerced into a single short sentence. Callers
    treat the empty string as "skip the augmentation for this item".
    """
    if ai_assist is None:
        # Late import keeps this module testable without standing up the
        # full ai_assist HTTP client.
        from services.ai_assist_client import ai_assist as _client

        ai_assist = _client  # type: ignore[assignment]

    body = (item.content_plain or "").strip()[:_MAX_ITEM_PROMPT_CHARS]
    if not body and not (item.title or "").strip():
        # Nothing for the model to ground on.
        return ""

    import json as _json

    tags_str = ""
    try:
        tags = _json.loads(item.tags) if item.tags else []
    except (ValueError, TypeError):
        tags = []
    if isinstance(tags, list) and tags:
        tags_str = ", ".join(str(t) for t in tags[:5])
    else:
        tags_str = "(keine)"

    prompt = _CONTEXT_PROMPT.format(
        project_title=_project_title(project),
        item_title=(item.title or "(unbenannt)")[:120],
        item_category=item.category or "reference",
        item_tags=tags_str,
        item_content=body or "(leerer Body)",
    )

    import secrets

    session_id = f"projecthub-contextual-{secrets.token_hex(6)}"
    try:
        result = await ai_assist.agent_call(
            session_id=session_id,
            message=prompt,
            model=model,
            auto_detect=False,
        )
    except Exception as e:  # noqa: BLE001 — never bubble into create/update
        logger.warning("contextual snippet LLM call failed: %s", e)
        return ""

    if not result or not isinstance(result, dict):
        return ""
    raw = result.get("response") or ""
    snippet = _clean_snippet(raw)
    if not snippet:
        logger.debug("contextual snippet was empty after cleaning; raw=%r", raw[:120])
    return snippet


async def backfill_project(
    db: AsyncSession,
    project_id: str,
    *,
    ai_assist: AIAssistProtocol | None = None,
    model: str | None = None,
    sleep_between_seconds: float = 1.0,
    max_items: int = 0,
) -> BackfillStats:
    """Generate ``context_summary`` for every item in a project that lacks one.

    Long-running by design — gated by ``sleep_between_seconds`` (default 1s)
    to avoid hammering the LLM proxy on a large project. ``max_items=0``
    means "all"; non-zero caps the run for tests / rate-limit windows.

    Idempotent: items already carrying a non-empty ``context_summary`` are
    skipped. Caller commits the session.
    """
    stats = BackfillStats()

    proj_res = await db.execute(select(Project).where(Project.id == project_id))
    project = proj_res.scalar_one_or_none()
    if project is None:
        return stats

    q = select(KnowledgeItem).where(KnowledgeItem.project_id == project_id)
    if max_items:
        q = q.limit(max_items * 4)  # over-fetch; we filter skipped items below
    rows = (await db.execute(q)).scalars().all()
    stats.total = len(rows)

    processed = 0
    for item in rows:
        if (item.context_summary or "").strip():
            stats.skipped += 1
            continue
        snippet = await generate_context(item, project, ai_assist=ai_assist, model=model)
        if not snippet:
            stats.failed += 1
            continue
        item.context_summary = snippet
        item.updated_at = _now()
        stats.updated += 1
        processed += 1
        if max_items and processed >= max_items:
            break
        if sleep_between_seconds > 0:
            await asyncio.sleep(sleep_between_seconds)

    return stats


# Avoid pulling the routers module just for ``_now`` — keep this local.

def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
