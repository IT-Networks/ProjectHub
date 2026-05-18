import asyncio
import hashlib
import html
import json
import re
import secrets
import logging
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, func, text, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.knowledge import KnowledgeItem, KnowledgeEdge, ProjectDocument
from models.note import Note
from models.research import ResearchResult
from models.project import Project
from models.communication import LinkedMessage
from services.ai_assist_client import ai_assist

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])
logger = logging.getLogger("projecthub.knowledge")


def _gen_id() -> str:
    return secrets.token_hex(8)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _strip_html(html_text: str) -> str:
    """Strip HTML tags for plain text / FTS indexing."""
    text_content = re.sub(r"<[^>]+>", " ", html_text)
    text_content = re.sub(r"\s+", " ", text_content).strip()
    return text_content


def _markdown_to_html(md_text: str) -> str:
    """Convert synthesized Markdown to HTML for the rich-text ``content`` field.

    ``NodeDetailPanel`` renders ``content`` via ``dangerouslySetInnerHTML``,
    so it must be HTML — raw Markdown would show literal ``##``/``-`` markers.
    Falls back to an escaped ``<pre>`` block if the markdown lib is missing,
    so a rendering hiccup never sinks the whole research.
    """
    if not md_text:
        return ""
    try:
        import markdown as _md
        return _md.markdown(md_text, extensions=["tables", "fenced_code", "sane_lists"])
    except Exception:
        return f"<pre>{html.escape(md_text)}</pre>"


VALID_CATEGORIES = {
    "architecture", "business_logic", "infrastructure",
    "process", "decision", "reference", "custom",
}
VALID_SOURCE_TYPES = {
    "manual", "research", "note_import", "email_extract",
    "chat_extract", "confluence", "document",
}
VALID_EDGE_TYPES = {"related", "references", "based_on", "extends"}
VALID_CONFIDENCE = {"high", "medium", "low"}


# --- Pydantic Schemas ---

class KnowledgeItemCreate(BaseModel):
    title: str
    content: str = ""
    category: str = "reference"
    tags: list[str] = []
    source_type: str = "manual"
    source_ref: str | None = None
    confidence: str = "medium"
    metadata: dict = {}


class KnowledgeItemUpdate(BaseModel):
    title: str | None = None
    content: str | None = None
    category: str | None = None
    tags: list[str] | None = None
    confidence: str | None = None
    metadata: dict | None = None
    is_pinned: bool | None = None


class EdgeCreate(BaseModel):
    source_item_id: str
    target_item_id: str
    edge_type: str = "related"
    label: str | None = None


class KnowledgeItemResponse(BaseModel):
    id: str
    project_id: str
    title: str
    content: str
    content_plain: str
    category: str
    source_type: str
    source_ref: str | None
    tags: list[str]
    confidence: str
    metadata: dict
    is_pinned: bool
    source_note_id: str | None
    sync_status: str
    last_synced_at: str | None
    created_at: str
    updated_at: str


class KnowledgeEdgeResponse(BaseModel):
    id: str
    source_item_id: str
    target_item_id: str
    edge_type: str
    label: str | None
    created_at: str


class KnowledgeItemDetailResponse(KnowledgeItemResponse):
    edges: list[KnowledgeEdgeResponse]
    neighbors: list[dict]


class GraphNodeResponse(BaseModel):
    id: str
    title: str
    category: str
    tags: list[str]
    is_pinned: bool
    source_type: str
    edge_count: int


class GraphEdgeResponse(BaseModel):
    id: str
    source: str
    target: str
    type: str
    label: str | None


class GraphDataResponse(BaseModel):
    nodes: list[GraphNodeResponse]
    edges: list[GraphEdgeResponse]


class KnowledgeStatsResponse(BaseModel):
    total_items: int
    total_edges: int
    by_category: dict[str, int]
    by_source: dict[str, int]
    recent_items: list[dict]


class SearchResultResponse(BaseModel):
    item: KnowledgeItemResponse
    snippet: str
    rank: float


# --- Helpers ---

