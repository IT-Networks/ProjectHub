import json
import secrets
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.todo import Todo, TodoQueue

router = APIRouter(prefix="/api/todo-queue", tags=["todo-queue"])


def _gen_id() -> str:
    return secrets.token_hex(8)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- Schemas ---

class QueueItemResponse(BaseModel):
    id: str
    suggested_title: str
    suggested_description: str
    suggested_priority: str
    suggested_deadline: str | None
    suggested_project_id: str | None
    source: str
    source_ref: str
    source_subject: str
    source_sender: str
    source_date: str
    ai_analysis: str
    ai_confidence: float
    queue_status: str
    reviewed_at: str | None
    created_at: str


class QueueItemUpdate(BaseModel):
    suggested_title: str | None = None
    suggested_description: str | None = None
    suggested_priority: str | None = None
    suggested_deadline: str | None = None
    suggested_project_id: str | None = None


class AcceptBody(BaseModel):
    project_id: str | None = None


def _to_response(q: TodoQueue) -> QueueItemResponse:
    return QueueItemResponse(
        id=q.id,
        suggested_title=q.suggested_title,
        suggested_description=q.suggested_description,
        suggested_priority=q.suggested_priority,
        suggested_deadline=q.suggested_deadline,
        suggested_project_id=q.suggested_project_id,
        source=q.source,
        source_ref=q.source_ref,
        source_subject=q.source_subject,
        source_sender=q.source_sender,
        source_date=q.source_date,
        ai_analysis=q.ai_analysis,
        ai_confidence=q.ai_confidence,
        queue_status=q.queue_status,
        reviewed_at=q.reviewed_at,
        created_at=q.created_at,
    )


# --- Routes ---

@router.get("")
async def list_queue(
    queue_status: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> list[QueueItemResponse]:
    stmt = select(TodoQueue)
    if queue_status:
        stmt = stmt.where(TodoQueue.queue_status == queue_status)
    stmt = stmt.order_by(TodoQueue.created_at.desc())

    result = await db.execute(stmt)
    return [_to_response(q) for q in result.scalars().all()]


@router.get("/stats")
async def queue_stats(db: AsyncSession = Depends(get_db)):
    pending = await db.scalar(
        select(func.count()).where(TodoQueue.queue_status == "pending")
    )
    accepted = await db.scalar(
        select(func.count()).where(TodoQueue.queue_status == "accepted")
    )
    rejected = await db.scalar(
        select(func.count()).where(TodoQueue.queue_status == "rejected")
    )
    return {
        "pending": pending or 0,
        "accepted": accepted or 0,
        "rejected": rejected or 0,
    }


@router.put("/{item_id}")
async def update_queue_item(
    item_id: str, data: QueueItemUpdate, db: AsyncSession = Depends(get_db)
) -> QueueItemResponse:
    result = await db.execute(select(TodoQueue).where(TodoQueue.id == item_id))
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(404, "Queue-Item nicht gefunden")
    if item.queue_status != "pending":
        raise HTTPException(400, "Nur ausstehende Items können bearbeitet werden")

    if data.suggested_title is not None:
        item.suggested_title = data.suggested_title
    if data.suggested_description is not None:
        item.suggested_description = data.suggested_description
    if data.suggested_priority is not None:
        item.suggested_priority = data.suggested_priority
    if data.suggested_deadline is not None:
        item.suggested_deadline = data.suggested_deadline
    if data.suggested_project_id is not None:
        item.suggested_project_id = data.suggested_project_id

    await db.commit()
    await db.refresh(item)
    return _to_response(item)


@router.post("/{item_id}/accept")
async def accept_queue_item(
    item_id: str, body: AcceptBody | None = None, db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(TodoQueue).where(TodoQueue.id == item_id))
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(404, "Queue-Item nicht gefunden")
    if item.queue_status != "pending":
        raise HTTPException(400, "Item ist nicht ausstehend")

    # Determine project_id
    project_id = None
    if body and body.project_id:
        project_id = body.project_id
    elif item.suggested_project_id:
        project_id = item.suggested_project_id

    # Create real Todo
    max_order = await db.scalar(
        select(func.max(Todo.kanban_order)).where(Todo.status == "backlog")
    )
    todo = Todo(
        id=_gen_id(),
        title=item.suggested_title,
        description=item.suggested_description,
        project_id=project_id,
        status="backlog",
        priority=item.suggested_priority,
        deadline=item.suggested_deadline,
        kanban_order=(max_order or 0) + 1,
        source=item.source,
        source_ref=item.source_ref,
        ai_analysis=item.ai_analysis,
    )
    db.add(todo)

    # Mark queue item as accepted
    item.queue_status = "accepted"
    item.reviewed_at = _now()

    await db.commit()

    return {
        "success": True,
        "todo_id": todo.id,
        "title": todo.title,
        "project_id": todo.project_id,
    }


@router.post("/{item_id}/reject")
async def reject_queue_item(item_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(TodoQueue).where(TodoQueue.id == item_id))
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(404, "Queue-Item nicht gefunden")
    if item.queue_status != "pending":
        raise HTTPException(400, "Item ist nicht ausstehend")

    item.queue_status = "rejected"
    item.reviewed_at = _now()
    await db.commit()
    return {"success": True}
