"""Project-level sync pipeline (S1 scaffolding).

Endpoints:
  POST /api/projects/{id}/sync              → trigger run (non-blocking)
  GET  /api/projects/{id}/sync/status       → current + last run + per-source state
  GET  /api/projects/{id}/sync/changes      → staged changes (default: pending)

Actual source adapters (S2+) plug into `_run_sync_for_project` by dispatching
on `source_type`. For S1 the function only bookkeeps: it marks the run as
started, calls registered adapters (none yet), writes `SyncRun` + updates
`DataSourceLink.last_synced_at`, and emits an SSE event.
"""

import asyncio
import logging
import secrets
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from database import async_session, get_db
from models.project import Project, DataSourceLink
from models.source_change import SourceChange, SyncRun
from services.source_adapters import run_adapter, ADAPTERS
from services.change_analyzer import analyze_pending_changes, analyze_change, _promote_to_knowledge
from services.sse_hub import sse_hub

router = APIRouter(prefix="/api/projects", tags=["project-sync"])
logger = logging.getLogger("projecthub.project_sync")

# Minimum interval between auto-triggered syncs. Manual triggers bypass this.
AUTO_SYNC_COOLDOWN_SECONDS = 30 * 60  # 30 min — matches user decision


def _gen_id() -> str:
    return secrets.token_hex(8)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


# ---- Schemas ---------------------------------------------------------------

class SourceStatusResponse(BaseModel):
    id: str
    source_type: str
    display_name: str
    last_synced_at: str | None
    last_sync_status: str
    last_error_msg: str | None
    sync_enabled: bool


class SyncRunResponse(BaseModel):
    id: str
    started_at: str
    finished_at: str | None
    trigger: str
    status: str
    sources_synced: int
    sources_failed: int
    changes_detected: int
    error_summary: str | None


class SyncStatusResponse(BaseModel):
    running: bool
    last_run: SyncRunResponse | None
    pending_changes: int
    sources: list[SourceStatusResponse]


class SourceChangeResponse(BaseModel):
    id: str
    source_type: str
    external_ref: str
    title: str
    detected_at: str
    analysis_status: str
    analysis: dict | None
    auto_accepted: bool
    knowledge_item_id: str | None


class SyncTriggerRequest(BaseModel):
    trigger: Literal["manual", "auto_open", "periodic"] = "manual"
    force: bool = False  # if true, ignore cooldown


class SyncTriggerResponse(BaseModel):
    run_id: str | None
    started: bool
    reason: str  # "started", "cooldown", "already_running", "no_sources"


# ---- Helpers ---------------------------------------------------------------

async def _project_exists(db: AsyncSession, project_id: str) -> bool:
    res = await db.execute(select(Project.id).where(Project.id == project_id))
    return res.scalar_one_or_none() is not None


async def _get_running_run(db: AsyncSession, project_id: str) -> SyncRun | None:
    res = await db.execute(
        select(SyncRun)
        .where(SyncRun.project_id == project_id, SyncRun.status == "running")
        .order_by(SyncRun.started_at.desc())
        .limit(1)
    )
    return res.scalar_one_or_none()


async def _get_last_run(db: AsyncSession, project_id: str) -> SyncRun | None:
    res = await db.execute(
        select(SyncRun)
        .where(SyncRun.project_id == project_id)
        .order_by(SyncRun.started_at.desc())
        .limit(1)
    )
    return res.scalar_one_or_none()


def _to_run_response(run: SyncRun) -> SyncRunResponse:
    return SyncRunResponse(
        id=run.id,
        started_at=run.started_at,
        finished_at=run.finished_at,
        trigger=run.trigger,
        status=run.status,
        sources_synced=run.sources_synced,
        sources_failed=run.sources_failed,
        changes_detected=run.changes_detected,
        error_summary=run.error_summary,
    )


# ---- Background runner ------------------------------------------------------

