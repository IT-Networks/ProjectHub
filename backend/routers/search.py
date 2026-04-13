from fastapi import APIRouter, Query, Depends
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.project import Project
from models.todo import Todo
from models.note import Note
from models.communication import LinkedMessage
from models.knowledge import KnowledgeItem

router = APIRouter(prefix="/api/search", tags=["search"])


@router.get("")
async def global_search(q: str = Query("", min_length=1), db: AsyncSession = Depends(get_db)):
    """Full-text search across projects, todos, notes, and linked messages."""
    term = f"%{q}%"

    # Projects
    result = await db.execute(
        select(Project).where(
            or_(
                Project.name.ilike(term),
                Project.description.ilike(term),
                Project.tags.ilike(term),
            )
        ).limit(10)
    )
    projects = [
        {"id": p.id, "name": p.name, "match": p.description[:100] if q.lower() in (p.description or "").lower() else p.name, "type": "project"}
        for p in result.scalars().all()
    ]

    # Todos
    result = await db.execute(
        select(Todo).where(
            or_(
                Todo.title.ilike(term),
                Todo.description.ilike(term),
            )
        ).limit(10)
    )
    todos = [
        {"id": t.id, "title": t.title, "project_id": t.project_id, "status": t.status, "match": t.title, "type": "todo"}
        for t in result.scalars().all()
    ]

    # Notes
    result = await db.execute(
        select(Note).where(
            or_(
                Note.title.ilike(term),
                Note.content.ilike(term),
            )
        ).limit(10)
    )
    notes = [
        {"id": n.id, "title": n.title or "Notiz", "project_id": n.project_id, "match": n.title or "Notiz", "type": "note"}
        for n in result.scalars().all()
    ]

    # Linked messages
    result = await db.execute(
        select(LinkedMessage).where(
            or_(
                LinkedMessage.subject.ilike(term),
                LinkedMessage.sender.ilike(term),
                LinkedMessage.snippet.ilike(term),
            )
        ).limit(10)
    )
    messages = [
        {"id": m.id, "subject": m.subject, "sender": m.sender, "source": m.source, "match": m.subject, "type": "message"}
        for m in result.scalars().all()
    ]

    # Knowledge items
    result = await db.execute(
        select(KnowledgeItem).where(
            or_(
                KnowledgeItem.title.ilike(term),
                KnowledgeItem.content_plain.ilike(term),
                KnowledgeItem.tags.ilike(term),
            )
        ).limit(10)
    )
    knowledge = [
        {"id": k.id, "title": k.title, "project_id": k.project_id, "category": k.category, "match": k.title, "type": "knowledge"}
        for k in result.scalars().all()
    ]

    return {
        "query": q,
        "projects": projects,
        "todos": todos,
        "notes": notes,
        "messages": messages,
        "knowledge": knowledge,
        "total": len(projects) + len(todos) + len(notes) + len(messages) + len(knowledge),
    }
