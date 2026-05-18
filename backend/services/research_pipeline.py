"""Research Auto-Mode pipeline orchestrator (P6).

End-to-end background task that drives one ``ResearchRun`` from
``status=running`` to a terminal state. Owns its own DB session
(detached from the request lifecycle), pumps SearchProgress events
from all providers into the ResearchFinding table, and emits a
``research_*`` SSE stream that the frontend renders live.

Phases:

    1. PLAN        — ``research_planner.plan_subqueries`` (1 LLM call)
    2. SEARCH      — fan-out across each sub-query's providers, drain
                     SearchProgress, persist Findings as they arrive
    3. EXTRACT     — STUB in P6 (claim decomposition lands in P8)
    4. VALIDATE    — STUB in P6 (Tier-B + Tier-C land in P8)
    5. PERSIST     — promote ``status=grounded`` findings to KnowledgeItem
                     rows; status="persisted" with knowledge_item_id link

Pipeline NEVER raises out of ``run_research``: every internal failure
sets the run row to ``status="error"`` and emits ``research_complete``
with the error summary. Budget pressure does NOT cause an error — the
auto-degradation ladder catches ``BudgetDegradation`` and either
proceeds with a downgraded strategy or finalises with ``status="partial"``.

Lateral expansion (Tief-mode) is deliberately out of scope for P6 —
that lands in P7 with its own entity-extract + relevance-ranking step.
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from database import async_session
from models.research import (
    ProjectResearchSettings,
    ResearchFinding,
    ResearchRun,
    ResearchSubQuery,
)
from services.research_budget import (
    BudgetDegradation,
    BudgetTracker,
    DEFAULT_THRESHOLD_PROFILES,
    PressureThresholds,
    TokenBudgetPolicy,
)
from services.research_planner import (
    PlanResult,
    SubQueryPlan,
    plan_subqueries,
)
from services.research_providers import PROVIDERS, Finding, SearchProgress
from services.sse_hub import sse_hub

logger = logging.getLogger("projecthub.research.pipeline")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _gen_id() -> str:
    return secrets.token_hex(8)


# ── Profile shape (decoupled from the not-yet-built settings.research) ────


# Default profiles per spec §8.1; used when the run's project has no
# overrides and the global settings haven't been built yet. P10
# (settings) will replace these by feeding ``settings.research.profiles``
# in directly.
_DEFAULT_NORMAL_BUDGET = TokenBudgetPolicy(
    soft_cap_tokens=200_000,
    hard_cap_tokens=400_000,
    per_category_caps={
        "planning": 20_000,
        "summary": 150_000,
        "grounding": 100_000,
        "critic": 40_000,
        "synthesis": 0,
    },
    threshold_profile="normal",
)
_DEFAULT_TIEF_BUDGET = TokenBudgetPolicy(
    soft_cap_tokens=600_000,
    hard_cap_tokens=1_000_000,
    per_category_caps={
        "planning": 30_000,
        "summary": 400_000,
        "entity_extract": 80_000,
        "lateral_plan": 20_000,
        "grounding": 200_000,
        "critic": 400_000,
        "synthesis": 30_000,
    },
    threshold_profile="tief",
)

_DEFAULT_PROFILES: dict[str, dict[str, Any]] = {
    "normal": {
        "max_initial_sub_queries": 5,
        "max_providers_per_sub_query": 1,
        "max_findings_per_provider": 5,
        "rerank_mode": "bm25",
        "rerank_top_k": 5,
        "bm25_top_n": 10,
        "budget": _DEFAULT_NORMAL_BUDGET,
        "hard_timeout_sec": 180,
        "max_concurrent_searches": 4,
    },
    "tief": {
        "max_initial_sub_queries": 8,
        "max_providers_per_sub_query": 3,
        "max_findings_per_provider": 10,
        "rerank_mode": "auto",
        "rerank_top_k": 8,
        "bm25_top_n": 15,
        "budget": _DEFAULT_TIEF_BUDGET,
        "hard_timeout_sec": 420,
        "max_concurrent_searches": 4,
    },
}


# ── Public entrypoint ─────────────────────────────────────────────────────


async def run_research(
    project_id: str,
    run_id: str,
    *,
    cancel: asyncio.Event | None = None,
) -> None:
    """Background-task entry. Owns its DB session, never raises.

    The trigger route (P10) creates the ``ResearchRun`` row with
    ``status="running"`` and dispatches ``asyncio.create_task(
    run_research(...))``. From there this function owns the row's
    lifecycle until a terminal state lands.

    Args:
        project_id: project under which the run was triggered.
        run_id: id of the pre-created ``ResearchRun`` row.
        cancel: optional ``asyncio.Event`` for user-initiated cancel.
            None → an internal event is used (cancel never fires).
    """
    cancel = cancel or asyncio.Event()
    try:
        await _run_pipeline(project_id, run_id, cancel)
    except Exception as e:  # noqa: BLE001 — never let an unhandled exception kill the task
        logger.exception("research pipeline crashed for run=%s", run_id)
        await _finalise_with_error(run_id, str(e))


# ── Pipeline body ─────────────────────────────────────────────────────────


async def _run_pipeline(
    project_id: str,
    run_id: str,
    cancel: asyncio.Event,
) -> None:
    """Drive PLAN → SEARCH → EXTRACT → VALIDATE → PERSIST."""

    # ─ Load the run + sanity-check ─
    async with async_session() as db:
        run = await db.scalar(
            select(ResearchRun).where(ResearchRun.id == run_id)
        )
        if run is None:
            logger.warning("research run %s vanished before pipeline start", run_id)
            return
        topic = run.topic
        depth = run.depth or "normal"
        run.phase = "planning"
        await db.commit()

    # ─ Resolve profile ─
    profile = _DEFAULT_PROFILES.get(depth) or _DEFAULT_PROFILES["normal"]

    # ─ Budget tracker per run ─
    async def _sse_budget_emit(level, snapshot):
        await sse_hub.emit("research_budget", {
            "project_id": project_id,
            "run_id": run_id,
            "level": level,
            "used": snapshot.total,
            "hard_cap": snapshot.hard_cap,
            "by_category": snapshot.by_category,
            "degradations_triggered": list(snapshot.degradations_triggered),
        })

    budget = BudgetTracker(profile["budget"], sse_emit=_sse_budget_emit)

    await _emit_progress(project_id, run_id, "planning", hop=0)

    # ─ Load per-project settings (enabled providers + provider settings + hints) ─
    enabled_providers, provider_settings, routing_hints = await _load_project_settings(
        project_id
    )
    if not enabled_providers:
        await _finalise_with_error(
            run_id, "no_enabled_providers"
        )
        return

    # ─ Phase 1: PLAN ─
    try:
        plan = await plan_subqueries(
            topic,
            enabled_providers=enabled_providers,
            max_sub_queries=profile["max_initial_sub_queries"],
            max_providers_per_sub_query=profile["max_providers_per_sub_query"],
            routing_hints=routing_hints or None,
            kb_context=None,
            budget=budget,
            model=None,
        )
    except BudgetDegradation as e:
        # Planner is fundamental — can't proceed without sub-queries.
        await _finalise_with_error(
            run_id, f"planner_budget_denied:{e.suggested_action}"
        )
        return

    if not plan.sub_queries:
        await _finalise_with_error(run_id, "planner_no_sub_queries")
        return

    # Apply any optional adaptive-budget request from the planner's
    # "heavy" sub-query hints. Tracker enforces the per-run cap.
    for sq in plan.sub_queries:
        if sq.budget_request:
            await budget.request_extension(sq.budget_request)
            break  # only one extension allowed per run

    # Persist the planned sub-queries + topic-fill fallback questions
    await _persist_sub_queries(run_id, topic, plan.sub_queries)
    await _patch_run(run_id, sub_query_count=len(plan.sub_queries))

    # ─ Phase 2: SEARCH (fan-out across sub-queries) ─
    await _emit_progress(project_id, run_id, "searching", hop=0, total=len(plan.sub_queries))
    semaphore = asyncio.Semaphore(profile["max_concurrent_searches"])

    findings_count_total = 0
    error_summary_parts: list[str] = []

    async def _run_one_sq(sq: SubQueryPlan) -> int:
        """Search one sub-query across its providers; return findings count."""
        nonlocal findings_count_total
        async with semaphore:
            if cancel.is_set():
                return 0
            await _emit_subquery_started(project_id, run_id, sq)
            await _patch_sub_query(sq.id, status="running", started_at=_now())

            question = sq.question.strip() or topic
            local_count = 0
            for provider_key in sq.providers:
                if cancel.is_set():
                    break
                provider = PROVIDERS.get(provider_key)
                if provider is None:
                    logger.warning(
                        "sub-query %s references unknown provider %s",
                        sq.id, provider_key,
                    )
                    continue
                p_settings = dict(provider_settings.get(provider_key, {}))
                p_settings.setdefault(
                    "max_results", profile["max_findings_per_provider"]
                )
                try:
                    async for event in provider.stream(
                        question, p_settings, cancel, project_id=project_id,
                    ):
                        if cancel.is_set():
                            break
                        if event.kind == "finding" and event.finding is not None:
                            await _persist_finding(
                                run_id, sq.id, event.finding,
                            )
                            findings_count_total += 1
                            local_count += 1
                            await _emit_finding(
                                project_id, run_id, sq.id, event.finding,
                            )
                        elif event.kind == "error" and event.error:
                            error_summary_parts.append(
                                f"{provider_key}:{event.error[:60]}"
                            )
                        # status/done events are forwarded to the SSE
                        # progress stream only at coarse granularity to
                        # avoid flooding.
                except Exception as e:  # noqa: BLE001 — one provider must not kill the SQ
                    logger.warning(
                        "provider %s raised for sq=%s: %s", provider_key, sq.id, e
                    )
                    error_summary_parts.append(f"{provider_key}:exception")

            sq_status = "cancelled" if cancel.is_set() else "done"
            await _patch_sub_query(sq.id, status=sq_status, finished_at=_now())
            await _emit_subquery_finished(
                project_id, run_id, sq.id, local_count, sq_status,
            )
            return local_count

    # Run all sub-queries concurrently; the semaphore inside _run_one_sq
    # caps actual parallelism. asyncio.gather pumps results back.
    await asyncio.gather(
        *(_run_one_sq(sq) for sq in plan.sub_queries),
        return_exceptions=False,
    )

    # ─ Phase 3+4: EXTRACT + VALIDATE (STUBS in P6) ─
    # P8 will replace this with claim decomposition + Tier-B grounding
    # + Tier-C critic fan-out via services.research_validation. For now,
    # mark every candidate finding as "grounded" so the persist step has
    # something to write — that matches the design's "degrade to BM25
    # ordering" defaults under tight budget.
    await _emit_progress(project_id, run_id, "validating", hop=0)
    grounded_count = await _stub_validate_all(run_id, cancel)

    if cancel.is_set():
        await _finalise(run_id, status="cancelled",
                        budget_snapshot=budget.snapshot(),
                        error_summary=None)
        await _emit_complete(project_id, run_id, "cancelled", budget=budget)
        return

    # ─ Phase 5: PERSIST (grounded → KnowledgeItem) ─
    await _emit_progress(project_id, run_id, "persisting", hop=0)
    persisted_count = await _persist_grounded_as_knowledge_items(
        project_id, run_id, topic, cancel,
    )

    # ─ Done ─
    await _emit_progress(project_id, run_id, "done", hop=0)
    final_status = "partial" if budget.pressure_level() == "exhausted" else "ok"
    error_summary = "; ".join(error_summary_parts)[:1000] if error_summary_parts else None
    await _finalise(
        run_id,
        status=final_status,
        budget_snapshot=budget.snapshot(),
        finding_count=findings_count_total,
        validated_count=grounded_count,
        persisted_count=persisted_count,
        error_summary=error_summary,
    )
    await _emit_complete(project_id, run_id, final_status, budget=budget)


# ── DB helpers ────────────────────────────────────────────────────────────


async def _load_project_settings(
    project_id: str,
) -> tuple[list[str], dict, str]:
    """Read per-project settings; fall back to "Tier-1 only" defaults.

    Returns (enabled_providers, provider_settings_by_key, routing_hints).
    """
    async with async_session() as db:
        cfg = await db.scalar(
            select(ProjectResearchSettings).where(
                ProjectResearchSettings.project_id == project_id
            )
        )
    if cfg is None:
        # Fresh project: Tier-1 local providers as the safe default.
        return (
            ["kb_fts", "project_documents", "project_notes", "chat_history"],
            {},
            "",
        )
    return (
        cfg.enabled_providers_list,
        cfg.provider_settings_dict,
        cfg.routing_hints or "",
    )


async def _persist_sub_queries(
    run_id: str, topic: str, sub_queries: list[SubQueryPlan]
) -> None:
    async with async_session() as db:
        for sq in sub_queries:
            row = ResearchSubQuery(
                id=sq.id,
                run_id=run_id,
                hop=0,
                is_lateral=False,
                question=sq.question or topic,
                rationale=sq.rationale,
                priority=sq.priority,
                status="pending",
            )
            row.providers_list = sq.providers
            row.parent_finding_ids_list = []
            db.add(row)
        await db.commit()


async def _patch_sub_query(sq_id: str, **fields) -> None:
    async with async_session() as db:
        sq = await db.scalar(
            select(ResearchSubQuery).where(ResearchSubQuery.id == sq_id)
        )
        if sq is None:
            return
        for k, v in fields.items():
            setattr(sq, k, v)
        await db.commit()


async def _persist_finding(
    run_id: str, sub_query_id: str, finding: Finding,
) -> str:
    """Write a Finding to the DB. Returns the new row's id."""
    fid = _gen_id()
    async with async_session() as db:
        row = ResearchFinding(
            id=fid,
            run_id=run_id,
            sub_query_id=sub_query_id,
            provider_key=finding.provider_key,
            source_ref=finding.source_ref,
            title=finding.title or "",
            snippet=finding.snippet or "",
            full_content=finding.full_content,
            url=finding.url,
            timestamp=finding.timestamp,
            author=finding.author,
            status="candidate",
            confidence=finding.score,
        )
        row.raw_metadata_dict = finding.raw_metadata or {}
        row.extra_data_dict = {}
        db.add(row)
        await db.commit()
    return fid


