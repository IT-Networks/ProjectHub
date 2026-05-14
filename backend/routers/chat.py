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
from models.synapse import Synapse
from services.ai_assist_client import ai_assist

router = APIRouter(prefix="/api/chat", tags=["chat"])
logger = logging.getLogger("projecthub.chat")


def _gen_id() -> str:
    return secrets.token_hex(8)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _ensure_project(db: AsyncSession, project_id: str) -> Project:
    """Stellt sicher, dass das Projekt existiert — sonst 404.

    Konsistent mit ``knowledge.py``: Research-Endpoints validierten das
    Projekt bisher nicht, ``list_research`` für ein gelöschtes Projekt gab
    still ``[]`` zurück statt 404.
    """
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(404, "Projekt nicht gefunden")
    return project


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

    # Project knowledge — prefer the synthesised "Synapsen" layer (validated,
    # higher-order insight nodes with a confidence score). Fall back to the
    # flat KnowledgeItems only when no synapses have been generated yet.
    synapse_result = await db.execute(
        select(Synapse).where(
            Synapse.project_id == project_id,
            Synapse.status == "validated",
            Synapse.verdict.in_(["persist", "persist_flagged"]),
        ).order_by(Synapse.confidence.desc()).limit(8)
    )
    synapses = synapse_result.scalars().all()
    if synapses:
        parts.append("\n## Synthetisiertes Projektwissen")
        for syn in synapses:
            flag = "" if syn.verdict == "persist" else " (ungeprüft)"
            parts.append(f"- {syn.title} — Konfidenz {syn.confidence:.0%}{flag}")
            if syn.summary_plain:
                parts.append(f"  {syn.summary_plain[:400]}")
    else:
        # Fallback: flat knowledge items (top relevant, pinned first)
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
    """Chat with LLM in project context. Returns SSE stream.

    Output format is intentionally legacy-shaped (anonymous ``data:``
    lines with ``{token, done, full_response}``) to keep
    ``frontend/src/components/chat/ProjectChat.tsx`` working unchanged.
    Internally we now talk v2: ``ai_assist.agent_stream`` produces typed
    events that we translate here.
    """
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

    async def generate():
        fragments: list[str] = []
        pending_error: str | None = None
        confirm_code: str | None = None

        async for event in ai_assist.agent_stream(
            session_id=session_id,
            message=full_message,
            model=data.model,
        ):
            etype = event.get("type")
            payload = event.get("data")

            if etype == "token" and isinstance(payload, str) and payload:
                fragments.append(payload)
                yield f"data: {json.dumps({'token': payload})}\n\n"
                continue

            if etype == "error":
                err = payload.get("error") if isinstance(payload, dict) else str(payload)
                pending_error = err or "unbekannter Fehler"
                if isinstance(payload, dict):
                    confirm_code = payload.get("code")
                continue
            if etype == "cancelled":
                pending_error = "Abgebrochen"
                continue
            if etype == "max_iterations":
                pending_error = (
                    "Maximale Iterationen erreicht — Antwort ggf. unvollständig"
                )
                continue
            if etype == "confirm_required":
                # The engine asked for confirmation of a write-tool. We
                # have no UI for it in ProjectHub-chat — surface as error
                # so the user knows the session needs a reset.
                pending_error = (
                    "Bestätigung erforderlich — Session bitte zurücksetzen"
                )
                continue

            if etype == "done":
                # Terminal: produce ONE final SSE frame that the frontend
                # parser folds into the bubble. Frontend logic
                # (ProjectChat.tsx:78-87) replaces fullText when it sees
                # ``data.error`` OR ``data.done && data.full_response`` —
                # so we never emit both in the same stream.
                full = "".join(fragments)
                if pending_error and not full:
                    msg = {"error": pending_error}
                    if confirm_code:
                        msg["code"] = confirm_code
                    yield f"data: {json.dumps(msg)}\n\n"
                elif pending_error:
                    # Preserve streamed text; append the warning so users
                    # don't lose partial output to an overwriting error.
                    full_with_warning = full + f"\n\n[{pending_error}]"
                    yield (
                        "data: "
                        + json.dumps({"done": True, "full_response": full_with_warning})
                        + "\n\n"
                    )
                else:
                    yield (
                        "data: "
                        + json.dumps({"done": True, "full_response": full})
                        + "\n\n"
                    )
                return

    return EventSourceResponse(generate())


@router.get("/history/{session_id}")
async def chat_history(session_id: str):
    """Get chat history from AI-Assist v2.

    Frontend expects ``{history: [...]}``; v2 returns
    ``{messages: [...]}`` — adapt the field name here.
    """
    data = await ai_assist.get_session_history(session_id)
    if data is None:
        return {"session_id": session_id, "history": []}
    messages = data.get("messages") if isinstance(data, dict) else None
    return {
        "session_id": session_id,
        "history": messages or [],
    }


