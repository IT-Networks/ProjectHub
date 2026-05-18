"""Memory Bridge endpoints (P1) — AI-Assist ↔ ProjectHub.

Two endpoints implement the bridge contract
(see ``claudedocs/bridge_openapi_20260516.yaml``):

    POST /api/memory/v1/extract  — AI-Assist pushes session facts into the
                                   ProjectHub-Brain (creates KnowledgeItems
                                   with source_type="chat_extract").
    POST /api/memory/v1/query    — AI-Assist pulls a system-prompt-ready
                                   knowledge block (synapses + items).

Workspace → project_id resolution chain (Design §4.4):
    1. exact match on ``project_workspace_paths``
    2. longest-prefix match (workspace is a subpath of a registered path)
    3. legacy fallback: ``projects.repo_path`` exact match
    4. None → HTTP 422 with ``known_workspaces`` list

Idempotency: ``source_ref = sha256(workspace|fact.text)``. Repeat calls dedup
silently (existing ``extract_from_message`` pattern in ``routers/knowledge.py``).

The ``/query`` endpoint reuses the chat-context logic from
``routers/chat.py`` — same fallback (validated synapses first, raw items
otherwise) and returns both raw results AND a ready-to-prepend markdown block
that AI-Assist can inject verbatim into the system prompt.
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import and_, or_, select, text as sql_text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.communication import LinkedMessage
from models.knowledge import KnowledgeItem
from models.project import Project
from models.synapse import Synapse
from models.workspace import ProjectWorkspacePath, canonicalize_workspace_path

router = APIRouter(prefix="/api/memory/v1", tags=["memory-bridge"])
logger = logging.getLogger("projecthub.memory")


# ---- helpers --------------------------------------------------------------


def _gen_id() -> str:
    return secrets.token_hex(8)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dedup_hash(workspace: str, fact_text: str) -> str:
    basis = f"{canonicalize_workspace_path(workspace)}|{fact_text.strip()}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


async def _fts_insert(db: AsyncSession, item: KnowledgeItem) -> None:
    """Index a newly-created KnowledgeItem in ``knowledge_items_fts``.

    Mirrors the SQL shape of ``routers/knowledge._fts_insert`` but does NOT
    commit — the surrounding /extract loop already does one bulk commit at
    the end. Without this call, items written by /extract are invisible to
    any FTS5-MATCH-based search (including ``services/retrieval/hybrid``).

    Idempotent via INSERT OR REPLACE on rowid — re-running the call on the
    same item replaces the FTS row in place.

    Failures are logged + swallowed: a misbehaving FTS5 index must NEVER
    prevent the underlying KnowledgeItem from being created. Recovery is
    a one-shot ``services/retrieval/contextual.backfill_project`` later.
    """
    try:
        result = await db.execute(
            sql_text("SELECT rowid FROM knowledge_items WHERE id = :id"),
            {"id": item.id},
        )
        row = result.fetchone()
        if not row:
            return
        tags_text = " ".join(json.loads(item.tags)) if item.tags else ""
        await db.execute(
            sql_text(
                "INSERT OR REPLACE INTO knowledge_items_fts"
                "(rowid, title, content_plain, tags) "
                "VALUES (:rowid, :title, :content_plain, :tags)"
            ),
            {
                "rowid": row[0],
                "title": item.title,
                "content_plain": item.content_plain,
                "tags": tags_text,
            },
        )
    except Exception as e:  # noqa: BLE001 — must never sink the create call
        logger.warning("FTS insert failed for item %s: %s", item.id, e)


async def resolve_project_id_from_workspace(
    db: AsyncSession, workspace: str
) -> str | None:
    """Resolve a filesystem path to a project_id via the documented fallback chain.

    Returns ``None`` when no mapping exists — caller should respond with 422
    plus the list of known workspaces (so the user can fix configuration).
    """
    if not workspace:
        return None
    canon = canonicalize_workspace_path(workspace)

    # 1. exact mapping
    exact = await db.execute(
        select(ProjectWorkspacePath.project_id)
        .where(ProjectWorkspacePath.workspace_path == canon)
    )
    row = exact.scalar_one_or_none()
    if row:
        return row

    # 2. longest-prefix match (workspace is a subpath of a registered path)
    prefix_rows = await db.execute(select(ProjectWorkspacePath))
    candidates = [
        (r.project_id, r.workspace_path)
        for r in prefix_rows.scalars().all()
        if canon.startswith(r.workspace_path + "/") or canon == r.workspace_path
    ]
    if candidates:
        # longest match wins — deterministic when multiple parents are registered
        candidates.sort(key=lambda x: len(x[1]), reverse=True)
        return candidates[0][0]

    # 3. legacy fallback — ``project.docs_path`` exact match.
    # NB: the Project model exposes ``docs_path`` (not ``repo_path``); we use
    # ``getattr`` so a future rename to ``repo_path`` keeps working without an
    # update here. Canonicalise both sides so backslash vs forward-slash on
    # Windows doesn't sink the lookup.
    legacy = await db.execute(select(Project))
    for p in legacy.scalars().all():
        legacy_path = getattr(p, "docs_path", None) or getattr(p, "repo_path", None)
        if legacy_path and canonicalize_workspace_path(legacy_path) == canon:
            return p.id

    return None


async def _known_workspaces(db: AsyncSession) -> list[str]:
    """All registered workspace paths (for 422 error payloads)."""
    res = await db.execute(select(ProjectWorkspacePath.workspace_path))
    return sorted({row[0] for row in res.all() if row[0]})


# ---- request/response models ---------------------------------------------


class Fact(BaseModel):
    text: str = Field(..., max_length=4000)
    type: str = Field(..., description="preference | decision | constraint | technical | reference")
    tags: list[str] = Field(default_factory=list)
    valid_from: str | None = None
    valid_to: str | None = None
    confidence: float = Field(0.5, ge=0.0, le=1.0)


class ExtractRequest(BaseModel):
    session_id: str
    workspace: str
    facts: list[Fact] = Field(..., min_length=1, max_length=100)


class ExtractResponse(BaseModel):
    project_id: str
    created_item_ids: list[str]
    deduplicated: int
    linked_to_synapses: list[str] = Field(default_factory=list)


class QueryRequest(BaseModel):
    workspace: str
    query: str = Field(..., min_length=1, max_length=2000)
    top_k: int = Field(8, ge=1, le=50)
    mode: str = Field("hybrid", description="synapses | items | hybrid")
    as_of: str | None = None


class SynapseHit(BaseModel):
    id: str
    title: str
    summary_plain: str
    confidence: float
    source_count: int


class ItemHit(BaseModel):
    id: str
    title: str
    snippet: str
    rank: float


class QueryResponse(BaseModel):
    synapses: list[SynapseHit]
    items: list[ItemHit]
    format_hint: str


# ---- endpoint: extract ----------------------------------------------------


@router.post("/extract", response_model=ExtractResponse)
async def memory_extract(
    payload: ExtractRequest, db: AsyncSession = Depends(get_db)
) -> ExtractResponse:
    """Persist AI-Assist session facts as KnowledgeItems with source_type chat_extract."""
    project_id = await resolve_project_id_from_workspace(db, payload.workspace)
    if not project_id:
        raise HTTPException(
            status_code=422,
            detail={
                "detail": (
                    "Workspace path could not be resolved to a project. "
                    "Register the mapping or use a project.repo_path that matches."
                ),
                "known_workspaces": await _known_workspaces(db),
                "received_workspace": canonicalize_workspace_path(payload.workspace),
            },
        )

    created: list[str] = []
    deduplicated = 0

    for fact in payload.facts:
        text = fact.text.strip()
        if not text:
            continue
        source_ref = _dedup_hash(payload.workspace, text)

        # Idempotency — same hash exists already?
        existing = await db.execute(
            select(KnowledgeItem.id).where(
                and_(
                    KnowledgeItem.project_id == project_id,
                    KnowledgeItem.source_type == "chat_extract",
                    KnowledgeItem.source_ref == source_ref,
                )
            )
        )
        if existing.scalar_one_or_none():
            deduplicated += 1
            continue

        # Build KnowledgeItem. content_plain duplicates text — FTS5 has the
        # ``content_plain`` column indexed; ``content`` keeps the HTML for the
        # UI rich-text panel.
        item = KnowledgeItem(
            id=_gen_id(),
            project_id=project_id,
            title=text[:80] + ("..." if len(text) > 80 else ""),
            content=f"<p>{html.escape(text)}</p>",
            content_plain=text[:5000],
            category="reference",
            source_type="chat_extract",
            source_ref=source_ref,
            tags=json.dumps(fact.tags[:8]),
            confidence=_map_confidence(fact.confidence),
            extra_data=json.dumps(
                {
                    "session_id": payload.session_id,
                    "fact_type": fact.type,
                    "valid_from": fact.valid_from,
                    "valid_to": fact.valid_to,
                    "numeric_confidence": fact.confidence,
                    "source": "memory-bridge",
                }
            ),
        )
        try:
            db.add(item)
            await db.flush()  # surface IntegrityError per-row, not per-batch
            created.append(item.id)
        except IntegrityError:
            await db.rollback()
            deduplicated += 1
            continue

        # Index in FTS5 so /search and hybrid retrieval can find this item.
        # Without this the item exists in knowledge_items but is invisible
        # to MATCH-queries.
        await _fts_insert(db, item)

        # LinkedMessage row mirrors the existing ``extract/message`` flow so
        # the Inbox-UI's "Wissen-✓"-badge surfaces these facts too.
        try:
            link = LinkedMessage(
                id=_gen_id(),
                link_target="knowledge",
                target_id=item.id,
                source="chat_extract",
                source_ref=source_ref,
                subject=f"Fact ({fact.type})",
                sender=payload.session_id[:200],
                snippet=text[:300],
            )
            db.add(link)
            await db.flush()
        except IntegrityError:
            await db.rollback()
            # The KnowledgeItem itself is the authoritative row — a duplicate
            # LinkedMessage shouldn't kill the whole call.

    await db.commit()
    return ExtractResponse(
        project_id=project_id,
        created_item_ids=created,
        deduplicated=deduplicated,
        linked_to_synapses=[],  # populated in P9 (incremental synapse update)
    )


def _map_confidence(value: float) -> str:
    """0–1 numeric → KnowledgeItem.confidence enum band."""
    if value >= 0.8:
        return "high"
    if value >= 0.5:
        return "medium"
    return "low"


# ---- endpoint: query ------------------------------------------------------


@router.post("/query", response_model=QueryResponse)
async def memory_query(
    payload: QueryRequest, db: AsyncSession = Depends(get_db)
) -> QueryResponse:
    """Compact knowledge block ready for AI-Assist system-prompt injection."""
    project_id = await resolve_project_id_from_workspace(db, payload.workspace)
    if not project_id:
        raise HTTPException(status_code=404, detail="workspace not registered")

    synapses_hits: list[SynapseHit] = []
    items_hits: list[ItemHit] = []

    # P1 retrieval is intentionally simple: ranked synapse list + FTS items.
    # P2 adds hybrid (embeddings + RRF); P3 adds the LLM-rerank stage.

    if payload.mode in ("synapses", "hybrid"):
        syn_q = (
            select(Synapse)
            .where(
                Synapse.project_id == project_id,
                Synapse.verdict.in_(["persist", "persist_flagged"]),
            )
            .order_by(Synapse.confidence.desc())
            .limit(payload.top_k)
        )
        for s in (await db.execute(syn_q)).scalars().all():
            source_count = len(json.loads(s.source_item_ids or "[]"))
            synapses_hits.append(
                SynapseHit(
                    id=s.id,
                    title=s.title,
                    summary_plain=(s.summary_plain or "")[:600],
                    confidence=s.confidence or 0.0,
                    source_count=source_count,
                )
            )

    if payload.mode in ("items", "hybrid") and (
        # only fall back to flat items when no synapse hits were found OR mode == items
        payload.mode == "items" or not synapses_hits
    ):
        items_q = (
            select(KnowledgeItem)
            .where(
                KnowledgeItem.project_id == project_id,
                or_(
                    KnowledgeItem.title.ilike(f"%{payload.query}%"),
                    KnowledgeItem.content_plain.ilike(f"%{payload.query}%"),
                ),
            )
            .order_by(KnowledgeItem.is_pinned.desc(), KnowledgeItem.updated_at.desc())
            .limit(payload.top_k)
        )
        rank = 1.0
        for it in (await db.execute(items_q)).scalars().all():
            items_hits.append(
                ItemHit(
                    id=it.id,
                    title=it.title,
                    snippet=(it.content_plain or "")[:300],
                    rank=rank,
                )
            )
            rank -= 1.0 / max(1, payload.top_k)

    format_hint = _build_format_hint(synapses_hits, items_hits)
    return QueryResponse(
        synapses=synapses_hits,
        items=items_hits,
        format_hint=format_hint,
    )


def _build_format_hint(synapses: list[SynapseHit], items: list[ItemHit]) -> str:
    """Render a ready-to-prepend markdown block for an LLM system prompt."""
    if not synapses and not items:
        return ""
    lines: list[str] = ["## Projekt-Wissen (aus ProjectHub-Brain)"]
    for s in synapses:
        lines.append(
            f"- **{s.title}** (Konfidenz {s.confidence:.2f}, "
            f"{s.source_count} Quellen): {s.summary_plain[:240]}"
        )
    if items and not synapses:
        # only surface raw items if no synapse summary covers the query —
        # keeps the prompt block lean
        for it in items[:5]:
            lines.append(f"- *{it.title}*: {it.snippet[:200]}")
    return "\n".join(lines) + "\n"


# ---- workspace mapping CRUD (small admin surface) ------------------------
#
# Without these the user can't *register* a workspace path, so /extract would
# always 422. Two minimal endpoints — list + put — match the project_id pair
# in the URL so the operation is unambiguous.


class WorkspaceMapping(BaseModel):
    project_id: str
    workspace_path: str
    created_at: str


@router.get("/workspaces", response_model=list[WorkspaceMapping])
async def list_workspace_mappings(
    db: AsyncSession = Depends(get_db),
) -> list[WorkspaceMapping]:
    res = await db.execute(select(ProjectWorkspacePath))
    return [
        WorkspaceMapping(
            project_id=r.project_id,
            workspace_path=r.workspace_path,
            created_at=r.created_at,
        )
        for r in res.scalars().all()
    ]


class WorkspaceMappingCreate(BaseModel):
    project_id: str
    workspace_path: str


@router.put("/workspaces", response_model=WorkspaceMapping, status_code=200)
async def upsert_workspace_mapping(
    payload: WorkspaceMappingCreate, db: AsyncSession = Depends(get_db)
) -> WorkspaceMapping:
    # Ensure project exists — otherwise FK explodes with a less helpful error.
    proj = await db.execute(select(Project).where(Project.id == payload.project_id))
    if not proj.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="project not found")

    canon = canonicalize_workspace_path(payload.workspace_path)
    existing = await db.execute(
        select(ProjectWorkspacePath).where(
            and_(
                ProjectWorkspacePath.project_id == payload.project_id,
                ProjectWorkspacePath.workspace_path == canon,
            )
        )
    )
    row = existing.scalar_one_or_none()
    if row is None:
        row = ProjectWorkspacePath(
            project_id=payload.project_id,
            workspace_path=canon,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return WorkspaceMapping(
        project_id=row.project_id,
        workspace_path=row.workspace_path,
        created_at=row.created_at,
    )