async def _stub_validate_all(run_id: str, cancel: asyncio.Event) -> int:
    """STUB validate: mark every candidate finding as "grounded".

    P8 replaces this with the real Tier-B + Tier-C pipeline. The
    intermediate behaviour mirrors the "BM25-only" fallback from the
    rerank adapter — we trust the provider ordering and let the user
    accept/reject in the UI.
    """
    if cancel.is_set():
        return 0
    async with async_session() as db:
        rows = (
            await db.execute(
                select(ResearchFinding).where(
                    ResearchFinding.run_id == run_id,
                    ResearchFinding.status == "candidate",
                )
            )
        ).scalars().all()
        count = 0
        for row in rows:
            row.status = "grounded"
            row.updated_at = _now()
            count += 1
        await db.commit()
    return count


async def _persist_grounded_as_knowledge_items(
    project_id: str, run_id: str, topic: str, cancel: asyncio.Event,
) -> int:
    """Promote each grounded finding to a KnowledgeItem row.

    Uses ``source_type="research_auto"`` so existing knowledge-router
    paths recognise these as Auto-Mode output. ``source_ref`` carries
    a stable ``research:{run}:{finding_id}`` shape for idempotency.
    """
    if cancel.is_set():
        return 0

    # Lazy import — keep models.knowledge out of the module top
    # so a partial import order during test setup doesn't bite.
    from models.knowledge import KnowledgeItem
    import hashlib

    persisted = 0
    async with async_session() as db:
        findings = (
            await db.execute(
                select(ResearchFinding).where(
                    ResearchFinding.run_id == run_id,
                    ResearchFinding.status == "grounded",
                )
            )
        ).scalars().all()
        for f in findings:
            if cancel.is_set():
                break
            ref_basis = f"{project_id}|research_auto|{run_id}|{f.id}"
            source_ref = hashlib.sha256(ref_basis.encode("utf-8")).hexdigest()

            item = KnowledgeItem(
                id=_gen_id(),
                project_id=project_id,
                title=f"Recherche: {topic[:60]} — {f.title[:120]}"[:300],
                content=f"<p>{f.full_content or f.snippet}</p>",
                content_plain=(f.full_content or f.snippet or "")[:5000],
                category="reference",
                source_type="research_auto",
                source_ref=source_ref,
                tags=json.dumps([]),
                confidence=_band_for_confidence(f.confidence),
                extra_data=json.dumps({
                    "run_id": run_id,
                    "sub_query_id": f.sub_query_id,
                    "provider_key": f.provider_key,
                    "original_source_ref": f.source_ref,
                    "score": f.confidence,
                }),
            )
            db.add(item)
            await db.flush()  # so item.id is settled before we link

            f.status = "persisted"
            f.knowledge_item_id = item.id
            f.updated_at = _now()
            persisted += 1
        await db.commit()
    return persisted