@router.post("/research/{project_id}")
async def start_research(
    project_id: str, data: ResearchRequest, db: AsyncSession = Depends(get_db)
):
    """Start a research query and save results."""
    await _ensure_project(db, project_id)

    if not ai_assist.is_connected:
        await ai_assist.health_check()
    if not ai_assist.is_connected:
        raise HTTPException(503, "AI-Assist nicht erreichbar")

    # Build project context for the research query
    context = await _build_project_context(project_id, db)
    full_query = f"[Projektkontext]\n{context}\n\n[Recherche-Frage]\n{data.query}" if context else data.query

    session_id = f"projecthub-research-{_gen_id()}"

    result_data = await ai_assist.agent_call(
        session_id=session_id,
        message=full_query,
    )

    if not result_data:
        # Consistent with builds.py / pulls.py: 503 when AI-Assist is unreachable
        raise HTTPException(503, "AI-Assist nicht erreichbar oder lieferte leere Antwort")

    response_text = result_data.get("response") or ""
    error_msg = result_data.get("error")
    model_used = result_data.get("model", "")

    if not response_text:
        # agent_call lieferte keinen Text — den echten Fehler durchreichen
        # statt einer generischen "leere Antwort"-Meldung.
        detail = error_msg or "AI-Assist lieferte eine leere Antwort"
        raise HTTPException(502, f"Recherche fehlgeschlagen: {detail}")

    if error_msg:
        # Teilausgabe + Fehler (z.B. max_iterations oder Tool-Fehler mitten im
        # Lauf): nicht still als Erfolg speichern — die Warnung im gespeicherten
        # Ergebnis sichtbar machen, damit der User das Resultat einordnen kann.
        response_text = (
            f"> ⚠️ **Hinweis:** Die Recherche wurde nicht sauber abgeschlossen "
            f"({error_msg}). Das Ergebnis ist möglicherweise unvollständig.\n\n"
            f"{response_text}"
        )

    # Save to database
    research = ResearchResult(
        id=_gen_id(),
        project_id=project_id,
        query=data.query,
        result=response_text,
        model_used=model_used,
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
    """List research results — Metadaten OHNE den vollen ``result``-Text.

    Der ``result`` (LLM-Output, oft mehrere KB) wird bewusst nicht
    mitgeladen: die Liste zeigt nur Query/Datum/Team. Den Volltext holt das
    Frontend lazy via ``GET /research/{project_id}/{research_id}``, erst
    wenn ein Eintrag aufgeklappt wird. Es werden nur die fünf benötigten
    Spalten selektiert — die große TEXT-Spalte verlässt die DB gar nicht.
    """
    await _ensure_project(db, project_id)

    result = await db.execute(
        select(
            ResearchResult.id,
            ResearchResult.query,
            ResearchResult.model_used,
            ResearchResult.agent_team,
            ResearchResult.created_at,
        )
        .where(ResearchResult.project_id == project_id)
        .order_by(ResearchResult.created_at.desc())
    )
    return [
        {
            "id": r_id,
            "query": query,
            "model_used": model_used,
            "agent_team": agent_team,
            "created_at": created_at,
        }
        for r_id, query, model_used, agent_team, created_at in result.all()
    ]


@router.get("/research/{project_id}/{research_id}")
async def get_research(
    project_id: str, research_id: str, db: AsyncSession = Depends(get_db)
):
    """Single research result inkl. vollem ``result``-Text (Lazy-Load-Ziel).

    Gegenstück zu ``list_research``: liefert genau den ``result``-Text, den
    die Listen-Antwort aus Performance-Gründen weglässt.
    """
    await _ensure_project(db, project_id)

    result = await db.execute(
        select(ResearchResult).where(
            ResearchResult.id == research_id,
            ResearchResult.project_id == project_id,
        )
    )
    research = result.scalar_one_or_none()
    if not research:
        raise HTTPException(404, "Recherche nicht gefunden")
    return {
        "id": research.id,
        "query": research.query,
        "result": research.result,
        "model_used": research.model_used,
        "agent_team": research.agent_team,
        "created_at": research.created_at,
    }


@router.delete("/research/{project_id}/{research_id}")
async def delete_research(
    project_id: str, research_id: str, db: AsyncSession = Depends(get_db)
):
    """Delete a single research result.

    Pro-Eintrag-Löschen — bisher konnte man Recherchen nur über das
    Löschen des gesamten Projekts (FK ``ondelete=CASCADE``) entfernen.
    Ein bereits nach Knowledge importierter Eintrag bleibt unberührt:
    der ``KnowledgeItem`` ist eine eigenständige Kopie des Inhalts.
    """
    await _ensure_project(db, project_id)

    result = await db.execute(
        select(ResearchResult).where(
            ResearchResult.id == research_id,
            ResearchResult.project_id == project_id,
        )
    )
    research = result.scalar_one_or_none()
    if not research:
        raise HTTPException(404, "Recherche nicht gefunden")

    await db.delete(research)
    await db.commit()
    return {"success": True}
