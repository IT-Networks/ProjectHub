"""Dedicated sync endpoints for bidirectional note <-> knowledge sync."""

import json
import re
import secrets
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.knowledge import KnowledgeItem
from models.note import Note

router = APIRouter(prefix="/api/sync", tags=["sync"])


@router.get("/test")
async def test_sync() -> dict:
    """Test endpoint to verify sync router works."""
    return {"message": "sync router works"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _strip_html(html: str) -> str:
    """Strip HTML tags for plain text / FTS indexing."""
    text_content = re.sub(r"<[^>]+>", " ", html)
    text_content = re.sub(r"\s+", " ", text_content).strip()
    return text_content


class SyncNoteToKnowledgeRequest(BaseModel):
    project_id: str
    note_id: str
    content: str
    title: str


class SyncKnowledgeToNoteRequest(BaseModel):
    project_id: str
    item_id: str


@router.post("/note-to-knowledge")
async def sync_note_to_knowledge(
    data: SyncNoteToKnowledgeRequest, db: AsyncSession = Depends(get_db)
) -> dict:
    """Sync updated note content to all linked knowledge items."""
    result = await db.execute(
        select(KnowledgeItem).where(
            KnowledgeItem.project_id == data.project_id,
            KnowledgeItem.source_note_id == data.note_id,
        )
    )
    items = result.scalars().all()

    if not items:
        return {"synced_count": 0}

    for item in items:
        content_plain = _strip_html(data.content) if data.content else ""
        item.content = data.content
        item.title = data.title
        item.content_plain = content_plain[:5000]
        item.sync_status = "synced"
        item.last_synced_at = _now()
        # Note: FTS update moved to knowledge router for dependency isolation

    await db.commit()
    return {"synced_count": len(items)}


@router.post("/knowledge-to-note")
async def sync_knowledge_to_note(
    data: SyncKnowledgeToNoteRequest, db: AsyncSession = Depends(get_db)
) -> dict:
    """Sync knowledge item content back to source note."""
    result = await db.execute(
        select(KnowledgeItem).where(
            KnowledgeItem.id == data.item_id,
            KnowledgeItem.project_id == data.project_id,
        )
    )
    item = result.scalar_one_or_none()
    if not item or not item.source_note_id:
        raise HTTPException(400, "Wissenselemente hat keine verknüpfte Notiz")

    result = await db.execute(
        select(Note).where(
            Note.id == item.source_note_id,
            Note.project_id == data.project_id,
        )
    )
    note = result.scalar_one_or_none()
    if not note:
        raise HTTPException(404, "Verknüpfte Notiz nicht gefunden")

    note.title = item.title
    note.content = item.content
    note.updated_at = _now()

    item.sync_status = "synced"
    item.last_synced_at = _now()

    await db.commit()
    return {"note_id": note.id, "synced": True}
