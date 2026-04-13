import json
import secrets
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from database import get_db
from models.project import Project, DataSourceLink
from models.todo import Todo
from models.note import Note
from models.research import ResearchResult
from models.knowledge import KnowledgeItem
from services.ai_assist_client import ai_assist

router = APIRouter(prefix="/api/chat", tags=["chat"])
logger = logging.getLogger("projecthub.chat")


def _gen_id() -> str:
    return secrets.token_hex(8)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- Schemas ---

class ProjectChatRequest(BaseModel):
    message: str
    model: str | None = None
    include_sources: bool = True


class ResearchRequest(BaseModel):
    query: str
    team: str | None = None


# --- Context Builder ---

async def _build_project_context(project_id: str, db: AsyncSession) -> str:
    """Build context string from all linked project data sources."""
    parts = []

    # Project info
    result = await db.execute(
        select(Project).where(Project.id == project_id)
    )
    project = result.scalar_one_or_none()
    if not project:
        return ""

    parts.append(f"## Projekt: {project.name}")
    if project.description:
        parts.append(f"Beschreibung: {project.description}")
    parts.append(f"Status: {project.status}")

    # Active todos
    result = await db.execute(
        select(Todo).where(
            Todo.project_id == project_id,
            Todo.status != "done",
        ).order_by(Todo.kanban_order).limit(10)
    )
    todos = result.scalars().all()
    if todos:
        parts.append("\n## Offene Todos")
        for t in todos:
            deadline = f" (Frist: {t.deadline})" if t.deadline else ""
            parts.append(f"- [{t.status}] {t.title}{deadline}")

    # Pinned notes
    result = await db.execute(
        select(Note).where(
            Note.project_id == project_id,
            Note.is_pinned == 1,
        ).limit(5)
    )
    notes = result.scalars().all()
    if notes:
        parts.append("\n## Gepinnte Notizen")
        for n in notes:
            parts.append(f"### {n.title or 'Notiz'}")
            # Strip HTML for context
            content = n.content.replace("<p>", "").replace("</p>", "\n").replace("<br>", "\n")
            import re
            content = re.sub(r"<[^>]+>", "", content)[:500]
            parts.append(content)

    # Data source links
    result = await db.execute(
        select(DataSourceLink).where(DataSourceLink.project_id == project_id)
    )
    sources = result.scalars().all()
    if sources:
        parts.append("\n## Verknüpfte Datenquellen")
        for s in sources:
            config = json.loads(s.source_config) if s.source_config else {}
            parts.append(f"- {s.source_type}: {s.display_name or json.dumps(config)}")

    # Cached build status
    from models.cache import OfflineCache
    for s in sources:
        if s.source_type == "jenkins_job":
            config = json.loads(s.source_config) if s.source_config else {}
            path_name = config.get("path_name", "default")
            cache_result = await db.execute(
                select(OfflineCache).where(OfflineCache.cache_key == f"jenkins:jobs:{path_name}")
            )
            cache = cache_result.scalar_one_or_none()
            if cache:
                try:
                    data = json.loads(cache.data)
                    if "jobs" in data:
                        parts.append(f"\n## Jenkins Build-Status ({path_name})")
                        for job in data["jobs"][:5]:
                            lb = job.get("lastBuild") or {}
                            parts.append(f"- {job['name']}: {job.get('color', '?')} (Build #{lb.get('number', '?')})")
                except Exception:
                    pass

    # Knowledge items (top relevant, pinned first)
    ki_result = await db.execute(
        select(KnowledgeItem).where(
            KnowledgeItem.project_id == project_id,
        ).order_by(
            KnowledgeItem.is_pinned.desc(),
            KnowledgeItem.updated_at.desc(),
        ).limit(10)
    )
    knowledge_items = ki_result.scalars().all()
    if knowledge_items:
        parts.append("\n## Projektwissen")
        for ki in knowledge_items:
            pin = " 📌" if ki.is_pinned else ""
            tags = json.loads(ki.tags) if ki.tags else []
            tag_str = f" [{', '.join(tags)}]" if tags else ""
            parts.append(f"- [{ki.category}]{pin} {ki.title}{tag_str}")
            if ki.content_plain:
                parts.append(f"  {ki.content_plain[:300]}")

    return "\n".join(parts)


# --- Routes ---

@router.post("/project/{project_id}")
async def project_chat(
    project_id: str, data: ProjectChatRequest, db: AsyncSession = Depends(get_db)
):
    """Chat with LLM in project context. Returns SSE stream."""
    if not ai_assist.is_connected:
        await ai_assist.health_check()

    # Build context
    context = ""
    if data.include_sources:
        context = await _build_project_context(project_id, db)

    session_id = f"projecthub-{project_id}"

    # Prepend context to message
    full_message = data.message
    if context:
        full_message = f"[Projektkontext]\n{context}\n\n[Frage]\n{data.message}"

    body = {
        "session_id": session_id,
        "message": full_message,
        "stream": True,
    }
    if data.model:
        body["model"] = data.model

    async def generate():
        async for chunk in ai_assist.stream_post("/api/chat/stream", body):
            yield chunk

    return EventSourceResponse(generate())


@router.get("/history/{session_id}")
async def chat_history(session_id: str):
    """Get chat history from AI-Assist."""
    data = await ai_assist.get(f"/api/chat/{session_id}/history")
    if data is None:
        return {"session_id": session_id, "history": []}
    return data


@router.post("/research/{project_id}")
async def start_research(
    project_id: str, data: ResearchRequest, db: AsyncSession = Depends(get_db)
):
    """Start a research query and save results."""
    if not ai_assist.is_connected:
        await ai_assist.health_check()
    if not ai_assist.is_connected:
        raise HTTPException(503, "AI-Assist nicht erreichbar")

    # Build project context for the research query
    context = await _build_project_context(project_id, db)
    full_query = f"[Projektkontext]\n{context}\n\n[Recherche-Frage]\n{data.query}" if context else data.query

    session_id = f"projecthub-research-{_gen_id()}"

    # Use regular chat (non-streaming) for research
    result_data = await ai_assist.post("/api/chat", {
        "session_id": session_id,
        "message": full_query,
    })

    response_text = ""
    if result_data and "response" in result_data:
        response_text = result_data["response"]
    elif result_data:
        response_text = str(result_data)

    # Save to database
    research = ResearchResult(
        id=_gen_id(),
        project_id=project_id,
        query=data.query,
        result=response_text,
        model_used="",
        agent_team=data.team or "",
        session_id=session_id,
    )
    db.add(research)
    await db.commit()
    await db.refresh(research)

    return {
        "id": research.id,
        "query": research.query,
        "result": research.result,
        "created_at": research.created_at,
    }


@router.get("/research/{project_id}")
async def list_research(project_id: str, db: AsyncSession = Depends(get_db)):
    """List all research results for a project."""
    result = await db.execute(
        select(ResearchResult)
        .where(ResearchResult.project_id == project_id)
        .order_by(ResearchResult.created_at.desc())
    )
    items = result.scalars().all()
    return [
        {
            "id": r.id,
            "query": r.query,
            "result": r.result,
            "model_used": r.model_used,
            "agent_team": r.agent_team,
            "created_at": r.created_at,
        }
        for r in items
    ]
