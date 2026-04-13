import json
import secrets
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.todo import Todo

router = APIRouter(prefix="/api/todos", tags=["todos"])


def _gen_id() -> str:
    return secrets.token_hex(8)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- Schemas ---

class TodoCreate(BaseModel):
    title: str
    description: str = ""
    project_id: str | None = None
    status: str = "backlog"
    priority: str = "medium"
    deadline: str | None = None
    tags: list[str] = []


class TodoUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    project_id: str | None = None
    status: str | None = None
    priority: str | None = None
    deadline: str | None = None
    tags: list[str] | None = None


class TodoStatusUpdate(BaseModel):
    status: str
    kanban_order: int | None = None


class TodoOrderUpdate(BaseModel):
    kanban_order: int


class TodoResponse(BaseModel):
    id: str
    project_id: str | None
    title: str
    description: str
    status: str
    priority: str
    deadline: str | None
    kanban_order: int
    tags: list[str]
    source: str
    source_ref: str | None
    ai_analysis: str | None
    created_at: str
    updated_at: str


def _to_response(t: Todo) -> TodoResponse:
    return TodoResponse(
        id=t.id,
        project_id=t.project_id,
        title=t.title,
        description=t.description,
        status=t.status,
        priority=t.priority,
        deadline=t.deadline,
        kanban_order=t.kanban_order,
        tags=json.loads(t.tags) if t.tags else [],
        source=t.source,
        source_ref=t.source_ref,
        ai_analysis=t.ai_analysis,
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


# --- Routes ---

@router.get("")
async def list_todos(
    project_id: str | None = Query(None),
    status: str | None = Query(None),
    priority: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> list[TodoResponse]:
    stmt = select(Todo)
    if project_id is not None:
        stmt = stmt.where(Todo.project_id == project_id)
    if status is not None:
        stmt = stmt.where(Todo.status == status)
    if priority is not None:
        stmt = stmt.where(Todo.priority == priority)
    stmt = stmt.order_by(Todo.kanban_order, Todo.created_at.desc())

    result = await db.execute(stmt)
    return [_to_response(t) for t in result.scalars().all()]


@router.get("/{todo_id}")
async def get_todo(todo_id: str, db: AsyncSession = Depends(get_db)) -> TodoResponse:
    result = await db.execute(select(Todo).where(Todo.id == todo_id))
    todo = result.scalar_one_or_none()
    if not todo:
        raise HTTPException(404, "Todo nicht gefunden")
    return _to_response(todo)


@router.post("", status_code=201)
async def create_todo(data: TodoCreate, db: AsyncSession = Depends(get_db)) -> TodoResponse:
    # Get max kanban_order for the target status
    max_order = await db.scalar(
        select(func.max(Todo.kanban_order)).where(Todo.status == data.status)
    )
    todo = Todo(
        id=_gen_id(),
        title=data.title,
        description=data.description,
        project_id=data.project_id,
        status=data.status,
        priority=data.priority,
        deadline=data.deadline,
        kanban_order=(max_order or 0) + 1,
        tags=json.dumps(data.tags),
    )
    db.add(todo)
    await db.commit()
    await db.refresh(todo)
    return _to_response(todo)


@router.put("/{todo_id}")
async def update_todo(
    todo_id: str, data: TodoUpdate, db: AsyncSession = Depends(get_db)
) -> TodoResponse:
    result = await db.execute(select(Todo).where(Todo.id == todo_id))
    todo = result.scalar_one_or_none()
    if not todo:
        raise HTTPException(404, "Todo nicht gefunden")

    if data.title is not None:
        todo.title = data.title
    if data.description is not None:
        todo.description = data.description
    if data.project_id is not None:
        todo.project_id = data.project_id
    if data.status is not None:
        todo.status = data.status
    if data.priority is not None:
        todo.priority = data.priority
    if data.deadline is not None:
        todo.deadline = data.deadline
    if data.tags is not None:
        todo.tags = json.dumps(data.tags)
    todo.updated_at = _now()

    await db.commit()
    await db.refresh(todo)
    return _to_response(todo)


@router.delete("/{todo_id}")
async def delete_todo(todo_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Todo).where(Todo.id == todo_id))
    todo = result.scalar_one_or_none()
    if not todo:
        raise HTTPException(404, "Todo nicht gefunden")
    await db.delete(todo)
    await db.commit()
    return {"success": True}


@router.patch("/{todo_id}/status")
async def update_status(
    todo_id: str, data: TodoStatusUpdate, db: AsyncSession = Depends(get_db)
) -> TodoResponse:
    result = await db.execute(select(Todo).where(Todo.id == todo_id))
    todo = result.scalar_one_or_none()
    if not todo:
        raise HTTPException(404, "Todo nicht gefunden")

    valid = {"backlog", "in_progress", "review", "done"}
    if data.status not in valid:
        raise HTTPException(400, f"Ungültiger Status. Erlaubt: {valid}")

    todo.status = data.status
    if data.kanban_order is not None:
        todo.kanban_order = data.kanban_order
    todo.updated_at = _now()

    await db.commit()
    await db.refresh(todo)
    return _to_response(todo)


@router.patch("/{todo_id}/order")
async def update_order(
    todo_id: str, data: TodoOrderUpdate, db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(Todo).where(Todo.id == todo_id))
    todo = result.scalar_one_or_none()
    if not todo:
        raise HTTPException(404, "Todo nicht gefunden")

    todo.kanban_order = data.kanban_order
    todo.updated_at = _now()
    await db.commit()
    return {"success": True}
