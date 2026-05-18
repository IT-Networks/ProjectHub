"""Synapsen API — knowledge synthesis & validation.

Endpoints (prefix ``/api/synapse``):

    POST   /{project_id}/generate                  → kick off a background run
    GET    /{project_id}/runs                       → recent generation runs
    GET    /{project_id}/runs/{run_id}              → one run's status
    GET    /{project_id}/synapses                   → list synapses
    GET    /{project_id}/synapses/{synapse_id}      → synapse detail + claims
    DELETE /{project_id}/synapses/{synapse_id}      → delete a synapse
    POST   /{project_id}/synapses/{synapse_id}/review → human verdict
    GET    /{project_id}/review-queue               → open review items
    POST   /{project_id}/ask                        → corpus-wide question

The generation pipeline runs as a detached asyncio task — the trigger
route only creates the ``SynapseGenerationRun`` row and returns; progress
arrives via ``sse_hub`` (``synapse_progress`` / ``synapse_complete``).
"""

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.project import Project
from models.synapse import (
    KnowledgeReviewQueue, Synapse, SynapseClaim, SynapseGenerationRun,
)
from services.synapse_llm import call_json, gen_id
from services.synapse_pipeline import run_synapse_generation

router = APIRouter(prefix="/api/synapse", tags=["synapse"])
logger = logging.getLogger("projecthub.synapse")

# Cap synapses fed into one /ask call — keeps the prompt within budget.
_ASK_MAX_SYNAPSES = 25