async def _run_sync_for_project(project_id: str, trigger: str, run_id: str) -> None:
    """Executed as an asyncio background task. Owns its own DB session."""
    async with async_session() as db:
        # Load sources
        src_res = await db.execute(
            select(DataSourceLink).where(
                DataSourceLink.project_id == project_id,
                DataSourceLink.sync_enabled == 1,
            )
        )
        sources = list(src_res.scalars().all())

        total_changes = 0
        failed = 0
        errors: list[str] = []

        for src in sources:
            src.last_sync_status = "in_progress"
            src.last_error_msg = None
            await db.commit()

            try:
                # Dispatch to adapter (S1: most return []; real adapters in S2+)
                detected = await run_adapter(db, project_id, src)
                total_changes += detected
                src.last_sync_status = "ok"
                src.last_synced_at = _now()
            except Exception as e:
                failed += 1
                msg = f"{src.source_type}: {e}"
                errors.append(msg)
                logger.warning("Sync failed for source %s (%s): %s", src.id, src.source_type, e)
                src.last_sync_status = "error"
                src.last_error_msg = str(e)[:500]
                src.last_synced_at = _now()
            await db.commit()

        # If we collected new changes, immediately kick off analyze pass
        # so the UI shows analyzed (or auto-accepted) items instead of raw "pending".
        if total_changes > 0:
            try:
                await analyze_pending_changes(db, project_id)
            except Exception as e:
                logger.warning("Post-sync analyze failed for %s: %s", project_id, e)

        # Finalize run
        run_res = await db.execute(select(SyncRun).where(SyncRun.id == run_id))
        run = run_res.scalar_one_or_none()
        if run:
            run.finished_at = _now()
            run.sources_synced = len(sources) - failed
            run.sources_failed = failed
            run.changes_detected = total_changes
            if failed == 0:
                run.status = "ok"
            elif failed < len(sources):
                run.status = "partial"
            else:
                run.status = "error"
            run.error_summary = " | ".join(errors)[:1000] if errors else None
            await db.commit()

        # SSE notification (frontend refreshes badge/list)
        await sse_hub.emit(
            "sync_complete",
            {
                "project_id": project_id,
                "run_id": run_id,
                "status": run.status if run else "unknown",
                "changes_detected": total_changes,
                "sources_failed": failed,
            },
        )


# ---- Routes ----------------------------------------------------------------

@router.post("/{project_id}/sync")
async def trigger_sync(
    project_id: str,
    req: SyncTriggerRequest | None = None,
    db: AsyncSession = Depends(get_db),
) -> SyncTriggerResponse:
    if not await _project_exists(db, project_id):
        raise HTTPException(404, "Projekt nicht gefunden")

    req = req or SyncTriggerRequest()

    # Short-circuit if already running
    running = await _get_running_run(db, project_id)
    if running:
        return SyncTriggerResponse(run_id=running.id, started=False, reason="already_running")

    # Cooldown for auto-triggers
    if req.trigger != "manual" and not req.force:
        last = await _get_last_run(db, project_id)
        if last and last.started_at:
            dt = _parse_iso(last.started_at)
            if dt:
                elapsed = (datetime.now(timezone.utc) - dt).total_seconds()
                if elapsed < AUTO_SYNC_COOLDOWN_SECONDS:
                    return SyncTriggerResponse(
                        run_id=last.id, started=False, reason="cooldown",
                    )

    # No sources → nothing to do
    src_count = await db.scalar(
        select(func.count(DataSourceLink.id)).where(
            DataSourceLink.project_id == project_id,
            DataSourceLink.sync_enabled == 1,
        )
    )
    if not src_count:
        return SyncTriggerResponse(run_id=None, started=False, reason="no_sources")

    # Create run and kick off background task
    run = SyncRun(
        id=_gen_id(),
        project_id=project_id,
        trigger=req.trigger,
        status="running",
    )
    db.add(run)
    await db.commit()

    asyncio.create_task(_run_sync_for_project(project_id, req.trigger, run.id))

    return SyncTriggerResponse(run_id=run.id, started=True, reason="started")


@router.get("/{project_id}/sync/status")
async def sync_status(
    project_id: str,
    db: AsyncSession = Depends(get_db),
) -> SyncStatusResponse:
    if not await _project_exists(db, project_id):
        raise HTTPException(404, "Projekt nicht gefunden")

    running = await _get_running_run(db, project_id)
    last = await _get_last_run(db, project_id)
    pending = await db.scalar(
        select(func.count(SourceChange.id)).where(
            and_(
                SourceChange.project_id == project_id,
                SourceChange.analysis_status.in_(("pending", "analyzing", "analyzed")),
            )
        )
    ) or 0

    src_res = await db.execute(
        select(DataSourceLink).where(DataSourceLink.project_id == project_id)
    )
    sources = [
        SourceStatusResponse(
            id=s.id,
            source_type=s.source_type,
            display_name=s.display_name or s.source_type,
            last_synced_at=s.last_synced_at,
            last_sync_status=s.last_sync_status,
            last_error_msg=s.last_error_msg,
            sync_enabled=bool(s.sync_enabled),
        )
        for s in src_res.scalars().all()
    ]

    return SyncStatusResponse(
        running=running is not None,
        last_run=_to_run_response(last) if last else None,
        pending_changes=int(pending),
        sources=sources,
    )


