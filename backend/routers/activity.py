from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, union_all, literal, desc
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.todo import Todo
from models.note import Note
from models.research import ResearchResult

router = APIRouter(prefix="/api/activity", tags=["activity"])


@router.get("")
async def get_activity(
    project_id: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Get a combined activity feed from todos, notes, and research results."""
    activities = []

    # Recent todos (created or updated)
    stmt = select(Todo)
    if project_id:
        stmt = stmt.where(Todo.project_id == project_id)
    stmt = stmt.order_by(desc(Todo.updated_at)).limit(limit)
    result = await db.execute(stmt)
    for t in result.scalars().all():
        is_new = t.created_at == t.updated_at
        activities.append({
            "type": "todo",
            "action": "erstellt" if is_new else "aktualisiert",
            "id": t.id,
            "title": t.title,
            "status": t.status,
            "priority": t.priority,
            "project_id": t.project_id,
            "timestamp": t.updated_at,
        })

    # Recent notes
    stmt = select(Note)
    if project_id:
        stmt = stmt.where(Note.project_id == project_id)
    stmt = stmt.order_by(desc(Note.updated_at)).limit(limit)
    result = await db.execute(stmt)
    for n in result.scalars().all():
        is_new = n.created_at == n.updated_at
        activities.append({
            "type": "note",
            "action": "erstellt" if is_new else "bearbeitet",
            "id": n.id,
            "title": n.title or "Notiz",
            "project_id": n.project_id,
            "timestamp": n.updated_at,
        })

    # Recent research
    stmt = select(ResearchResult)
    if project_id:
        stmt = stmt.where(ResearchResult.project_id == project_id)
    stmt = stmt.order_by(desc(ResearchResult.created_at)).limit(limit)
    result = await db.execute(stmt)
    for r in result.scalars().all():
        activities.append({
            "type": "research",
            "action": "recherchiert",
            "id": r.id,
            "title": r.query[:100],
            "project_id": r.project_id,
            "timestamp": r.created_at,
        })

    # Sort by timestamp descending, limit
    activities.sort(key=lambda a: a["timestamp"], reverse=True)
    return {"activities": activities[:limit]}
