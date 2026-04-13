import json
import secrets
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.note import Note

router = APIRouter(prefix="/api/notes", tags=["notes"])


def _gen_id() -> str:
    return secrets.token_hex(8)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- Schemas ---

class NoteCreate(BaseModel):
    project_id: str
    title: str = ""
    content: str = ""
    content_format: str = "tiptap"
    deadline: str | None = None
    tags: list[str] = []


class NoteUpdate(BaseModel):
    title: str | None = None
    content: str | None = None
    content_format: str | None = None
    deadline: str | None = None
    tags: list[str] | None = None


class NoteResponse(BaseModel):
    id: str
    project_id: str
    title: str
    content: str
    content_format: str
    deadline: str | None
    is_pinned: bool
    tags: list[str]
    sort_order: int
    created_at: str
    updated_at: str


def _to_response(n: Note) -> NoteResponse:
    return NoteResponse(
        id=n.id,
        project_id=n.project_id,
        title=n.title,
        content=n.content,
        content_format=n.content_format,
        deadline=n.deadline,
        is_pinned=bool(n.is_pinned),
        tags=json.loads(n.tags) if n.tags else [],
        sort_order=n.sort_order,
        created_at=n.created_at,
        updated_at=n.updated_at,
    )


# --- Routes ---

@router.get("")
async def list_notes(
    project_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> list[NoteResponse]:
    stmt = select(Note)
    if project_id is not None:
        stmt = stmt.where(Note.project_id == project_id)
    stmt = stmt.order_by(Note.is_pinned.desc(), Note.sort_order, Note.created_at.desc())

    result = await db.execute(stmt)
    return [_to_response(n) for n in result.scalars().all()]


@router.get("/{note_id}")
async def get_note(note_id: str, db: AsyncSession = Depends(get_db)) -> NoteResponse:
    result = await db.execute(select(Note).where(Note.id == note_id))
    note = result.scalar_one_or_none()
    if not note:
        raise HTTPException(404, "Notiz nicht gefunden")
    return _to_response(note)


@router.post("", status_code=201)
async def create_note(data: NoteCreate, db: AsyncSession = Depends(get_db)) -> NoteResponse:
    note = Note(
        id=_gen_id(),
        project_id=data.project_id,
        title=data.title,
        content=data.content,
        content_format=data.content_format,
        deadline=data.deadline,
        tags=json.dumps(data.tags),
    )
    db.add(note)
    await db.commit()
    await db.refresh(note)
    return _to_response(note)


@router.put("/{note_id}")
async def update_note(
    note_id: str, data: NoteUpdate, db: AsyncSession = Depends(get_db)
) -> NoteResponse:
    result = await db.execute(select(Note).where(Note.id == note_id))
    note = result.scalar_one_or_none()
    if not note:
        raise HTTPException(404, "Notiz nicht gefunden")

    if data.title is not None:
        note.title = data.title
    if data.content is not None:
        note.content = data.content
    if data.content_format is not None:
        note.content_format = data.content_format
    if data.deadline is not None:
        note.deadline = data.deadline
    if data.tags is not None:
        note.tags = json.dumps(data.tags)
    note.updated_at = _now()

    await db.commit()
    await db.refresh(note)
    return _to_response(note)


@router.delete("/{note_id}")
async def delete_note(note_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Note).where(Note.id == note_id))
    note = result.scalar_one_or_none()
    if not note:
        raise HTTPException(404, "Notiz nicht gefunden")
    await db.delete(note)
    await db.commit()
    return {"success": True}


@router.patch("/{note_id}/pin")
async def toggle_pin(note_id: str, db: AsyncSession = Depends(get_db)) -> NoteResponse:
    result = await db.execute(select(Note).where(Note.id == note_id))
    note = result.scalar_one_or_none()
    if not note:
        raise HTTPException(404, "Notiz nicht gefunden")

    note.is_pinned = 0 if note.is_pinned else 1
    note.updated_at = _now()
    await db.commit()
    await db.refresh(note)
    return _to_response(note)