def _band_for_confidence(score: float | None) -> str:
    if score is None:
        return "medium"
    if score >= 0.75:
        return "high"
    if score >= 0.5:
        return "medium"
    return "low"


async def _patch_run(run_id: str, **fields) -> None:
    async with async_session() as db:
        run = await db.scalar(
            select(ResearchRun).where(ResearchRun.id == run_id)
        )
        if run is None:
            return
        for k, v in fields.items():
            setattr(run, k, v)
        await db.commit()


async def _finalise(
    run_id: str,
    *,
    status: str,
    budget_snapshot: dict,
    finding_count: int | None = None,
    validated_count: int | None = None,
    persisted_count: int | None = None,
    error_summary: str | None = None,
) -> None:
    async with async_session() as db:
        run = await db.scalar(
            select(ResearchRun).where(ResearchRun.id == run_id)
        )
        if run is None:
            return
        run.status = status
        run.phase = "done"
        run.finished_at = _now()
        if finding_count is not None:
            run.finding_count = finding_count
        if validated_count is not None:
            run.validated_count = validated_count
        if persisted_count is not None:
            run.persisted_count = persisted_count
        if error_summary:
            run.error_summary = error_summary
        run.token_usage_dict = budget_snapshot
        await db.commit()


async def _finalise_with_error(run_id: str, error: str) -> None:
    """Set run row to status=error + emit complete; safe to call from
    any failure path (including pre-DB-load failures)."""
    async with async_session() as db:
        run = await db.scalar(
            select(ResearchRun).where(ResearchRun.id == run_id)
        )
        if run is None:
            return
        project_id = run.project_id
        run.status = "error"
        run.phase = "done"
        run.error_summary = error[:1000]
        run.finished_at = _now()
        await db.commit()
    await sse_hub.emit("research_complete", {
        "project_id": project_id,
        "run_id": run_id,
        "status": "error",
        "error": error[:300],
    })