def _item_to_response(item: KnowledgeItem) -> KnowledgeItemResponse:
    return KnowledgeItemResponse(
        id=item.id,
        project_id=item.project_id,
        title=item.title,
        content=item.content,
        content_plain=item.content_plain,
        category=item.category,
        source_type=item.source_type,
        source_ref=item.source_ref,
        tags=json.loads(item.tags) if item.tags else [],
        confidence=item.confidence,
        metadata=json.loads(item.extra_data) if item.extra_data else {},
        is_pinned=bool(item.is_pinned),
        source_note_id=item.source_note_id,
        sync_status=item.sync_status,
        last_synced_at=item.last_synced_at,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def _edge_to_response(edge: KnowledgeEdge) -> KnowledgeEdgeResponse:
    return KnowledgeEdgeResponse(
        id=edge.id,
        source_item_id=edge.source_item_id,
        target_item_id=edge.target_item_id,
        edge_type=edge.edge_type,
        label=edge.label,
        created_at=edge.created_at,
    )


async def _ensure_project(db: AsyncSession, project_id: str):
    result = await db.execute(select(Project).where(Project.id == project_id))
    if not result.scalar_one_or_none():
        raise HTTPException(404, "Projekt nicht gefunden")


# --- Aggregierte Imports (projekt-übergreifend) ---
#
# WICHTIG: Diese literale Route MUSS vor den ``/{project_id}``- und
# ``/{project_id}/{item_id}``-Routen stehen. FastAPI matcht in
# Deklarations-Reihenfolge — sonst würde ``/imports/messages`` als
# ``project_id="imports", item_id="messages"`` fehlinterpretiert.

@router.get("/imports/messages")
async def list_all_imported_messages(
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """Alle als Wissen extrahierten Nachrichten ÜBER ALLE PROJEKTE — eine Query.

    Ersetzt das frühere Frontend-Fan-out (ein Request pro Projekt → O(Projekte)
    HTTP-Calls bei jedem Inbox-Aufruf). Die Inbox braucht nur die
    ``source:external_id``-Keys für das „✓ Wissen"-Badge; ``project_id`` wird
    für Konsumenten mitgeliefert, die eine Zuordnung brauchen.

    Es werden nur die vier benötigten Spalten selektiert — keine vollen
    ``KnowledgeItem``-Objekte (deren ``content`` kann groß sein).
    """
    result = await db.execute(
        select(
            KnowledgeItem.id,
            KnowledgeItem.project_id,
            KnowledgeItem.extra_data,
            KnowledgeItem.source_ref,
        ).where(
            KnowledgeItem.source_type.in_(["email_extract", "chat_extract"]),
        )
    )
    out: list[dict] = []
    for item_id, project_id, extra_data, source_ref in result.all():
        try:
            meta = json.loads(extra_data) if extra_data else {}
        except (ValueError, TypeError):
            # Eine kaputte ``extra_data``-Zeile darf nicht die ganze Liste kippen.
            meta = {}
        out.append({
            "item_id": item_id,
            "project_id": project_id,
            "source": meta.get("source", ""),
            "external_id": meta.get("external_id"),
            "source_ref": source_ref,
        })
    return out


# --- Knowledge Item CRUD ---

@router.get("/{project_id}")
async def list_items(
    project_id: str,
    category: str | None = None,
    tag: str | None = None,
    source_type: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[KnowledgeItemResponse]:
    await _ensure_project(db, project_id)

    query = select(KnowledgeItem).where(
        KnowledgeItem.project_id == project_id
    ).order_by(KnowledgeItem.is_pinned.desc(), KnowledgeItem.updated_at.desc())

    if category:
        query = query.where(KnowledgeItem.category == category)
    if source_type:
        query = query.where(KnowledgeItem.source_type == source_type)
    if tag:
        query = query.where(KnowledgeItem.tags.contains(f'"{tag}"'))

    query = query.limit(limit).offset(offset)
    result = await db.execute(query)
    return [_item_to_response(item) for item in result.scalars().all()]


@router.post("/{project_id}", status_code=201)
async def create_item(
    project_id: str, data: KnowledgeItemCreate, db: AsyncSession = Depends(get_db)
) -> KnowledgeItemResponse:
    await _ensure_project(db, project_id)

    if data.category not in VALID_CATEGORIES:
        raise HTTPException(400, f"Ungültige Kategorie. Erlaubt: {VALID_CATEGORIES}")
    if data.source_type not in VALID_SOURCE_TYPES:
        raise HTTPException(400, f"Ungültiger source_type. Erlaubt: {VALID_SOURCE_TYPES}")
    if data.confidence not in VALID_CONFIDENCE:
        raise HTTPException(400, f"Ungültige Konfidenz. Erlaubt: {VALID_CONFIDENCE}")

    content_plain = _strip_html(data.content) if data.content else ""

    item = KnowledgeItem(
        id=_gen_id(),
        project_id=project_id,
        title=data.title,
        content=data.content,
        content_plain=content_plain,
        category=data.category,
        source_type=data.source_type,
        source_ref=data.source_ref,
        tags=json.dumps(data.tags),
        confidence=data.confidence,
        extra_data=json.dumps(data.metadata),
    )

    # T2.5 — Brain augmentation. Both stages gated by independent flags
    # (brain_contextual_retrieval_enabled, brain_embedding_enabled);
    # both default OFF, so the call is a cheap no-op out of the box.
    # ``enrich_item`` swallows every LLM/embedder failure internally so
    # the user-facing create never crashes on a flaky upstream.
    from services.retrieval.enrichment import enrich_item

    proj_lookup = await db.execute(select(Project).where(Project.id == project_id))
    project_obj = proj_lookup.scalar_one_or_none()
    await enrich_item(item, project_obj)

    db.add(item)
    await db.commit()
    await db.refresh(item)

    # Sync FTS (now includes context_summary in the indexed text — see _fts_insert).
    await _fts_insert(db, item)

    return _item_to_response(item)


@router.put("/{project_id}/{item_id}")
async def update_item(
    project_id: str, item_id: str, data: KnowledgeItemUpdate,
    db: AsyncSession = Depends(get_db),
) -> KnowledgeItemResponse:
    result = await db.execute(
        select(KnowledgeItem).where(
            KnowledgeItem.id == item_id,
            KnowledgeItem.project_id == project_id,
        )
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(404, "Knowledge Item nicht gefunden")

    # T2.5 — Track whether the change is "semantic" (title/content). Only
    # those edits warrant a re-enrichment + re-embed; tag-only or pin-only
    # changes don't move the embedding meaningfully and would just burn
    # LLM tokens.
    semantic_changed = False

    if data.title is not None:
        if data.title != item.title:
            semantic_changed = True
        item.title = data.title
    if data.content is not None:
        new_plain = _strip_html(data.content)
        if new_plain != item.content_plain:
            semantic_changed = True
        item.content = data.content
        item.content_plain = new_plain
    if data.category is not None:
        if data.category not in VALID_CATEGORIES:
            raise HTTPException(400, f"Ungültige Kategorie. Erlaubt: {VALID_CATEGORIES}")
        item.category = data.category
    if data.tags is not None:
        item.tags = json.dumps(data.tags)
    if data.confidence is not None:
        if data.confidence not in VALID_CONFIDENCE:
            raise HTTPException(400, f"Ungültige Konfidenz. Erlaubt: {VALID_CONFIDENCE}")
        item.confidence = data.confidence
    if data.metadata is not None:
        item.extra_data = json.dumps(data.metadata)
    if data.is_pinned is not None:
        item.is_pinned = data.is_pinned

    item.updated_at = _now()

    # T2.5 — Re-enrich only when title/content actually changed.
    if semantic_changed:
        from services.retrieval.enrichment import enrich_item

        proj_lookup = await db.execute(select(Project).where(Project.id == project_id))
        project_obj = proj_lookup.scalar_one_or_none()
        await enrich_item(item, project_obj)

    if item.source_note_id:
        note_result = await db.execute(
            select(Note).where(
                Note.id == item.source_note_id,
                Note.project_id == project_id,
            )
        )
        note = note_result.scalar_one_or_none()
        if note:
            note.title = item.title
            note.content = item.content
            note.updated_at = _now()
            item.sync_status = "synced"
            item.last_synced_at = _now()
        else:
            item.sync_status = "conflict"
            item.last_synced_at = _now()

    await db.commit()
    await db.refresh(item)

    # Sync FTS
    await _fts_update(db, item)

    return _item_to_response(item)


@router.delete("/{project_id}/{item_id}")
async def delete_item(
    project_id: str, item_id: str, db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(KnowledgeItem).where(
            KnowledgeItem.id == item_id,
            KnowledgeItem.project_id == project_id,
        )
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(404, "Knowledge Item nicht gefunden")

    if item.source_note_id:
        note_result = await db.execute(
            select(Note).where(
                Note.id == item.source_note_id,
                Note.project_id == project_id,
            )
        )
        note = note_result.scalar_one_or_none()
        if note:
            linked_ids = json.loads(note.linked_knowledge_ids) if note.linked_knowledge_ids else []
            if item_id in linked_ids:
                linked_ids.remove(item_id)
                note.linked_knowledge_ids = json.dumps(linked_ids)
                note.updated_at = _now()

    # FTS cleanup
    await _fts_delete(db, item_id)

    await db.delete(item)
    await db.commit()
    return {"success": True}


# --- Edges ---

@router.post("/{project_id}/edges", status_code=201)
async def create_edge(
    project_id: str, data: EdgeCreate, db: AsyncSession = Depends(get_db)
) -> KnowledgeEdgeResponse:
    if data.edge_type not in VALID_EDGE_TYPES:
        raise HTTPException(400, f"Ungültiger edge_type. Erlaubt: {VALID_EDGE_TYPES}")

    # Verify both items belong to this project
    for iid in [data.source_item_id, data.target_item_id]:
        result = await db.execute(
            select(KnowledgeItem).where(
                KnowledgeItem.id == iid,
                KnowledgeItem.project_id == project_id,
            )
        )
        if not result.scalar_one_or_none():
            raise HTTPException(404, f"Knowledge Item {iid} nicht gefunden in Projekt")

    if data.source_item_id == data.target_item_id:
        raise HTTPException(400, "Selbst-Verknüpfung nicht erlaubt")

    edge = KnowledgeEdge(
        id=_gen_id(),
        source_item_id=data.source_item_id,
        target_item_id=data.target_item_id,
        edge_type=data.edge_type,
        label=data.label,
    )
    db.add(edge)
    await db.commit()
    await db.refresh(edge)
    return _edge_to_response(edge)


@router.delete("/{project_id}/edges/{edge_id}")
async def delete_edge(
    project_id: str, edge_id: str, db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(KnowledgeEdge).where(KnowledgeEdge.id == edge_id)
    )
    edge = result.scalar_one_or_none()
    if not edge:
        raise HTTPException(404, "Edge nicht gefunden")

    await db.delete(edge)
    await db.commit()
    return {"success": True}


# --- Search (FTS5) ---

@router.get("/{project_id}/search")
async def search_items(
    project_id: str,
    q: str = Query("", min_length=1),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> list[SearchResultResponse]:
    """Full-text search over knowledge items using FTS5."""
    # Sanitize query for FTS5
    sanitized = _sanitize_fts_query(q)
    if not sanitized:
        return []

    try:
        result = await db.execute(text("""
            SELECT ki.*, fts.rank
            FROM knowledge_items_fts fts
            JOIN knowledge_items ki ON ki.rowid = fts.rowid
            WHERE knowledge_items_fts MATCH :query
            AND ki.project_id = :project_id
            ORDER BY fts.rank
            LIMIT :limit
        """), {"query": sanitized, "project_id": project_id, "limit": limit})

        rows = result.fetchall()
    except Exception as e:
        logger.warning("FTS search failed for query '%s': %s", q, e)
        # Fallback to LIKE search
        return await _fallback_search(db, project_id, q, limit)

    items = []
    for row in rows:
        item_data = KnowledgeItemResponse(
            id=row.id,
            project_id=row.project_id,
            title=row.title,
            content=row.content,
            content_plain=row.content_plain,
            category=row.category,
            source_type=row.source_type,
            source_ref=row.source_ref,
            source_note_id=row.source_note_id,
            sync_status=row.sync_status,
            last_synced_at=row.last_synced_at,
            tags=json.loads(row.tags) if row.tags else [],
            confidence=row.confidence,
            metadata=json.loads(row.extra_data) if row.extra_data else {},
            is_pinned=bool(row.is_pinned),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
        # Create snippet from content_plain
        snippet = _create_snippet(row.content_plain, q)
        items.append(SearchResultResponse(
            item=item_data,
            snippet=snippet,
            rank=abs(row.rank) if row.rank else 0,
        ))

    return items


# --- Graph Data ---

@router.get("/{project_id}/graph")
async def get_graph(
    project_id: str,
    category: str | None = None,
    tag: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> GraphDataResponse:
    await _ensure_project(db, project_id)

    # Get items
    query = select(KnowledgeItem).where(KnowledgeItem.project_id == project_id)
    if category:
        query = query.where(KnowledgeItem.category == category)
    if tag:
        query = query.where(KnowledgeItem.tags.contains(f'"{tag}"'))

    result = await db.execute(query)
    items = result.scalars().all()
    item_ids = {item.id for item in items}

    # Get edges between these items
    if not item_ids:
        return GraphDataResponse(nodes=[], edges=[])

    edges_result = await db.execute(
        select(KnowledgeEdge).where(
            KnowledgeEdge.source_item_id.in_(item_ids),
            KnowledgeEdge.target_item_id.in_(item_ids),
        )
    )
    edges = edges_result.scalars().all()

    # Count edges per node
    edge_counts: dict[str, int] = {}
    for e in edges:
        edge_counts[e.source_item_id] = edge_counts.get(e.source_item_id, 0) + 1
        edge_counts[e.target_item_id] = edge_counts.get(e.target_item_id, 0) + 1

    nodes = [
        GraphNodeResponse(
            id=item.id,
            title=item.title,
            category=item.category,
            tags=json.loads(item.tags) if item.tags else [],
            is_pinned=bool(item.is_pinned),
            source_type=item.source_type,
            edge_count=edge_counts.get(item.id, 0),
        )
        for item in items
    ]

    graph_edges = [
        GraphEdgeResponse(
            id=e.id,
            source=e.source_item_id,
            target=e.target_item_id,
            type=e.edge_type,
            label=e.label,
        )
        for e in edges
    ]

    return GraphDataResponse(nodes=nodes, edges=graph_edges)


# --- Stats ---

@router.get("/{project_id}/stats")
async def get_stats(
    project_id: str, db: AsyncSession = Depends(get_db)
) -> KnowledgeStatsResponse:
    await _ensure_project(db, project_id)

    total_items = await db.scalar(
        select(func.count()).where(KnowledgeItem.project_id == project_id)
    ) or 0

    # Count edges for this project's items
    item_ids_q = select(KnowledgeItem.id).where(KnowledgeItem.project_id == project_id)
    total_edges = await db.scalar(
        select(func.count()).where(
            KnowledgeEdge.source_item_id.in_(item_ids_q)
        )
    ) or 0

    # By category
    cat_result = await db.execute(
        select(KnowledgeItem.category, func.count()).where(
            KnowledgeItem.project_id == project_id
        ).group_by(KnowledgeItem.category)
    )
    by_category = {row[0]: row[1] for row in cat_result.all()}

    # By source
    src_result = await db.execute(
        select(KnowledgeItem.source_type, func.count()).where(
            KnowledgeItem.project_id == project_id
        ).group_by(KnowledgeItem.source_type)
    )
    by_source = {row[0]: row[1] for row in src_result.all()}

    # Recent items
    recent_result = await db.execute(
        select(KnowledgeItem).where(
            KnowledgeItem.project_id == project_id
        ).order_by(KnowledgeItem.updated_at.desc()).limit(5)
    )
    recent = [
        {"id": r.id, "title": r.title, "category": r.category, "updated_at": r.updated_at}
        for r in recent_result.scalars().all()
    ]

    return KnowledgeStatsResponse(
        total_items=total_items,
        total_edges=total_edges,
        by_category=by_category,
        by_source=by_source,
        recent_items=recent,
    )


# --- Documents ---

@router.get("/{project_id}/documents")
async def list_documents(
    project_id: str, db: AsyncSession = Depends(get_db)
) -> list[dict]:
    await _ensure_project(db, project_id)
    result = await db.execute(
        select(ProjectDocument).where(
            ProjectDocument.project_id == project_id
        ).order_by(ProjectDocument.file_name)
    )
    return [
        {
            "id": d.id,
            "project_id": d.project_id,
            "file_path": d.file_path,
            "file_name": d.file_name,
            "file_type": d.file_type,
            "file_size": d.file_size,
            "file_hash": d.file_hash,
            "last_scanned_at": d.last_scanned_at,
            "scan_status": d.scan_status,
            "total_sections": d.total_sections,
            "extracted_items": d.extracted_items,
            "created_at": d.created_at,
            "updated_at": d.updated_at,
        }
        for d in result.scalars().all()
    ]


@router.get("/{project_id}/documents/{doc_id}")
async def get_document(
    project_id: str, doc_id: str, db: AsyncSession = Depends(get_db)
) -> dict:
    result = await db.execute(
        select(ProjectDocument).where(
            ProjectDocument.id == doc_id,
            ProjectDocument.project_id == project_id,
        )
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "Dokument nicht gefunden")

    # Get knowledge items from this document
    items_result = await db.execute(
        select(KnowledgeItem).where(
            KnowledgeItem.project_id == project_id,
            KnowledgeItem.source_type == "document",
            KnowledgeItem.source_ref == doc_id,
        ).order_by(KnowledgeItem.created_at)
    )

    return {
        "id": doc.id,
        "project_id": doc.project_id,
        "file_path": doc.file_path,
        "file_name": doc.file_name,
        "file_type": doc.file_type,
        "file_size": doc.file_size,
        "file_hash": doc.file_hash,
        "last_scanned_at": doc.last_scanned_at,
        "scan_status": doc.scan_status,
        "total_sections": doc.total_sections,
        "extracted_items": doc.extracted_items,
        "knowledge_items": [_item_to_response(i).model_dump() for i in items_result.scalars().all()],
    }


@router.delete("/{project_id}/documents/{doc_id}")
async def delete_document(
    project_id: str, doc_id: str, db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(ProjectDocument).where(
            ProjectDocument.id == doc_id,
            ProjectDocument.project_id == project_id,
        )
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "Dokument nicht gefunden")

    # Delete associated knowledge items
    items_result = await db.execute(
        select(KnowledgeItem).where(
            KnowledgeItem.source_type == "document",
            KnowledgeItem.source_ref == doc_id,
        )
    )
    for item in items_result.scalars().all():
        await _fts_delete(db, item.id)
        await db.delete(item)

    await db.delete(doc)
    await db.commit()
    return {"success": True}


# --- Docs Path Validation ---

@router.get("/{project_id}/validate-docs-path")
async def validate_docs_path(
    project_id: str, db: AsyncSession = Depends(get_db)
) -> dict:
    """Check if the project's docs_path is valid and list files."""
    import os

    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(404, "Projekt nicht gefunden")

    docs_path = project.docs_path
    if not docs_path:
        return {"valid": False, "error": "Kein Dokumentenpfad konfiguriert", "file_count": 0, "total_size": 0}

    if not os.path.isdir(docs_path):
        return {"valid": False, "error": "Pfad existiert nicht", "file_count": 0, "total_size": 0}

    supported = (".docx", ".pdf")
    file_count = 0
    total_size = 0
    for root, _dirs, files in os.walk(docs_path):
        for f in files:
            if f.lower().endswith(supported) and not f.startswith("~$"):
                fp = os.path.join(root, f)
                file_count += 1
                total_size += os.path.getsize(fp)

    return {"valid": True, "file_count": file_count, "total_size": total_size}


# --- Item Detail (wildcard route — registered AFTER all literal-path GETs
# so /graph, /stats, /search, /documents, /validate-docs-path are not shadowed) ---

@router.get("/{project_id}/{item_id}")
async def get_item(
    project_id: str, item_id: str, db: AsyncSession = Depends(get_db)
) -> KnowledgeItemDetailResponse:
    result = await db.execute(
        select(KnowledgeItem).where(
            KnowledgeItem.id == item_id,
            KnowledgeItem.project_id == project_id,
        )
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(404, "Knowledge Item nicht gefunden")

    # Get edges (both directions)
    edges_result = await db.execute(
        select(KnowledgeEdge).where(
            or_(
                KnowledgeEdge.source_item_id == item_id,
                KnowledgeEdge.target_item_id == item_id,
            )
        )
    )
    edges = edges_result.scalars().all()

    # Get neighbor items
    neighbor_ids = set()
    for e in edges:
        if e.source_item_id != item_id:
            neighbor_ids.add(e.source_item_id)
        if e.target_item_id != item_id:
            neighbor_ids.add(e.target_item_id)

    neighbors = []
    if neighbor_ids:
        nb_result = await db.execute(
            select(KnowledgeItem).where(KnowledgeItem.id.in_(neighbor_ids))
        )
        for nb in nb_result.scalars().all():
            neighbors.append({
                "id": nb.id,
                "title": nb.title,
                "category": nb.category,
            })

    resp = _item_to_response(item)
    return KnowledgeItemDetailResponse(
        **resp.model_dump(),
        edges=[_edge_to_response(e) for e in edges],
        neighbors=neighbors,
    )


# --- Document Scan ---

class ScanDocsRequest(BaseModel):
    force: bool = False


@router.post("/{project_id}/scan-docs")
async def scan_docs(
    project_id: str,
    data: ScanDocsRequest | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Start document scan and extraction pipeline."""
    await _ensure_project(db, project_id)

    from services.doc_scanner import scan_project_documents
    force = data.force if data else False
    result = await scan_project_documents(project_id, db, force=force)
    return result


@router.post("/{project_id}/scan-doc/{doc_id}")
async def scan_single_doc(
    project_id: str, doc_id: str, db: AsyncSession = Depends(get_db)
) -> dict:
    """Re-scan a single document."""
    await _ensure_project(db, project_id)

    result = await db.execute(
        select(ProjectDocument).where(
            ProjectDocument.id == doc_id,
            ProjectDocument.project_id == project_id,
        )
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "Dokument nicht gefunden")

    from services.doc_scanner import scan_project_documents
    # Force re-scan by setting hash to empty
    doc.file_hash = ""
    await db.commit()

    scan_result = await scan_project_documents(project_id, db, force=False)
    return scan_result


# --- AI-Powered: Research → Knowledge ---

class ResearchToKnowledgeRequest(BaseModel):
    topic: str
    team: str | None = None
    # Confluence Deep-Research: wenn page_url ODER space gesetzt ist, läuft
    # die Recherche über die Confluence-Pipeline (Seitenbaum + PDF-Attachments)
    # statt über den generischen Agent-Call.
    confluence_page_url: str | None = None
    confluence_space: str | None = None
    include_children: bool = False


@router.post("/{project_id}/research")
async def research_to_knowledge(
    project_id: str, data: ResearchToKnowledgeRequest, db: AsyncSession = Depends(get_db)
) -> KnowledgeItemResponse:
    """Research a topic via AI-Assist and save result as Knowledge Item.

    Two modes:
      * Confluence Deep-Research — when ``confluence_page_url`` or
        ``confluence_space`` is set: discovery + PDF-attachment analysis
        + synthesis via AI-Assist's ``/api/research/confluence``.
      * Generic agent research — otherwise: a single agent call.
    """
    await _ensure_project(db, project_id)

    if data.confluence_page_url or data.confluence_space:
        return await _research_confluence_to_knowledge(project_id, data, db)

    topic_norm = (data.topic or "").strip().lower()
    ref_basis = f"{project_id}|ai-research|{topic_norm}"
    source_ref_hash = hashlib.sha256(ref_basis.encode("utf-8")).hexdigest()

    pre_existing = await db.execute(
        select(KnowledgeItem).where(
            KnowledgeItem.project_id == project_id,
            KnowledgeItem.source_type == "research",
            KnowledgeItem.source_ref == source_ref_hash,
        )
    )
    pre_existing_item = pre_existing.scalar_one_or_none()
    if pre_existing_item:
        return _item_to_response(pre_existing_item)

    if not ai_assist.is_connected:
        await ai_assist.health_check()
    if not ai_assist.is_connected:
        raise HTTPException(503, "AI-Assist nicht erreichbar")

    # Build context from existing knowledge
    existing = await db.execute(
        select(KnowledgeItem).where(
            KnowledgeItem.project_id == project_id
        ).order_by(KnowledgeItem.updated_at.desc()).limit(5)
    )
    context_items = existing.scalars().all()
    context = ""
    if context_items:
        context = "\n[Bestehendes Projektwissen]\n"
        for ki in context_items:
            context += f"- {ki.title}: {ki.content_plain[:200]}\n"

    full_query = f"{context}\n[Recherche-Anfrage]\n{data.topic}"

    session_id = f"projecthub-kb-research-{_gen_id()}"
    result_data = await ai_assist.agent_call(
        session_id=session_id,
        message=full_query,
    )

    response_text = ""
    if result_data:
        response_text = result_data.get("response") or ""

    if not response_text:
        raise HTTPException(502, "Leere Antwort von AI-Assist")

    # Re-check for duplicates that may have been created while the LLM was running
    # (narrows the race window between pre_existing check and the insert below).
    race_check = await db.execute(
        select(KnowledgeItem).where(
            KnowledgeItem.project_id == project_id,
            KnowledgeItem.source_type == "research",
            KnowledgeItem.source_ref == source_ref_hash,
        )
    )
    race_existing = race_check.scalar_one_or_none()
    if race_existing:
        logger.info("Research duplicate detected after LLM call, returning existing item")
        return _item_to_response(race_existing)

    # Try to extract tags via LLM (quick)
    tags = _extract_tags_from_text(data.topic + " " + response_text[:500])

    content_plain = _strip_html(response_text) if "<" in response_text else response_text
    item = KnowledgeItem(
        id=_gen_id(),
        project_id=project_id,
        title=f"Recherche: {data.topic[:80]}",
        content=f"<p>{response_text}</p>" if "<" not in response_text else response_text,
        content_plain=content_plain[:5000],
        category="reference",
        source_type="research",
        source_ref=source_ref_hash,
        tags=json.dumps(tags),
        confidence="medium",
        extra_data=json.dumps({"topic": data.topic, "team": data.team, "session_id": session_id}),
    )
    try:
        db.add(item)
        await db.commit()
        await db.refresh(item)
    except IntegrityError:
        await db.rollback()
        existing_result = await db.execute(
            select(KnowledgeItem).where(
                KnowledgeItem.project_id == project_id,
                KnowledgeItem.source_type == "research",
                KnowledgeItem.source_ref == source_ref_hash,
            )
        )
        existing = existing_result.scalar_one_or_none()
        if existing:
            return _item_to_response(existing)
        raise
    await _fts_insert(db, item)

    # Auto-link to items with overlapping tags
    await _auto_link_by_tags(db, project_id, item)

    return _item_to_response(item)


def _confluence_ref_hash(project_id: str, data: "ResearchToKnowledgeRequest") -> str:
    """Stable dedup key for a Confluence research (topic + target)."""
    ref_basis = (
        f"{project_id}|confluence|{data.confluence_page_url or ''}"
        f"|{data.confluence_space or ''}|{(data.topic or '').strip().lower()}"
    )
    return hashlib.sha256(ref_basis.encode("utf-8")).hexdigest()


async def _research_confluence_to_knowledge(
    project_id: str, data: ResearchToKnowledgeRequest, db: AsyncSession
) -> KnowledgeItemResponse:
    """Confluence Deep-Research via AI-Assist → ein synthetisiertes Knowledge Item.

    Anders als der generische Pfad läuft hier die volle Confluence-Pipeline
    (Seitenbaum-Discovery + PDF-Attachment-Analyse + Synthese). Das Ergebnis
    ist ein Markdown-Dokument; die einzelnen Findings stecken darin und in
    ``extra_data``. Kein 5000-Zeichen-Cap mehr — ``content`` ist Text.
    """
    topic = (data.topic or "").strip()
    if not topic:
        raise HTTPException(400, "topic ist erforderlich")

    source_ref_hash = _confluence_ref_hash(project_id, data)

    async def _find_existing() -> KnowledgeItem | None:
        res = await db.execute(
            select(KnowledgeItem).where(
                KnowledgeItem.project_id == project_id,
                KnowledgeItem.source_type == "confluence",
                KnowledgeItem.source_ref == source_ref_hash,
            )
        )
        return res.scalar_one_or_none()

    pre_existing = await _find_existing()
    if pre_existing:
        return _item_to_response(pre_existing)

    # AI-Assist Confluence-Pipeline aufrufen
    try:
        result = await ai_assist.research_confluence(
            topic,
            url=data.confluence_page_url or None,
            space_key=data.confluence_space or None,
            include_children=data.include_children,
        )
    except ConnectionError:
        raise HTTPException(503, "AI-Assist nicht erreichbar")
    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        try:
            detail = e.response.json().get("detail", e.response.text[:300])
        except (ValueError, AttributeError):
            detail = e.response.text[:300]
        if status in (422, 502, 504):
            # Auflösungs-/Confluence-/Timeout-Fehler 1:1 durchreichen.
            raise HTTPException(status, detail)
        raise HTTPException(502, f"AI-Assist Confluence-Research fehlgeschlagen: {detail}")

    result = result or {}
    markdown = result.get("markdown") or ""
    summary = result.get("summary") or ""
    if not markdown and not summary:
        raise HTTPException(502, "Leere Antwort von AI-Assist Confluence-Research")

    findings = result.get("findings") or []
    pages_analyzed = result.get("pages_analyzed", 0)
    pdfs_analyzed = result.get("pdfs_analyzed", 0)

    # Race-Re-Check — der Pipeline-Call kann Minuten dauern.
    race_existing = await _find_existing()
    if race_existing:
        logger.info("Confluence research duplicate detected after call, returning existing item")
        return _item_to_response(race_existing)

    # Tags: Finding-Kategorien + Topic-Keywords.
    finding_categories = sorted({
        f.get("category", "") for f in findings if f.get("category")
    })
    keyword_tags = _extract_tags_from_text(topic + " " + summary[:500])
    tags = list(dict.fromkeys(finding_categories + keyword_tags))[:8]

    # Konfidenz: Mehrheit der Findings (sonst medium).
    confidences = [f.get("confidence", "medium") for f in findings]
    if confidences:
        high_ratio = confidences.count("high") / len(confidences)
        confidence = "high" if high_ratio > 0.5 else (
            "medium" if confidences.count("high") else "low"
        )
    else:
        confidence = "medium"

    # Synthese ist Markdown → für das rich-text content-Feld nach HTML wandeln
    # (NodeDetailPanel rendert via dangerouslySetInnerHTML).
    content_html = _markdown_to_html(markdown) if markdown else f"<p>{html.escape(summary)}</p>"
    content_plain = _strip_html(content_html)
    item = KnowledgeItem(
        id=_gen_id(),
        project_id=project_id,
        title=f"Confluence-Recherche: {topic[:80]}",
        content=content_html,
        content_plain=content_plain[:20000],
        category="reference",
        source_type="confluence",
        source_ref=source_ref_hash,
        tags=json.dumps(tags),
        confidence=confidence,
        extra_data=json.dumps({
            "topic": topic,
            "team": data.team,
            "confluence_page_url": data.confluence_page_url,
            "confluence_space": data.confluence_space,
            "include_children": data.include_children,
            "summary": summary[:2000],
            "findings_count": len(findings),
            "pages_analyzed": pages_analyzed,
            "pdfs_analyzed": pdfs_analyzed,
            "errors": (result.get("errors") or [])[:20],
        }),
    )
    try:
        db.add(item)
        await db.commit()
        await db.refresh(item)
    except IntegrityError:
        await db.rollback()
        existing = await _find_existing()
        if existing:
            return _item_to_response(existing)
        raise
    await _fts_insert(db, item)
    await _auto_link_by_tags(db, project_id, item)

    return _item_to_response(item)


# --- AI-Powered: Suggest Links ---

@router.post("/{project_id}/suggest-links")
async def suggest_links(
    project_id: str,
    item_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """LLM suggests edges between an item and other project items."""
    result = await db.execute(
        select(KnowledgeItem).where(
            KnowledgeItem.id == item_id,
            KnowledgeItem.project_id == project_id,
        )
    )
    source_item = result.scalar_one_or_none()
    if not source_item:
        raise HTTPException(404, "Knowledge Item nicht gefunden")

    # Get existing edge targets
    existing_edges = await db.execute(
        select(KnowledgeEdge).where(
            or_(
                KnowledgeEdge.source_item_id == item_id,
                KnowledgeEdge.target_item_id == item_id,
            )
        )
    )
    linked_ids = set()
    for e in existing_edges.scalars().all():
        linked_ids.add(e.source_item_id)
        linked_ids.add(e.target_item_id)

    # Get unlinked items
    other_result = await db.execute(
        select(KnowledgeItem).where(
            KnowledgeItem.project_id == project_id,
            KnowledgeItem.id != item_id,
            ~KnowledgeItem.id.in_(linked_ids) if linked_ids else KnowledgeItem.id != item_id,
        ).limit(20)
    )
    other_items = other_result.scalars().all()

    if not other_items:
        return []

    # Score by tag overlap (no LLM needed for v1)
    source_tags = set(json.loads(source_item.tags)) if source_item.tags else set()
    suggestions = []

    for other in other_items:
        other_tags = set(json.loads(other.tags)) if other.tags else set()
        overlap = source_tags & other_tags

        # Score: tag overlap + same category bonus
        score = len(overlap) * 0.3
        if source_item.category == other.category:
            score += 0.2

        # Title word overlap
        source_words = set(source_item.title.lower().split())
        other_words = set(other.title.lower().split())
        word_overlap = source_words & other_words - {"und", "der", "die", "das", "für", "mit", "von"}
        score += len(word_overlap) * 0.15

        if score >= 0.3:
            edge_type = "related"
            if source_item.source_type == "document" and other.source_type == "document":
                edge_type = "based_on"

            suggestions.append({
                "target_item_id": other.id,
                "target_title": other.title,
                "target_category": other.category,
                "edge_type": edge_type,
                "reason": f"Gemeinsame Tags: {', '.join(overlap)}" if overlap else f"Ähnliche Thematik",
                "confidence": min(1.0, score),
            })

    suggestions.sort(key=lambda x: x["confidence"], reverse=True)
    return suggestions[:10]


# --- Import: Note → Knowledge ---

class ImportNoteRequest(BaseModel):
    note_id: str


@router.post("/{project_id}/import/note")
async def import_note(
    project_id: str, data: ImportNoteRequest, db: AsyncSession = Depends(get_db)
) -> KnowledgeItemResponse:
    """Import an existing note as a Knowledge Item."""
    await _ensure_project(db, project_id)

    result = await db.execute(
        select(Note).where(Note.id == data.note_id, Note.project_id == project_id)
    )
    note = result.scalar_one_or_none()
    if not note:
        raise HTTPException(404, "Notiz nicht gefunden")

    existing_result = await db.execute(
        select(KnowledgeItem).where(
            KnowledgeItem.project_id == project_id,
            KnowledgeItem.source_note_id == note.id,
            KnowledgeItem.source_type == "note_import",
        )
    )
    existing_item = existing_result.scalar_one_or_none()
    if existing_item:
        linked_ids = json.loads(note.linked_knowledge_ids) if note.linked_knowledge_ids else []
        if existing_item.id not in linked_ids:
            linked_ids.append(existing_item.id)
            note.linked_knowledge_ids = json.dumps(linked_ids)
            note.updated_at = _now()
            await db.commit()
        return _item_to_response(existing_item)

    content_plain = _strip_html(note.content) if note.content else ""
    tags = json.loads(note.tags) if note.tags else []

    item = KnowledgeItem(
        id=_gen_id(),
        project_id=project_id,
        title=note.title or "Importierte Notiz",
        content=note.content,
        content_plain=content_plain[:5000],
        category="reference",
        source_type="note_import",
        source_ref=note.id,
        source_note_id=note.id,
        sync_status="synced",
        last_synced_at=_now(),
        tags=json.dumps(tags),
        confidence="high",
        extra_data=json.dumps({"imported_from": "note", "note_id": note.id}),
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)

    linked_ids = json.loads(note.linked_knowledge_ids) if note.linked_knowledge_ids else []
    if item.id not in linked_ids:
        linked_ids.append(item.id)
        note.linked_knowledge_ids = json.dumps(linked_ids)
        note.updated_at = _now()
        await db.commit()

    await _fts_insert(db, item)
    await _auto_link_by_tags(db, project_id, item)

    return _item_to_response(item)


# --- Import: Research → Knowledge ---

class ImportResearchRequest(BaseModel):
    research_id: str


@router.post("/{project_id}/import/research")
async def import_research(
    project_id: str, data: ImportResearchRequest, db: AsyncSession = Depends(get_db)
) -> KnowledgeItemResponse:
    """Import an existing research result as a Knowledge Item."""
    await _ensure_project(db, project_id)

    result = await db.execute(
        select(ResearchResult).where(
            ResearchResult.id == data.research_id,
            ResearchResult.project_id == project_id,
        )
    )
    research = result.scalar_one_or_none()
    if not research:
        raise HTTPException(404, "Recherche nicht gefunden")

    existing_result = await db.execute(
        select(KnowledgeItem).where(
            KnowledgeItem.project_id == project_id,
            KnowledgeItem.source_type == "research",
            KnowledgeItem.source_ref == research.id,
        )
    )
    existing = existing_result.scalar_one_or_none()
    if existing:
        return _item_to_response(existing)

    content_plain = _strip_html(research.result) if "<" in research.result else research.result
    tags = _extract_tags_from_text(research.query + " " + content_plain[:500])

    item = KnowledgeItem(
        id=_gen_id(),
        project_id=project_id,
        title=f"Recherche: {research.query[:80]}",
        content=f"<p>{research.result}</p>" if "<" not in research.result else research.result,
        content_plain=content_plain[:5000],
        category="reference",
        source_type="research",
        source_ref=research.id,
        tags=json.dumps(tags),
        confidence="medium",
        extra_data=json.dumps({
            "imported_from": "research",
            "research_id": research.id,
            "original_query": research.query,
        }),
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    await _fts_insert(db, item)
    await _auto_link_by_tags(db, project_id, item)

    return _item_to_response(item)


@router.get("/{project_id}/imports/research")
async def list_imported_research(
    project_id: str, db: AsyncSession = Depends(get_db)
) -> list[str]:
    """Return research_ids that have already been imported as KnowledgeItems."""
    await _ensure_project(db, project_id)
    result = await db.execute(
        select(KnowledgeItem.source_ref).where(
            KnowledgeItem.project_id == project_id,
            KnowledgeItem.source_type == "research",
            KnowledgeItem.source_ref.isnot(None),
        )
    )
    return [row[0] for row in result.all() if row[0]]


@router.get("/{project_id}/imports/messages")
async def list_imported_messages(
    project_id: str, db: AsyncSession = Depends(get_db)
) -> list[dict]:
    """Return messages already extracted into knowledge for this project."""
    await _ensure_project(db, project_id)
    result = await db.execute(
        select(KnowledgeItem).where(
            KnowledgeItem.project_id == project_id,
            KnowledgeItem.source_type.in_(["email_extract", "chat_extract"]),
        )
    )
    out = []
    for item in result.scalars().all():
        meta = json.loads(item.extra_data) if item.extra_data else {}
        out.append({
            "item_id": item.id,
            "source": meta.get("source", ""),
            "external_id": meta.get("external_id"),
            "source_ref": item.source_ref,
        })
    return out


# --- Extract: Email/Chat → Knowledge ---

class ExtractMessageRequest(BaseModel):
    subject: str
    sender: str
    content: str
    source: str = "email"  # email or webex
    external_id: str | None = None


@router.post("/{project_id}/extract/message")
async def extract_from_message(
    project_id: str, data: ExtractMessageRequest, db: AsyncSession = Depends(get_db)
) -> KnowledgeItemResponse:
    """Extract knowledge from an email or chat message."""
    await _ensure_project(db, project_id)

    content_plain = _strip_html(data.content) if "<" in data.content else data.content
    tags = _extract_tags_from_text(data.subject + " " + content_plain[:300])

    source_type = "email_extract" if data.source == "email" else "chat_extract"

    if data.external_id:
        ref_basis = f"{project_id}|{data.source}|{data.external_id}"
    else:
        ref_basis = f"{project_id}|{data.source}|{data.sender}|{data.subject}|{content_plain[:500]}"
    source_ref_hash = hashlib.sha256(ref_basis.encode("utf-8")).hexdigest()

    existing_result = await db.execute(
        select(KnowledgeItem).where(
            KnowledgeItem.project_id == project_id,
            KnowledgeItem.source_type == source_type,
            KnowledgeItem.source_ref == source_ref_hash,
        )
    )
    existing = existing_result.scalar_one_or_none()
    if existing:
        return _item_to_response(existing)

    item = KnowledgeItem(
        id=_gen_id(),
        project_id=project_id,
        title=data.subject[:300] if data.subject else "Nachricht-Extrakt",
        content=f"<p><strong>Von:</strong> {data.sender}</p><p>{data.content}</p>",
        content_plain=f"Von: {data.sender}\n{content_plain}"[:5000],
        category="reference",
        source_type=source_type,
        source_ref=source_ref_hash,
        tags=json.dumps(tags),
        confidence="low",
        extra_data=json.dumps({"sender": data.sender, "source": data.source, "external_id": data.external_id} if data.external_id else {"sender": data.sender, "source": data.source}),
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    await _fts_insert(db, item)

    try:
        link = LinkedMessage(
            id=_gen_id(),
            link_target="knowledge",
            target_id=item.id,
            source=data.source,
            source_ref=source_ref_hash,
            subject=data.subject[:500] if data.subject else "",
            sender=data.sender[:200] if data.sender else "",
            snippet=content_plain[:300],
        )
        db.add(link)
        await db.commit()
    except IntegrityError:
        await db.rollback()

    return _item_to_response(item)


# --- Helper: Auto-link by tags ---

# --- T2.6: Backfill Embeddings + Context Snippets ---


class BackfillEmbeddingsRequest(BaseModel):
    """Body for POST /{project_id}/backfill-embeddings.

    ``force`` re-enriches items that already have a context_summary / embedding —
    useful after a model change (clear ``embedding_model`` invariant). ``False``
    (default) only touches items lacking one of the two pieces.

    ``rate_limit_seconds`` is the inter-item sleep. 0 disables (good for
    small projects + tests); production-grade values are 1-36s depending
    on the LLM proxy budget.
    """

    force: bool = False
    rate_limit_seconds: float = 0.0


@router.post("/{project_id}/backfill-embeddings", status_code=202)
async def backfill_embeddings(
    project_id: str,
    data: BackfillEmbeddingsRequest | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Spawn a detached background task that enriches items in this project.

    The task lives on its own ``async_session`` (pattern mirrors
    ``services/synapse_pipeline.py``). Progress is broadcast via
    ``sse_hub.emit("backfill_progress", ...)`` so the frontend can render a
    live progress bar without polling.

    Returns immediately with status=started; the caller subscribes to the
    SSE stream for updates.
    """
    await _ensure_project(db, project_id)
    force = data.force if data else False
    rate_limit_seconds = data.rate_limit_seconds if data else 0.0
    asyncio.create_task(
        _run_backfill_embeddings(
            project_id, force=force, rate_limit_seconds=rate_limit_seconds
        )
    )
    return {
        "status": "started",
        "project_id": project_id,
        "force": force,
        "rate_limit_seconds": rate_limit_seconds,
    }


async def _run_backfill_embeddings(
    project_id: str, *, force: bool, rate_limit_seconds: float
) -> None:
    """Background runner — owns its own session, never raises into the caller.

    Filtering:
        force=False → only items where ``embedding IS NULL OR context_summary = ''``
        force=True  → ALL items

    Per-item flow:
        1. ``enrich_item(item, project)`` — context + embedding (each gated
           by its own setting flag, so the task is a no-op when both flags
           are off; logs WARN once and exits early in that case).
        2. ``_fts_insert(db, item)`` — re-index with the new context_summary
           prepended (matches the create-path behaviour).
        3. Commit per item so partial progress survives a mid-run crash.
        4. SSE progress event after each step.
    """
    from database import async_session
    from services.retrieval.enrichment import enrich_item
    from services.sse_hub import sse_hub

    contextual_on, embedding_on = _read_brain_flags()
    if not contextual_on and not embedding_on:
        logger.warning(
            "[backfill] both flags off — nothing to do (project=%s)", project_id
        )
        try:
            await sse_hub.emit(
                "backfill_progress",
                {
                    "project_id": project_id,
                    "phase": "skipped",
                    "reason": "both brain flags off",
                },
            )
        except Exception:
            pass
        return

    try:
        async with async_session() as db:
            proj_res = await db.execute(
                select(Project).where(Project.id == project_id)
            )
            project = proj_res.scalar_one_or_none()
            if project is None:
                logger.warning("[backfill] project %s vanished", project_id)
                return

            q = select(KnowledgeItem).where(KnowledgeItem.project_id == project_id)
            if not force:
                q = q.where(
                    or_(
                        KnowledgeItem.embedding.is_(None),
                        KnowledgeItem.context_summary == "",
                    )
                )
            items = (await db.execute(q)).scalars().all()

            stats = {
                "total": len(items),
                "processed": 0,
                "skipped": 0,
                "failed": 0,
            }
            await _safe_emit(
                sse_hub,
                "backfill_progress",
                {"project_id": project_id, "phase": "start", **stats},
            )

            for item in items:
                try:
                    result = await enrich_item(item, project)
                    if result["context_set"] or result["embedding_set"]:
                        item.updated_at = _now()
                        await _fts_insert(db, item)
                        # _fts_insert commits; nothing left to do here
                        stats["processed"] += 1
                    else:
                        # enrich was a no-op (both flags off in settings, OR
                        # nothing for the LLM to ground on) — skip without
                        # touching FTS so the row is unchanged.
                        stats["skipped"] += 1
                except Exception as e:  # noqa: BLE001 — one bad row mustn't kill the run
                    logger.warning("[backfill] item %s failed: %s", item.id, e)
                    stats["failed"] += 1
                    try:
                        await db.rollback()
                    except Exception:
                        pass

                await _safe_emit(
                    sse_hub,
                    "backfill_progress",
                    {
                        "project_id": project_id,
                        "phase": "step",
                        "item_id": item.id,
                        **stats,
                    },
                )

                if rate_limit_seconds > 0:
                    await asyncio.sleep(rate_limit_seconds)

            await _safe_emit(
                sse_hub,
                "backfill_progress",
                {"project_id": project_id, "phase": "done", **stats},
            )
    except Exception as e:  # pragma: no cover — task must never raise
        logger.exception("[backfill] task crashed for project %s", project_id)
        await _safe_emit(
            sse_hub if "sse_hub" in dir() else None,
            "backfill_progress",
            {
                "project_id": project_id,
                "phase": "error",
                "error": str(e)[:200],
            },
        )


def _read_brain_flags() -> tuple[bool, bool]:
    """Return ``(contextual_enabled, embedding_enabled)`` defensively."""
    try:
        from config import settings

        return (
            bool(getattr(settings, "brain_contextual_retrieval_enabled", False)),
            bool(getattr(settings, "brain_embedding_enabled", False)),
        )
    except Exception:  # pragma: no cover
        return (False, False)


async def _safe_emit(sse_hub: object, event: str, payload: dict) -> None:
    """SSE emit that never crashes the backfill task — broken hub silently ignored."""
    if sse_hub is None:
        return
    try:
        await sse_hub.emit(event, payload)  # type: ignore[attr-defined]
    except Exception:
        pass


# --- Helper: Auto-link by tags (existing) ---

async def _auto_link_by_tags(db: AsyncSession, project_id: str, new_item: KnowledgeItem):
    """Create RELATED edges to existing items with overlapping tags."""
    new_tags = set(json.loads(new_item.tags)) if new_item.tags else set()
    if not new_tags:
        return

    existing = await db.execute(
        select(KnowledgeItem).where(
            KnowledgeItem.project_id == project_id,
            KnowledgeItem.id != new_item.id,
        ).limit(50)
    )
    for other in existing.scalars().all():
        other_tags = set(json.loads(other.tags)) if other.tags else set()
        overlap = new_tags & other_tags
        if len(overlap) >= 2:
            edge = KnowledgeEdge(
                id=_gen_id(),
                source_item_id=new_item.id,
                target_item_id=other.id,
                edge_type="related",
                label=f"Tags: {', '.join(list(overlap)[:3])}",
            )
            db.add(edge)

    await db.commit()


def _extract_tags_from_text(text_input: str, max_tags: int = 5) -> list[str]:
    """Extract simple keyword tags from text."""
    # Remove common German stop words and extract meaningful words
    stop_words = {
        "und", "oder", "der", "die", "das", "ein", "eine", "ist", "sind", "wird",
        "werden", "hat", "haben", "mit", "von", "für", "auf", "aus", "bei", "nach",
        "über", "unter", "durch", "nicht", "auch", "als", "wie", "nur", "noch",
        "aber", "wenn", "dann", "diese", "dieser", "dieses", "kann", "muss",
        "soll", "alle", "den", "dem", "des", "sich", "einer", "einem", "einen",
    }
    words = re.findall(r'\b\w{4,}\b', text_input.lower())
    # Count frequency
    freq: dict[str, int] = {}
    for w in words:
        if w not in stop_words and not w.isdigit():
            freq[w] = freq.get(w, 0) + 1

    # Return top N by frequency
    sorted_words = sorted(freq.items(), key=lambda x: x[1], reverse=True)
    return [w for w, _ in sorted_words[:max_tags]]


# --- FTS5 Helpers ---

async def _fts_insert(db: AsyncSession, item: KnowledgeItem) -> bool:
    """Insert item into FTS5 index. Returns True on success, False on failure (logged).

    T2.5: the indexed ``content_plain`` column on the FTS5 virtual table now
    receives ``context_summary || '\\n\\n' || content_plain`` so that the
    LLM-generated context snippet contributes to BM25 ranking — matches
    the Anthropic-Contextual-Retrieval prescription without requiring a
    DROP+REBUILD of the FTS5 table schema. The raw ``content_plain`` on
    ``knowledge_items`` stays untouched so the UI shows what the user typed.
    """
    try:
        # Get rowid
        result = await db.execute(
            text("SELECT rowid FROM knowledge_items WHERE id = :id"),
            {"id": item.id}
        )
        row = result.fetchone()
        if row:
            tags_text = " ".join(json.loads(item.tags)) if item.tags else ""
            ctx = (item.context_summary or "").strip()
            body = item.content_plain or ""
            fts_content_plain = f"{ctx}\n\n{body}" if ctx else body
            await db.execute(text("""
                INSERT OR REPLACE INTO knowledge_items_fts(rowid, title, content_plain, tags)
                VALUES (:rowid, :title, :content_plain, :tags)
            """), {
                "rowid": row[0],
                "title": item.title,
                "content_plain": fts_content_plain,
                "tags": tags_text,
            })
            await db.commit()
            return True
        return False
    except Exception as e:
        logger.warning("FTS insert failed for item %s: %s", item.id, e)
        return False


async def _fts_update(db: AsyncSession, item: KnowledgeItem) -> bool:
    """Update item in FTS5 index. Returns True on success."""
    return await _fts_insert(db, item)  # INSERT OR REPLACE handles update


async def _fts_delete(db: AsyncSession, item_id: str):
    """Delete item from FTS5 contentless index using INSERT with delete command."""
    try:
        # For contentless FTS5, we need to use the special delete command
        result = await db.execute(
            text("SELECT rowid, title, content_plain, tags FROM knowledge_items WHERE id = :id"),
            {"id": item_id}
        )
        row = result.fetchone()
        if row:
            tags_text = " ".join(json.loads(row[3])) if row[3] else ""
            await db.execute(text("""
                INSERT INTO knowledge_items_fts(knowledge_items_fts, rowid, title, content_plain, tags)
                VALUES ('delete', :rowid, :title, :content_plain, :tags)
            """), {
                "rowid": row[0],
                "title": row[1],
                "content_plain": row[2],
                "tags": tags_text,
            })
    except Exception as e:
        logger.warning("FTS delete failed: %s", e)


def _sanitize_fts_query(query: str) -> str:
    """Sanitize user query for FTS5 MATCH."""
    # Remove FTS operators
    reserved = {"AND", "OR", "NOT", "NEAR"}
    terms = []
    for word in query.split():
        cleaned = re.sub(r"[^\w\u00e4\u00f6\u00fc\u00c4\u00d6\u00dc\u00df]", "", word)
        if cleaned and len(cleaned) >= 2 and cleaned.upper() not in reserved:
            terms.append(f'"{cleaned}"')  # Quote each term for exact matching

    if not terms:
        return ""

    return " OR ".join(terms)


def _create_snippet(content_plain: str, query: str, max_len: int = 200) -> str:
    """Create a text snippet around the first match."""
    lower = content_plain.lower()
    query_lower = query.lower().split()[0] if query else ""

    pos = lower.find(query_lower) if query_lower else -1
    if pos == -1:
        return content_plain[:max_len] + ("..." if len(content_plain) > max_len else "")

    start = max(0, pos - 60)
    end = min(len(content_plain), pos + max_len - 60)
    snippet = content_plain[start:end]
    if start > 0:
        snippet = "..." + snippet
    if end < len(content_plain):
        snippet = snippet + "..."
    return snippet


async def _fallback_search(
    db: AsyncSession, project_id: str, q: str, limit: int
) -> list[SearchResultResponse]:
    """LIKE-based fallback when FTS5 is not available."""
    pattern = f"%{q}%"
    result = await db.execute(
        select(KnowledgeItem).where(
            KnowledgeItem.project_id == project_id,
            or_(
                KnowledgeItem.title.ilike(pattern),
                KnowledgeItem.content_plain.ilike(pattern),
                KnowledgeItem.tags.ilike(pattern),
            ),
        ).limit(limit)
    )
    items = result.scalars().all()
    return [
        SearchResultResponse(
            item=_item_to_response(item),
            snippet=_create_snippet(item.content_plain, q),
            rank=0,
        )
        for item in items
    ]
