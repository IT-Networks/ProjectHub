"""HTTP surface for the Research Auto-Mode pipeline (P10).

Eleven endpoints covering the run lifecycle, finding actions, provider
discovery + health, and per-project settings:

    POST   /api/research/{pid}/runs                — trigger a new run
    GET    /api/research/{pid}/runs                — list recent runs
    GET    /api/research/runs/{run_id}             — full run detail
    POST   /api/research/runs/{run_id}/cancel      — user-initiated stop
    POST   /api/research/runs/{run_id}/findings/{fid}/accept
    POST   /api/research/runs/{run_id}/findings/{fid}/reject
    GET    /api/research/{pid}/providers           — provider catalogue
    GET    /api/research/{pid}/providers/health    — per-provider probe
    GET    /api/research/{pid}/settings            — per-project settings
    PUT    /api/research/{pid}/settings            — upsert settings

The trigger route creates the ``ResearchRun`` row in ``status="running"``
and dispatches the background task via ``asyncio.create_task`` — that
pattern mirrors the Synapse router and lets the HTTP request return
immediately with a 202.

Cancellation works via a module-level registry: each running task gets
an ``asyncio.Event`` stored under its run_id; the cancel route sets that
event. The pipeline polls the event at every phase boundary.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.project import Project
from models.research import (
    FINDING_STATUSES,
    ProjectResearchSettings,
    RESEARCH_DEPTHS,
    RESEARCH_MODES,
    ResearchFinding,
    ResearchRun,
    ResearchSubQuery,
)
from services.research_pipeline import run_research
from services.research_providers import PROVIDERS

logger = logging.getLogger("projecthub.research.router")

router = APIRouter(prefix="/api/research", tags=["research"])


# ── Cancel-event registry ─────────────────────────────────────────────────


#: run_id → asyncio.Event. The trigger route inserts an event; the cancel
#: route sets it; the pipeline pops it in its final cleanup. Module-level
#: state is fine here — there's at most one pipeline instance per project,
#: and a backend restart kills any in-flight runs anyway (they get the
#: ``error`` finalisation on next call, not a hung task).
_cancel_events: dict[str, asyncio.Event] = {}


def _register_cancel_event(run_id: str) -> asyncio.Event:
    event = asyncio.Event()
    _cancel_events[run_id] = event
    return event


def _signal_cancel(run_id: str) -> bool:
    """Set the cancel event for a run; returns False if no event exists."""
    event = _cancel_events.get(run_id)
    if event is None:
        return False
    event.set()
    return True


async def _wrap_run_research(project_id: str, run_id: str, event: asyncio.Event) -> None:
    """Pipeline wrapper that cleans up the cancel-event entry at the end."""
    try:
        await run_research(project_id, run_id, cancel=event)
    finally:
        _cancel_events.pop(run_id, None)


# ── Helpers ───────────────────────────────────────────────────────────────


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _gen_id() -> str:
    return secrets.token_hex(8)


async def _ensure_project(db: AsyncSession, project_id: str) -> Project:
    proj = await db.scalar(select(Project).where(Project.id == project_id))
    if proj is None:
        raise HTTPException(404, f"project {project_id!r} not found")
    return proj


# ── Request / response models ─────────────────────────────────────────────


class StartRunRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=2000)
    depth: Literal["normal", "tief"] | None = None
    mode: Literal["auto", "single"] = "auto"


class StartRunResponse(BaseModel):
    run_id: str
    started: bool
    depth: str
    reason: str | None = None


class FindingOut(BaseModel):
    id: str
    sub_query_id: str
    provider_key: str
    source_ref: str
    title: str
    snippet: str
    url: str | None
    timestamp: str | None
    author: str | None
    status: str
    confidence: float | None
    knowledge_item_id: str | None
    created_at: str
    updated_at: str


class SubQueryOut(BaseModel):
    id: str
    hop: int
    is_lateral: bool
    question: str
    providers: list[str]
    rationale: str
    priority: int
    relevance_score: float | None
    entity_focus: str | None
    parent_finding_ids: list[str]
    status: str
    started_at: str | None
    finished_at: str | None


class RunSummary(BaseModel):
    id: str
    project_id: str
    topic: str
    depth: str
    mode: str
    status: str
    phase: str
    current_hop: int
    sub_query_count: int
    finding_count: int
    validated_count: int
    persisted_count: int
    flagged_count: int
    rejected_count: int
    synapse_run_id: str | None
    started_at: str
    finished_at: str | None


class RunDetail(BaseModel):
    run: RunSummary
    sub_queries: list[SubQueryOut]
    findings: list[FindingOut]
    token_usage: dict


class CancelResponse(BaseModel):
    cancelled: bool
    reason: str | None = None


class FindingActionRequest(BaseModel):
    note: str | None = Field(default=None, max_length=500)


class ProviderInfo(BaseModel):
    key: str
    description: str
    typical_latency: str
    side_effect: str
    default_enabled: bool
    enabled: bool


class ProviderHealthOut(BaseModel):
    key: str
    ok: bool
    detail: str
    last_checked_at: str


class SettingsOut(BaseModel):
    default_depth: str
    enabled_providers: list[str]
    provider_settings: dict[str, Any]
    routing_hints: str
    updated_at: str


class SettingsIn(BaseModel):
    default_depth: Literal["normal", "tief"] | None = None
    enabled_providers: list[str] | None = None
    provider_settings: dict[str, Any] | None = None
    routing_hints: str | None = None


# ── Run lifecycle ─────────────────────────────────────────────────────────


@router.post("/{project_id}/runs", status_code=202)
async def start_run(
    project_id: str,
    body: StartRunRequest,
    db: AsyncSession = Depends(get_db),
) -> StartRunResponse:
    """Trigger an Auto-Mode research run.

    Returns 202 with the new ``run_id`` immediately; the actual pipeline
    runs in the background. Returns 409 with the existing run_id when
    another run is already in progress for this project.
    """
    await _ensure_project(db, project_id)

    # Depth: explicit > project default > "normal"
    depth = body.depth
    if depth is None:
        cfg = await db.scalar(
            select(ProjectResearchSettings).where(
                ProjectResearchSettings.project_id == project_id
            )
        )
        depth = (cfg.default_depth if cfg else None) or "normal"
    if depth not in RESEARCH_DEPTHS:
        raise HTTPException(400, f"invalid depth: {depth!r}")
    if body.mode not in RESEARCH_MODES:
        raise HTTPException(400, f"invalid mode: {body.mode!r}")

    # Concurrency short-circuit — analogue to /api/synapse/{pid}/generate.
    running = await db.scalar(
        select(ResearchRun)
        .where(
            ResearchRun.project_id == project_id,
            ResearchRun.status == "running",
        )
        .order_by(desc(ResearchRun.started_at))
        .limit(1)
    )
    if running:
        raise HTTPException(
            status_code=409,
            detail={
                "run_id": running.id,
                "started": False,
                "reason": "already_running",
                "depth": running.depth,
            },
        )

    run_id = _gen_id()
    run = ResearchRun(
        id=run_id,
        project_id=project_id,
        topic=body.topic.strip(),
        depth=depth,
        mode=body.mode,
        status="running",
        phase="planning",
    )
    db.add(run)
    await db.commit()

    cancel_event = _register_cancel_event(run_id)
    asyncio.create_task(_wrap_run_research(project_id, run_id, cancel_event))

    return StartRunResponse(run_id=run_id, started=True, depth=depth)


@router.get("/{project_id}/runs")
async def list_runs(
    project_id: str,
    limit: int = Query(20, ge=1, le=100),
    status: str | None = Query(None, description="Comma-separated status filter"),
    db: AsyncSession = Depends(get_db),
) -> list[RunSummary]:
    """Recent runs for the project, newest first."""
    await _ensure_project(db, project_id)
    stmt = (
        select(ResearchRun)
        .where(ResearchRun.project_id == project_id)
        .order_by(desc(ResearchRun.started_at))
        .limit(limit)
    )
    if status:
        wanted = {s.strip() for s in status.split(",") if s.strip()}
        stmt = stmt.where(ResearchRun.status.in_(wanted))
    rows = (await db.execute(stmt)).scalars().all()
    return [_run_to_summary(r) for r in rows]


@router.get("/runs/{run_id}")
async def get_run_detail(
    run_id: str,
    db: AsyncSession = Depends(get_db),
) -> RunDetail:
    """Full run with sub-queries + findings."""
    run = await db.scalar(select(ResearchRun).where(ResearchRun.id == run_id))
    if run is None:
        raise HTTPException(404, f"run {run_id!r} not found")
    sqs = (
        await db.execute(
            select(ResearchSubQuery)
            .where(ResearchSubQuery.run_id == run_id)
            .order_by(ResearchSubQuery.hop, ResearchSubQuery.priority)
        )
    ).scalars().all()
    findings = (
        await db.execute(
            select(ResearchFinding)
            .where(ResearchFinding.run_id == run_id)
            .order_by(ResearchFinding.created_at)
        )
    ).scalars().all()
    return RunDetail(
        run=_run_to_summary(run),
        sub_queries=[_sq_to_out(sq) for sq in sqs],
        findings=[_finding_to_out(f) for f in findings],
        token_usage=run.token_usage_dict,
    )


@router.post("/runs/{run_id}/cancel")
async def cancel_run(
    run_id: str,
    db: AsyncSession = Depends(get_db),
) -> CancelResponse:
    """Signal cancel to a running pipeline."""
    run = await db.scalar(select(ResearchRun).where(ResearchRun.id == run_id))
    if run is None:
        raise HTTPException(404, f"run {run_id!r} not found")
    if run.status != "running":
        return CancelResponse(cancelled=False, reason=f"status={run.status}")
    cancelled = _signal_cancel(run_id)
    if not cancelled:
        return CancelResponse(cancelled=False, reason="no_active_event")
    return CancelResponse(cancelled=True)


# ── Finding actions ───────────────────────────────────────────────────────


@router.post("/runs/{run_id}/findings/{finding_id}/accept")
async def accept_finding(
    run_id: str, finding_id: str,
    body: FindingActionRequest = Body(default_factory=FindingActionRequest),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Manually mark a flagged finding as grounded → promote to KB later.

    The pipeline only persists ``grounded`` findings during a run; a
    user accept here promotes a previously-flagged finding to grounded
    so it gets picked up by the next sweep (or surfaced as "ready to
    promote" in the UI).
    """
    return await _patch_finding_action(
        db, run_id, finding_id, new_status="grounded",
        action="accepted", note=body.note,
    )