# ── SSE emit helpers ──────────────────────────────────────────────────────


async def _emit_progress(
    project_id: str, run_id: str, phase: str, hop: int = 0, **extra,
) -> None:
    await sse_hub.emit("research_progress", {
        "project_id": project_id,
        "run_id": run_id,
        "phase": phase,
        "hop": hop,
        **extra,
    })


async def _emit_subquery_started(
    project_id: str, run_id: str, sq: SubQueryPlan,
) -> None:
    await sse_hub.emit("research_subquery_started", {
        "project_id": project_id,
        "run_id": run_id,
        "sub_query_id": sq.id,
        "hop": 0,
        "providers": sq.providers,
        "is_lateral": False,
        "parent_finding_ids": [],
    })


async def _emit_subquery_finished(
    project_id: str, run_id: str, sq_id: str, finding_count: int, status: str,
) -> None:
    await sse_hub.emit("research_subquery_finished", {
        "project_id": project_id,
        "run_id": run_id,
        "sub_query_id": sq_id,
        "finding_count": finding_count,
        "status": status,
    })


async def _emit_finding(
    project_id: str, run_id: str, sq_id: str, finding: Finding,
) -> None:
    await sse_hub.emit("research_finding", {
        "project_id": project_id,
        "run_id": run_id,
        "sub_query_id": sq_id,
        "provider_key": finding.provider_key,
        "source_ref": finding.source_ref,
        "title": finding.title,
        "snippet": finding.snippet,
        "confidence": finding.score,
    })


async def _emit_complete(
    project_id: str, run_id: str, status: str, *, budget: BudgetTracker,
) -> None:
    snapshot = budget.snapshot()
    await sse_hub.emit("research_complete", {
        "project_id": project_id,
        "run_id": run_id,
        "status": status,
        "counts": {
            "total_used": snapshot["total"],
            "max_pressure_reached": snapshot["max_pressure_reached"],
        },
        "token_usage": snapshot,
    })
