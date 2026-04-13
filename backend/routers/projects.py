import json
import secrets
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_db
from models.project import Project, DataSourceLink
from models.todo import Todo
from models.note import Note
from models.research import ResearchResult

router = APIRouter(prefix="/api/projects", tags=["projects"])


def _gen_id() -> str:
    return secrets.token_hex(8)


# --- Pydantic Schemas ---

class ProjectCreate(BaseModel):
    name: str
    description: str = ""
    status: str = "aktiv"
    color: str = "#6366f1"
    tags: list[str] = []
    docs_path: str | None = None


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    status: str | None = None
    color: str | None = None
    tags: list[str] | None = None
    sort_order: int | None = None
    docs_path: str | None = None


class DataSourceLinkCreate(BaseModel):
    source_type: str
    source_config: dict = {}
    display_name: str = ""


class ProjectResponse(BaseModel):
    id: str
    name: str
    description: str
    status: str
    color: str
    tags: list[str]
    sort_order: int
    docs_path: str | None
    sources: list[dict]
    counts: dict
    created_at: str
    updated_at: str


class ProjectListItem(BaseModel):
    id: str
    name: str
    description: str
    status: str
    color: str
    tags: list[str]
    sort_order: int
    docs_path: str | None
    source_count: int
    todo_open: int
    created_at: str
    updated_at: str


# --- Helpers ---

def _source_to_dict(s: DataSourceLink) -> dict:
    return {
        "id": s.id,
        "source_type": s.source_type,
        "source_config": json.loads(s.source_config) if s.source_config else {},
        "display_name": s.display_name,
        "created_at": s.created_at,
    }


async def _get_project_counts(db: AsyncSession, project_id: str) -> dict:
    todos_open = await db.scalar(
        select(func.count()).where(Todo.project_id == project_id, Todo.status != "done")
    )
    todos_done = await db.scalar(
        select(func.count()).where(Todo.project_id == project_id, Todo.status == "done")
    )
    notes = await db.scalar(
        select(func.count()).where(Note.project_id == project_id)
    )
    research = await db.scalar(
        select(func.count()).where(ResearchResult.project_id == project_id)
    )
    return {
        "todos_open": todos_open or 0,
        "todos_done": todos_done or 0,
        "notes": notes or 0,
        "research": research or 0,
    }


# --- Routes ---

@router.get("")
async def list_projects(db: AsyncSession = Depends(get_db)) -> list[ProjectListItem]:
    result = await db.execute(
        select(Project).options(selectinload(Project.sources)).order_by(Project.sort_order, Project.name)
    )
    projects = result.scalars().all()
    items = []
    for p in projects:
        todo_open = await db.scalar(
            select(func.count()).where(Todo.project_id == p.id, Todo.status != "done")
        )
        items.append(ProjectListItem(
            id=p.id,
            name=p.name,
            description=p.description,
            status=p.status,
            color=p.color,
            tags=json.loads(p.tags) if p.tags else [],
            sort_order=p.sort_order,
            docs_path=p.docs_path,
            source_count=len(p.sources),
            todo_open=todo_open or 0,
            created_at=p.created_at,
            updated_at=p.updated_at,
        ))
    return items


@router.get("/{project_id}")
async def get_project(project_id: str, db: AsyncSession = Depends(get_db)) -> ProjectResponse:
    result = await db.execute(
        select(Project).options(selectinload(Project.sources)).where(Project.id == project_id)
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Projekt nicht gefunden")

    counts = await _get_project_counts(db, project_id)
    return ProjectResponse(
        id=project.id,
        name=project.name,
        description=project.description,
        status=project.status,
        color=project.color,
        tags=json.loads(project.tags) if project.tags else [],
        sort_order=project.sort_order,
        docs_path=project.docs_path,
        sources=[_source_to_dict(s) for s in project.sources],
        counts=counts,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


@router.post("", status_code=201)
async def create_project(data: ProjectCreate, db: AsyncSession = Depends(get_db)) -> ProjectResponse:
    project = Project(
        id=_gen_id(),
        name=data.name,
        description=data.description,
        status=data.status,
        color=data.color,
        tags=json.dumps(data.tags),
        docs_path=data.docs_path,
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)
    return ProjectResponse(
        id=project.id,
        name=project.name,
        description=project.description,
        status=project.status,
        color=project.color,
        tags=data.tags,
        sort_order=project.sort_order,
        docs_path=project.docs_path,
        sources=[],
        counts={"todos_open": 0, "todos_done": 0, "notes": 0, "research": 0},
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


@router.put("/{project_id}")
async def update_project(
    project_id: str, data: ProjectUpdate, db: AsyncSession = Depends(get_db)
) -> ProjectResponse:
    result = await db.execute(
        select(Project).options(selectinload(Project.sources)).where(Project.id == project_id)
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Projekt nicht gefunden")

    if data.name is not None:
        project.name = data.name
    if data.description is not None:
        project.description = data.description
    if data.status is not None:
        project.status = data.status
    if data.color is not None:
        project.color = data.color
    if data.tags is not None:
        project.tags = json.dumps(data.tags)
    if data.sort_order is not None:
        project.sort_order = data.sort_order
    if data.docs_path is not None:
        project.docs_path = data.docs_path if data.docs_path else None
    project.updated_at = datetime.now(timezone.utc).isoformat()

    await db.commit()
    await db.refresh(project)

    counts = await _get_project_counts(db, project_id)
    return ProjectResponse(
        id=project.id,
        name=project.name,
        description=project.description,
        status=project.status,
        color=project.color,
        tags=json.loads(project.tags) if project.tags else [],
        sort_order=project.sort_order,
        docs_path=project.docs_path,
        sources=[_source_to_dict(s) for s in project.sources],
        counts=counts,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


@router.delete("/{project_id}")
async def delete_project(project_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Projekt nicht gefunden")

    await db.delete(project)
    await db.commit()
    return {"success": True}


# --- Data Source Links ---

@router.post("/{project_id}/sources", status_code=201)
async def add_source(
    project_id: str, data: DataSourceLinkCreate, db: AsyncSession = Depends(get_db)
) -> dict:
    result = await db.execute(select(Project).where(Project.id == project_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Projekt nicht gefunden")

    valid_types = {
        "jenkins_job", "github_repo", "git_repo",
        "jira_project", "confluence_space", "email_folder", "webex_room",
    }
    if data.source_type not in valid_types:
        raise HTTPException(status_code=400, detail=f"Ungültiger source_type. Erlaubt: {valid_types}")

    link = DataSourceLink(
        id=_gen_id(),
        project_id=project_id,
        source_type=data.source_type,
        source_config=json.dumps(data.source_config),
        display_name=data.display_name,
    )
    db.add(link)
    await db.commit()
    await db.refresh(link)
    return _source_to_dict(link)


@router.delete("/{project_id}/sources/{source_id}")
async def remove_source(
    project_id: str, source_id: str, db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(DataSourceLink).where(
            DataSourceLink.id == source_id, DataSourceLink.project_id == project_id
        )
    )
    link = result.scalar_one_or_none()
    if not link:
        raise HTTPException(status_code=404, detail="Datenquelle nicht gefunden")

    await db.delete(link)
    await db.commit()
    return {"success": True}