_HUMAN_VERDICTS = {"accepted", "rejected", "edited"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _ensure_project(db: AsyncSession, project_id: str) -> None:
    exists = await db.scalar(select(Project.id).where(Project.id == project_id))
    if not exists:
        raise HTTPException(404, "Projekt nicht gefunden")


# --- Schemas ----------------------------------------------------------------

class GenerateResponse(BaseModel):
    run_id: str | None
    started: bool
    reason: str  # started | already_running | project_not_found


class RunResponse(BaseModel):
    id: str
    project_id: str
    trigger: str
    status: str
    phase: str
    item_count: int
    entity_count: int
    synapse_count: int
    validated_count: int
    flagged_count: int
    review_count: int
    token_usage: dict
    error_summary: str | None
    started_at: str
    finished_at: str | None


class SynapseResponse(BaseModel):
    id: str
    project_id: str
    title: str
    summary: str
    summary_plain: str
    community_level: int
    confidence: float
    confidence_band: str
    verdict: str
    status: str
    source_item_ids: list[str]
    source_entity_ids: list[str]
    claim_count: int
    created_at: str
    updated_at: str


class ClaimResponse(BaseModel):
    id: str
    claim_text: str
    relation: str
    evidence: list[dict]
    nli_score: float | None
    verifier_agreement: float
    verifier_votes: dict
    # P10 — bi-temporal fields. ``valid_to is None`` ⇒ "currently true".
    valid_from: str = ""
    valid_to: str | None = None
    superseded_by: str | None = None
    is_current: bool = True


class SynapseDetailResponse(SynapseResponse):
    claims: list[ClaimResponse]
    defects: list[str]


class ReviewQueueItemResponse(BaseModel):
    id: str
    synapse_id: str
    synapse_title: str
    reason: str
    confidence: float
    human_verdict: str | None
    created_at: str
    resolved_at: str | None


class ReviewRequest(BaseModel):
    verdict: str  # accepted | rejected | edited


class AskRequest(BaseModel):
    question: str


class AskSource(BaseModel):
    synapse_id: str
    title: str
    confidence: float


class AskResponse(BaseModel):
    answer: str
    sources: list[AskSource]


# --- Serialisers ------------------------------------------------------------

def _run_to_response(run: SynapseGenerationRun) -> RunResponse:
    return RunResponse(
        id=run.id,
        project_id=run.project_id,
        trigger=run.trigger,
        status=run.status,
        phase=run.phase,
        item_count=run.item_count,
        entity_count=run.entity_count,
        synapse_count=run.synapse_count,
        validated_count=run.validated_count,
        flagged_count=run.flagged_count,
        review_count=run.review_count,
        token_usage=run.token_usage_dict,
        error_summary=run.error_summary,
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


def _synapse_to_response(syn: Synapse, claim_count: int) -> SynapseResponse:
    return SynapseResponse(
        id=syn.id,
        project_id=syn.project_id,
        title=syn.title,
        summary=syn.summary,
        summary_plain=syn.summary_plain,
        community_level=syn.community_level,
        confidence=syn.confidence,
        confidence_band=syn.confidence_band,
        verdict=syn.verdict,
        status=syn.status,
        source_item_ids=syn.source_item_ids_list,
        source_entity_ids=syn.source_entity_ids_list,
        claim_count=claim_count,
        created_at=syn.created_at,
        updated_at=syn.updated_at,
    )


def _claim_to_response(claim: SynapseClaim) -> ClaimResponse:
    return ClaimResponse(
        id=claim.id,
        claim_text=claim.claim_text,
        relation=claim.relation,
        evidence=claim.evidence_list,
        nli_score=claim.nli_score,
        verifier_agreement=claim.verifier_agreement,
        verifier_votes=claim.verifier_votes_dict,
        valid_from=claim.valid_from or claim.created_at,
        valid_to=claim.valid_to,
        superseded_by=claim.superseded_by,
        is_current=claim.is_current,
    )


# --- Generation -------------------------------------------------------------

@router.post("/{project_id}/generate")
async def generate(
    project_id: str, db: AsyncSession = Depends(get_db)
) -> GenerateResponse:
    """Trigger a synapse generation run (non-blocking).

    Short-circuits if a run is already in progress for this project — the
    pipeline does a full clean rebuild, so concurrent runs would race.
    """
    await _ensure_project(db, project_id)

    running = await db.scalar(
        select(SynapseGenerationRun)
        .where(
            SynapseGenerationRun.project_id == project_id,
            SynapseGenerationRun.status == "running",
        )
        .order_by(SynapseGenerationRun.started_at.desc())
        .limit(1)
    )
    if running:
        return GenerateResponse(
            run_id=running.id, started=False, reason="already_running"
        )

    run = SynapseGenerationRun(
        id=gen_id(),
        project_id=project_id,
        trigger="manual",
        status="running",
        phase="extracting_entities",
    )
    db.add(run)
    await db.commit()

    asyncio.create_task(run_synapse_generation(project_id, run.id))
    return GenerateResponse(run_id=run.id, started=True, reason="started")


@router.get("/{project_id}/runs")
async def list_runs(
    project_id: str,
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
) -> list[RunResponse]:
    await _ensure_project(db, project_id)
    result = await db.execute(
        select(SynapseGenerationRun)
        .where(SynapseGenerationRun.project_id == project_id)
        .order_by(SynapseGenerationRun.started_at.desc())
        .limit(limit)
    )
    return [_run_to_response(r) for r in result.scalars().all()]


@router.get("/{project_id}/runs/{run_id}")
async def get_run(
    project_id: str, run_id: str, db: AsyncSession = Depends(get_db)
) -> RunResponse:
    run = await db.scalar(
        select(SynapseGenerationRun).where(
            SynapseGenerationRun.id == run_id,
            SynapseGenerationRun.project_id == project_id,
        )
    )
    if not run:
        raise HTTPException(404, "Generierungs-Lauf nicht gefunden")
    return _run_to_response(run)


# --- Synapses ---------------------------------------------------------------

@router.get("/{project_id}/synapses")
async def list_synapses(
    project_id: str,
    verdict: str | None = None,
    status: str | None = None,
    limit: int = Query(100, ge=1, le=300),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[SynapseResponse]:
    """List a project's synapses, highest-confidence first."""
    await _ensure_project(db, project_id)

    query = select(Synapse).where(Synapse.project_id == project_id)
    if verdict:
        query = query.where(Synapse.verdict == verdict)
    if status:
        query = query.where(Synapse.status == status)
    query = query.order_by(Synapse.confidence.desc()).limit(limit).offset(offset)

    result = await db.execute(query)
    synapses = list(result.scalars().all())
    if not synapses:
        return []

    # One grouped query for claim counts — avoids N+1.
    # P10: count CURRENT claims only (valid_to IS NULL). The history rows
    # would inflate this number after each regeneration.
    counts_result = await db.execute(
        select(SynapseClaim.synapse_id, func.count(SynapseClaim.id))
        .where(SynapseClaim.synapse_id.in_([s.id for s in synapses]))
        .where(SynapseClaim.valid_to.is_(None))
        .group_by(SynapseClaim.synapse_id)
    )
    claim_counts = {sid: cnt for sid, cnt in counts_result.all()}

    return [
        _synapse_to_response(s, claim_counts.get(s.id, 0)) for s in synapses
    ]


@router.get("/{project_id}/hierarchy")
async def get_hierarchy(
    project_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Tree of validated synapses across all levels (P5, T5.3).

    Response shape::

        {
          "max_level": 2,
          "level_counts": {"0": 12, "1": 3, "2": 1},
          "nodes": [
            {
              "id": "...",
              "title": "...",
              "summary_plain": "...",
              "confidence": 0.87,
              "confidence_band": "high",
              "verdict": "persist",
              "community_level": 0,
              "parent_id": "abc...",         // points UP to Level-1 parent
              "source_count": 5
            },
            ...
          ]
        }

    Frontend builds the tree client-side from ``parent_id`` — keeps the
    server-side query flat and cacheable, and lets the UI toggle between
    flat and tree without a second request.

    Only ``status='validated'`` synapses with verdict in {persist,
    persist_flagged} are included — the "tree" should reflect what's
    trusted, not the human-review queue. (The review queue has its own
    endpoint.)
    """
    await _ensure_project(db, project_id)

    result = await db.execute(
        select(Synapse).where(
            Synapse.project_id == project_id,
            Synapse.status == "validated",
            Synapse.verdict.in_(["persist", "persist_flagged"]),
        ).order_by(
            Synapse.community_level.desc(),  # roots first
            Synapse.confidence.desc(),
        )
    )
    synapses = list(result.scalars().all())

    level_counts: dict[int, int] = {}
    nodes: list[dict] = []
    max_level = 0
    for s in synapses:
        lvl = int(s.community_level or 0)
        max_level = max(max_level, lvl)
        level_counts[lvl] = level_counts.get(lvl, 0) + 1
        nodes.append({
            "id": s.id,
            "title": s.title,
            "summary_plain": (s.summary_plain or "")[:600],
            "confidence": float(s.confidence or 0.0),
            "confidence_band": s.confidence_band,
            "verdict": s.verdict,
            "community_level": lvl,
            "parent_id": s.parent_id,
            "source_count": len(s.source_item_ids_list),
        })

    return {
        "max_level": max_level,
        "level_counts": {str(k): v for k, v in level_counts.items()},
        "nodes": nodes,
    }


@router.get("/{project_id}/review-queue")
async def review_queue(
    project_id: str,
    include_resolved: bool = False,
    db: AsyncSession = Depends(get_db),
) -> list[ReviewQueueItemResponse]:
    """Synapses awaiting (or with) a human verdict. Open items by default."""
    await _ensure_project(db, project_id)

    query = (
        select(KnowledgeReviewQueue, Synapse)
        .join(Synapse, Synapse.id == KnowledgeReviewQueue.synapse_id)
        .where(KnowledgeReviewQueue.project_id == project_id)
    )
    if not include_resolved:
        query = query.where(KnowledgeReviewQueue.human_verdict.is_(None))
    query = query.order_by(KnowledgeReviewQueue.created_at.desc())

    result = await db.execute(query)
    return [
        ReviewQueueItemResponse(
            id=rq.id,
            synapse_id=rq.synapse_id,
            synapse_title=syn.title,
            reason=rq.reason,
            confidence=syn.confidence,
            human_verdict=rq.human_verdict,
            created_at=rq.created_at,
            resolved_at=rq.resolved_at,
        )
        for rq, syn in result.all()
    ]


@router.post("/{project_id}/ask")
async def ask(
    project_id: str, data: AskRequest, db: AsyncSession = Depends(get_db)
) -> AskResponse:
    """Answer a corpus-wide question by synthesising over validated synapses.

    A lightweight "global search": the highest-confidence persisted /
    flagged synapses are fed to the LLM, which answers and cites the
    synapse numbers it used.
    """
    await _ensure_project(db, project_id)

    question = (data.question or "").strip()
    if not question:
        raise HTTPException(400, "Frage darf nicht leer sein")

    result = await db.execute(
        select(Synapse)
        .where(
            Synapse.project_id == project_id,
            Synapse.status == "validated",
            Synapse.verdict.in_(["persist", "persist_flagged"]),
        )
        .order_by(Synapse.confidence.desc())
        .limit(_ASK_MAX_SYNAPSES)
    )
    synapses = list(result.scalars().all())
    if not synapses:
        return AskResponse(
            answer="Es gibt noch kein validiertes Synapsen-Wissen für dieses "
                   "Projekt. Starte zuerst eine Wissens-Synthese.",
            sources=[],
        )

    blocks = []
    for i, syn in enumerate(synapses, start=1):
        blocks.append(
            f"[{i}] {syn.title} (Konfidenz {syn.confidence:.2f})\n"
            f"{syn.summary_plain}"
        )
    prompt = (
        "Beantworte die FRAGE ausschließlich auf Basis des folgenden "
        "synthetisierten Projektwissens. Stütze jede Aussage auf die "
        "Wissens-Nummern und erfinde nichts.\n\n"
        f"FRAGE: {question}\n\n"
        "SYNTHETISIERTES WISSEN:\n" + "\n\n".join(blocks) + "\n\n"
        "Antworte AUSSCHLIESSLICH als valides JSON:\n"
        '{"answer": "deine Antwort", "sources": [1, 2]}\n'
        '"sources" = die Wissens-Nummern, auf die du dich stützt. '
        "Wenn das Wissen die Frage nicht beantwortet, sage das klar in "
        '"answer" und gib "sources": [].'
    )

    res = await call_json(prompt, session_prefix="ask")
    if not res.ok or not isinstance(res.parsed, dict):
        raise HTTPException(502, "Keine verwertbare Antwort von AI-Assist")

    answer = str(res.parsed.get("answer") or "").strip()
    if not answer:
        raise HTTPException(502, "Leere Antwort von AI-Assist")

    used_sources: list[AskSource] = []
    for num in res.parsed.get("sources") or []:
        try:
            idx = int(num) - 1
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(synapses):
            syn = synapses[idx]
            used_sources.append(AskSource(
                synapse_id=syn.id, title=syn.title, confidence=syn.confidence,
            ))

    return AskResponse(answer=answer, sources=used_sources)


@router.get("/{project_id}/synapses/{synapse_id}")
async def get_synapse(
    project_id: str,
    synapse_id: str,
    as_of: str | None = Query(
        None,
        description=(
            "ISO timestamp — return claims that were valid at this "
            "moment. Default: currently-valid claims only."
        ),
    ),
    include_history: bool = Query(
        False,
        description=(
            "If true, return ALL claim versions (current + superseded). "
            "Mutually exclusive with as_of — as_of wins."
        ),
    ),
    db: AsyncSession = Depends(get_db),
) -> SynapseDetailResponse:
    """Get a synapse plus its claims.

    Bi-temporal querying (P10):
        * default                 → currently-valid claims (valid_to IS NULL)
        * ?as_of=2026-03-01T00:00 → claims valid AT that moment
        * ?include_history=true   → every claim version, oldest-first
    """
    syn = await db.scalar(
        select(Synapse).where(
            Synapse.id == synapse_id, Synapse.project_id == project_id
        )
    )
    if not syn:
        raise HTTPException(404, "Synapse nicht gefunden")

    # Claim-selection strategy
    if as_of:
        from services.synapse_claims_bitemporal import claims_as_of
        claims = await claims_as_of(db, synapse_id, as_of)
    elif include_history:
        result = await db.execute(
            select(SynapseClaim)
            .where(SynapseClaim.synapse_id == synapse_id)
            .order_by(SynapseClaim.valid_from, SynapseClaim.created_at)
        )
        claims = list(result.scalars().all())
    else:
        from services.synapse_claims_bitemporal import current_claims
        claims = await current_claims(db, synapse_id)

    defects = syn.extra_data_dict.get("validation", {}).get("defects", [])

    base = _synapse_to_response(syn, len(claims))
    return SynapseDetailResponse(
        **base.model_dump(),
        claims=[_claim_to_response(c) for c in claims],
        defects=defects,
    )


@router.delete("/{project_id}/synapses/{synapse_id}")
async def delete_synapse(
    project_id: str, synapse_id: str, db: AsyncSession = Depends(get_db)
) -> dict:
    syn = await db.scalar(
        select(Synapse).where(
            Synapse.id == synapse_id, Synapse.project_id == project_id
        )
    )
    if not syn:
        raise HTTPException(404, "Synapse nicht gefunden")

    await db.execute(
        delete(SynapseClaim).where(SynapseClaim.synapse_id == synapse_id)
    )
    await db.execute(
        delete(KnowledgeReviewQueue).where(
            KnowledgeReviewQueue.synapse_id == synapse_id
        )
    )
    await db.delete(syn)
    await db.commit()
    return {"success": True}


@router.post("/{project_id}/synapses/{synapse_id}/review")
async def review_synapse(
    project_id: str,
    synapse_id: str,
    data: ReviewRequest,
    db: AsyncSession = Depends(get_db),
) -> SynapseResponse:
    """Record a human verdict on a synapse from the review queue.

    ``accepted`` → the synapse becomes trusted (verdict ``persist_flagged``);
    ``rejected`` → ``status="rejected"``; ``edited`` → just closes the
    review item (the user edited the content elsewhere).
    """
    if data.verdict not in _HUMAN_VERDICTS:
        raise HTTPException(400, f"Ungültiges Verdikt. Erlaubt: {_HUMAN_VERDICTS}")

    syn = await db.scalar(
        select(Synapse).where(
            Synapse.id == synapse_id, Synapse.project_id == project_id
        )
    )
    if not syn:
        raise HTTPException(404, "Synapse nicht gefunden")

    if data.verdict == "accepted":
        syn.verdict = "persist_flagged"
        syn.status = "validated"
    elif data.verdict == "rejected":
        syn.status = "rejected"
    syn.updated_at = _now()

    # Close any open review-queue rows for this synapse.
    open_rows = await db.execute(
        select(KnowledgeReviewQueue).where(
            KnowledgeReviewQueue.synapse_id == synapse_id,
            KnowledgeReviewQueue.human_verdict.is_(None),
        )
    )
    for rq in open_rows.scalars().all():
        rq.human_verdict = data.verdict
        rq.resolved_at = _now()

    await db.commit()

    # P10: only currently-valid claims count toward the UI's "X claims" badge.
    claim_count = await db.scalar(
        select(func.count(SynapseClaim.id)).where(
            SynapseClaim.synapse_id == synapse_id,
            SynapseClaim.valid_to.is_(None),
        )
    ) or 0
    return _synapse_to_response(syn, int(claim_count))