@router.get("/{project_id}/sync/changes")
async def list_changes(
    project_id: str,
    status: str = Query("pending"),  # pending | analyzed | accepted | dismissed | all
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> list[SourceChangeResponse]:
    if not await _project_exists(db, project_id):
        raise HTTPException(404, "Projekt nicht gefunden")

    stmt = select(SourceChange).where(SourceChange.project_id == project_id)
    if status != "all":
        if status == "pending":
            stmt = stmt.where(SourceChange.analysis_status.in_(("pending", "analyzing", "analyzed")))
        else:
            stmt = stmt.where(SourceChange.analysis_status == status)
    stmt = stmt.order_by(SourceChange.detected_at.desc()).limit(limit)
    res = await db.execute(stmt)
    rows = res.scalars().all()

    return [
        SourceChangeResponse(
            id=r.id,
            source_type=r.source_type,
            external_ref=r.external_ref,
            title=r.title,
            detected_at=r.detected_at,
            analysis_status=r.analysis_status,
            analysis=r.analysis,
            auto_accepted=bool(r.auto_accepted),
            knowledge_item_id=r.knowledge_item_id,
        )
        for r in rows
    ]


@router.get("/sync/adapters")
async def list_adapters() -> dict:
    """Debug/introspection — which source_types have adapters wired in."""
    return {"registered": sorted(ADAPTERS.keys())}


# --- Analyze / Accept / Dismiss (S2) ----------------------------------------

@router.post("/{project_id}/sync/analyze")
async def analyze_pending(
    project_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Re-run analyzer over pending changes (manual trigger)."""
    if not await _project_exists(db, project_id):
        raise HTTPException(404, "Projekt nicht gefunden")
    counts = await analyze_pending_changes(db, project_id)
    return {"project_id": project_id, **counts}


@router.post("/{project_id}/sync/changes/{change_id}/accept")
async def accept_change(
    project_id: str,
    change_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Promote a change to a KnowledgeItem (user-driven)."""
    if not await _project_exists(db, project_id):
        raise HTTPException(404, "Projekt nicht gefunden")

    res = await db.execute(
        select(SourceChange).where(
            SourceChange.id == change_id,
            SourceChange.project_id == project_id,
        )
    )
    change = res.scalar_one_or_none()
    if not change:
        raise HTTPException(404, "Change nicht gefunden")
    if change.analysis_status not in ("analyzed", "pending"):
        raise HTTPException(400, f"Change ist bereits {change.analysis_status}")

    # If still pending, analyze first
    if change.analysis_status == "pending":
        await analyze_change(db, change)
        await db.refresh(change)

    analysis = change.analysis
    if not analysis:
        raise HTTPException(422, "Kann ohne Analyse-Ergebnis nicht akzeptieren")

    # Load project
    proj_res = await db.execute(select(Project).where(Project.id == project_id))
    project = proj_res.scalar_one_or_none()
    if not project:
        raise HTTPException(404, "Projekt nicht gefunden")

    ki = await _promote_to_knowledge(db, change, analysis, project)
    change.knowledge_item_id = ki.id
    change.analysis_status = "accepted"
    await db.commit()

    return {"success": True, "knowledge_item_id": ki.id, "change_id": change.id}


@router.post("/{project_id}/sync/changes/{change_id}/dismiss")
async def dismiss_change(
    project_id: str,
    change_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not await _project_exists(db, project_id):
        raise HTTPException(404, "Projekt nicht gefunden")
    res = await db.execute(
        select(SourceChange).where(
            SourceChange.id == change_id,
            SourceChange.project_id == project_id,
        )
    )
    change = res.scalar_one_or_none()
    if not change:
        raise HTTPException(404, "Change nicht gefunden")
    change.analysis_status = "dismissed"
    await db.commit()
    return {"success": True, "change_id": change.id}