@router.post("/runs/{run_id}/findings/{finding_id}/reject")
async def reject_finding(
    run_id: str, finding_id: str,
    body: FindingActionRequest = Body(default_factory=FindingActionRequest),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Manually mark a finding as rejected (user-initiated review verdict)."""
    return await _patch_finding_action(
        db, run_id, finding_id, new_status="rejected",
        action="rejected", note=body.note,
    )


async def _patch_finding_action(
    db: AsyncSession, run_id: str, finding_id: str, *,
    new_status: str, action: str, note: str | None,
) -> dict:
    if new_status not in FINDING_STATUSES:
        raise HTTPException(400, f"invalid new_status {new_status!r}")
    row = await db.scalar(
        select(ResearchFinding).where(
            ResearchFinding.id == finding_id,
            ResearchFinding.run_id == run_id,
        )
    )
    if row is None:
        raise HTTPException(404, f"finding {finding_id!r} not in run {run_id!r}")
    row.status = new_status
    extra = row.extra_data_dict
    user_actions = extra.get("user_actions") or []
    user_actions.append({
        "action": action,
        "note": note,
        "at": _now(),
    })
    extra["user_actions"] = user_actions
    row.extra_data_dict = extra
    row.updated_at = _now()
    await db.commit()
    return {"ok": True, "finding_id": finding_id, "new_status": new_status}


# ── Provider discovery + health ───────────────────────────────────────────


@router.get("/{project_id}/providers")
async def list_providers(
    project_id: str,
    db: AsyncSession = Depends(get_db),
) -> list[ProviderInfo]:
    """All registered providers with per-project enabled status."""
    await _ensure_project(db, project_id)
    cfg = await db.scalar(
        select(ProjectResearchSettings).where(
            ProjectResearchSettings.project_id == project_id
        )
    )
    enabled = set(cfg.enabled_providers_list) if cfg else set()
    # When no settings exist, default-enabled providers (Tier-1 local) count
    # as "on" — same logic the pipeline applies in _load_project_settings.
    return [
        ProviderInfo(
            key=key,
            description=p.description,
            typical_latency=p.typical_latency,
            side_effect=p.side_effect,
            default_enabled=p.default_enabled,
            enabled=(
                (key in enabled) if cfg is not None
                else p.default_enabled
            ),
        )
        for key, p in PROVIDERS.items()
    ]


@router.get("/{project_id}/providers/health")
async def provider_health(
    project_id: str,
    refresh: bool = Query(False, description="Always-true placeholder; v1 doesn't cache"),
    db: AsyncSession = Depends(get_db),
) -> list[ProviderHealthOut]:
    """Run each enabled provider's health probe in parallel.

    Disabled providers report as ``ok=False detail="disabled"`` so the
    settings UI can render them with a grey indicator.
    """
    await _ensure_project(db, project_id)
    cfg = await db.scalar(
        select(ProjectResearchSettings).where(
            ProjectResearchSettings.project_id == project_id
        )
    )
    enabled = set(cfg.enabled_providers_list) if cfg else set()

    async def _check(key: str, provider: Any) -> ProviderHealthOut:
        is_enabled = (
            (key in enabled) if cfg is not None
            else provider.default_enabled
        )
        if not is_enabled:
            return ProviderHealthOut(
                key=key, ok=False, detail="disabled", last_checked_at=_now(),
            )
        try:
            h = await provider.health()
        except Exception as e:  # noqa: BLE001 — health must never raise here
            return ProviderHealthOut(
                key=key, ok=False, detail=f"probe_failed:{e!s}"[:120],
                last_checked_at=_now(),
            )
        return ProviderHealthOut(
            key=key, ok=h.ok, detail=h.detail, last_checked_at=h.last_checked_at,
        )

    items = list(PROVIDERS.items())
    results = await asyncio.gather(
        *(_check(k, p) for k, p in items), return_exceptions=False,
    )
    return list(results)


# ── Per-project settings ──────────────────────────────────────────────────


@router.get("/{project_id}/settings")
async def get_settings(
    project_id: str,
    db: AsyncSession = Depends(get_db),
) -> SettingsOut:
    """Return per-project research settings (empty defaults if none yet)."""
    await _ensure_project(db, project_id)
    cfg = await db.scalar(
        select(ProjectResearchSettings).where(
            ProjectResearchSettings.project_id == project_id
        )
    )
    if cfg is None:
        return SettingsOut(
            default_depth="normal",
            enabled_providers=[
                k for k, p in PROVIDERS.items() if p.default_enabled
            ],
            provider_settings={},
            routing_hints="",
            updated_at=_now(),
        )
    return SettingsOut(
        default_depth=cfg.default_depth or "normal",
        enabled_providers=cfg.enabled_providers_list,
        provider_settings=cfg.provider_settings_dict,
        routing_hints=cfg.routing_hints or "",
        updated_at=cfg.updated_at,
    )


@router.put("/{project_id}/settings")
async def upsert_settings(
    project_id: str,
    body: SettingsIn,
    db: AsyncSession = Depends(get_db),
) -> SettingsOut:
    """Create or update per-project research settings."""
    await _ensure_project(db, project_id)

    # Validate enabled_providers against registry — silently drop unknown keys
    # so a frontend version-skew doesn't 400 the user.
    enabled = (
        [p for p in body.enabled_providers if p in PROVIDERS]
        if body.enabled_providers is not None
        else None
    )
    if body.default_depth is not None and body.default_depth not in RESEARCH_DEPTHS:
        raise HTTPException(400, f"invalid default_depth {body.default_depth!r}")

    cfg = await db.scalar(
        select(ProjectResearchSettings).where(
            ProjectResearchSettings.project_id == project_id
        )
    )
    if cfg is None:
        cfg = ProjectResearchSettings(
            project_id=project_id,
            default_depth=body.default_depth or "normal",
            routing_hints=body.routing_hints or "",
        )
        cfg.enabled_providers_list = enabled if enabled is not None else [
            k for k, p in PROVIDERS.items() if p.default_enabled
        ]
        cfg.provider_settings_dict = body.provider_settings or {}
        db.add(cfg)
    else:
        if body.default_depth is not None:
            cfg.default_depth = body.default_depth
        if enabled is not None:
            cfg.enabled_providers_list = enabled
        if body.provider_settings is not None:
            cfg.provider_settings_dict = body.provider_settings
        if body.routing_hints is not None:
            cfg.routing_hints = body.routing_hints
        cfg.updated_at = _now()

    await db.commit()
    return SettingsOut(
        default_depth=cfg.default_depth or "normal",
        enabled_providers=cfg.enabled_providers_list,
        provider_settings=cfg.provider_settings_dict,
        routing_hints=cfg.routing_hints or "",
        updated_at=cfg.updated_at,
    )


# ── Serialisers ───────────────────────────────────────────────────────────


def _run_to_summary(run: ResearchRun) -> RunSummary:
    return RunSummary(
        id=run.id,
        project_id=run.project_id,
        topic=run.topic,
        depth=run.depth,
        mode=run.mode,
        status=run.status,
        phase=run.phase,
        current_hop=run.current_hop,
        sub_query_count=run.sub_query_count,
        finding_count=run.finding_count,
        validated_count=run.validated_count,
        persisted_count=run.persisted_count,
        flagged_count=run.flagged_count,
        rejected_count=run.rejected_count,
        synapse_run_id=run.synapse_run_id,
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


def _sq_to_out(sq: ResearchSubQuery) -> SubQueryOut:
    return SubQueryOut(
        id=sq.id,
        hop=sq.hop,
        is_lateral=bool(sq.is_lateral),
        question=sq.question,
        providers=sq.providers_list,
        rationale=sq.rationale or "",
        priority=sq.priority,
        relevance_score=sq.relevance_score,
        entity_focus=sq.entity_focus,
        parent_finding_ids=sq.parent_finding_ids_list,
        status=sq.status,
        started_at=sq.started_at,
        finished_at=sq.finished_at,
    )


def _finding_to_out(f: ResearchFinding) -> FindingOut:
    return FindingOut(
        id=f.id,
        sub_query_id=f.sub_query_id,
        provider_key=f.provider_key,
        source_ref=f.source_ref,
        title=f.title or "",
        snippet=f.snippet or "",
        url=f.url,
        timestamp=f.timestamp,
        author=f.author,
        status=f.status,
        confidence=f.confidence,
        knowledge_item_id=f.knowledge_item_id,
        created_at=f.created_at,
        updated_at=f.updated_at,
    )
