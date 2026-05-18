"""Pipeline orchestrator — wires Phases 1–4 into one background run.

Triggered by ``POST /api/synapse/{project_id}/generate``. Runs as a
detached asyncio task (pattern mirrors ``routers/project_sync.py``):
owns its own DB session, drives the ``SynapseGenerationRun`` row's
lifecycle, and emits ``sse_hub`` events the frontend listens on.

    clear → extract entities → detect communities → synthesise → validate

A run is a CLEAN REBUILD — the project's existing entity layer and
synapses are wiped first, so re-running after knowledge changes always
produces a consistent result rather than accumulating stale rows.

``run_synapse_generation`` never raises: any failure is recorded on the
run row (``status="error"``) and surfaced via an SSE event.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import async_session
from models.knowledge import KnowledgeItem
from models.synapse import (
    KnowledgeReviewQueue, Synapse, SynapseClaim, SynapseGenerationRun,
)
from services.sse_hub import sse_hub
from services.synapse_communities import detect_communities
from services.synapse_entities import clear_project_entities, extract_project_entities
from services.synapse_synthesis import synthesise_communities
from services.synapse_validation import ValidationStats, validate_synapse

logger = logging.getLogger("projecthub.synapse")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _combine_usage(*usages: dict) -> dict:
    """Sum ``{calls, total_tokens}`` dicts from the pipeline stages."""
    total = {"calls": 0, "total_tokens": 0}
    for u in usages:
        total["calls"] += int(u.get("calls", 0) or 0)
        total["total_tokens"] += int(u.get("total_tokens", 0) or 0)
    return total


async def _clear_project_synapses(db: AsyncSession, project_id: str) -> None:
    """Wipe synapses + claims + review-queue rows for a clean rebuild.

    Deletes children explicitly — SQLite FK cascade is not reliably on.
    """
    synapse_ids_subq = select(Synapse.id).where(Synapse.project_id == project_id)
    await db.execute(
        delete(SynapseClaim).where(SynapseClaim.synapse_id.in_(synapse_ids_subq))
    )
    await db.execute(
        delete(KnowledgeReviewQueue).where(
            KnowledgeReviewQueue.project_id == project_id
        )
    )
    await db.execute(delete(Synapse).where(Synapse.project_id == project_id))
    await db.commit()


async def _emit(project_id: str, run_id: str, phase: str, **extra) -> None:
    await sse_hub.emit("synapse_progress", {
        "project_id": project_id, "run_id": run_id, "phase": phase, **extra,
    })


async def run_synapse_generation(project_id: str, run_id: str) -> None:
    """Background entrypoint — owns its own session, never raises.

    The caller (the trigger route) has already created the
    ``SynapseGenerationRun`` row in status ``running``.
    """
    async with async_session() as db:
        run = await db.scalar(
            select(SynapseGenerationRun).where(SynapseGenerationRun.id == run_id)
        )
        if run is None:
            logger.warning("synapse run %s vanished before start", run_id)
            return

        try:
            # --- Phase: extract entities (clean rebuild first) ---
            run.phase = "extracting_entities"
            await db.commit()
            await _emit(project_id, run_id, "extracting_entities")

            await clear_project_entities(db, project_id)
            await _clear_project_synapses(db, project_id)

            item_count = await db.scalar(
                select(func.count(KnowledgeItem.id)).where(
                    KnowledgeItem.project_id == project_id
                )
            ) or 0
            run.item_count = int(item_count)
            await db.commit()

            extraction = await extract_project_entities(db, project_id)
            run.entity_count = extraction.entities_created
            await db.commit()

            # No entities from a non-empty project → AI-Assist almost certainly
            # unreachable. Fail loudly instead of finishing "ok" with 0 synapses.
            if item_count > 0 and extraction.entities_created == 0:
                raise RuntimeError(
                    "Keine Entitäten extrahiert — ist AI-Assist erreichbar?"
                )

            # --- Phase: detect communities ---
            run.phase = "detecting_communities"
            await db.commit()
            await _emit(project_id, run_id, "detecting_communities")
            communities = await detect_communities(db, project_id)

            # --- Phase: synthesise ---
            run.phase = "synthesising"
            await db.commit()
            await _emit(
                project_id, run_id, "synthesising", communities=len(communities)
            )
            synapses, synth = await synthesise_communities(
                db, project_id, communities, run_id
            )
            run.synapse_count = len(synapses)
            await db.commit()

            # --- Phase: validate ---
            run.phase = "validating"
            await db.commit()
            val_stats = ValidationStats()
            for i, synapse in enumerate(synapses, start=1):
                await validate_synapse(db, synapse, stats=val_stats)
                await _emit(
                    project_id, run_id, "validating",
                    current=i, total=len(synapses),
                )

            # --- Phase: synthesise_hierarchy (P5) ---
            # Optional Level-N pass over the Level-0 synapses, MS-GraphRAG
            # style. Gated by brain_hierarchical_synapses_enabled — default
            # OFF so existing deployments are unaffected until the flag flips.
            hierarchy_stats = None
            try:
                from config import settings as _settings

                hierarchy_on = bool(
                    getattr(_settings, "brain_hierarchical_synapses_enabled", False)
                )
            except Exception:  # pragma: no cover — defensive
                hierarchy_on = False

            if hierarchy_on and val_stats.persisted + val_stats.flagged >= 2:
                # Need at least 2 validated parents at L0 before clustering
                # is meaningful. (DEFAULT_MIN_SYNAPSES=2 in the hierarchy module.)
                run.phase = "synthesise_hierarchy"
                await db.commit()
                await _emit(project_id, run_id, "synthesise_hierarchy")
                try:
                    from services.synapse_hierarchy import run_hierarchy_phase

                    hierarchy_stats = await run_hierarchy_phase(
                        db, project_id, run_id=run_id, max_level=2,
                    )
                    logger.info(
                        "[hierarchy] project=%s levels=%d synapses=%d skipped=%d",
                        project_id,
                        hierarchy_stats.levels_built,
                        hierarchy_stats.synapses_created,
                        hierarchy_stats.skipped_clusters,
                    )
                except Exception as e:  # noqa: BLE001 — must not sink the whole run
                    logger.warning(
                        "[hierarchy] phase failed for project %s: %s", project_id, e
                    )
                    # Continue to finalise — Level-0 synapses are still good.

            # --- Finalise ---
            run.validated_count = val_stats.persisted
            run.flagged_count = val_stats.flagged
            run.review_count = val_stats.review
            run.token_usage_dict = _combine_usage(
                extraction.usage, synth.usage, val_stats.usage
            )
            # Bump synapse_count to include Level-N synapses, since they
            # show up under the same project in the UI.
            if hierarchy_stats is not None:
                run.synapse_count = (run.synapse_count or 0) + hierarchy_stats.synapses_created
            run.phase = "done"
            run.status = "ok"
            run.finished_at = _now()
            await db.commit()

            await _emit(
                project_id, run_id, "done",
                synapse_count=len(synapses),
                persisted=val_stats.persisted,
                flagged=val_stats.flagged,
                review=val_stats.review,
            )
            await sse_hub.emit("synapse_complete", {
                "project_id": project_id, "run_id": run_id,
                "status": "ok", "synapse_count": len(synapses),
            })

        except Exception as e:  # noqa: BLE001 — background task must not crash silently
            logger.exception("synapse generation failed for project %s", project_id)
            try:
                run.status = "error"
                run.phase = "done"
                run.error_summary = str(e)[:1000]
                run.finished_at = _now()
                await db.commit()
            except Exception:
                await db.rollback()
            await sse_hub.emit("synapse_complete", {
                "project_id": project_id, "run_id": run_id,
                "status": "error", "error": str(e)[:300],
            })
