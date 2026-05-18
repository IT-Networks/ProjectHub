"""Bi-temporal helpers for SynapseClaim (P10).

A claim has two time axes:

    valid_time   — the window the claim was *true* in the world
                   (``valid_from`` → ``valid_to``; ``valid_to is None``
                   means "still current")

    transaction_time — when the row was *written*
                   (``created_at`` immutable, ``updated_at`` moves)

These two axes power three common queries the rest of the system needs:

    current_claims(db, synapse_id)        → what's true NOW
    claims_as_of(db, synapse_id, ts)      → what was true AT ts (audit)
    supersede_claims(db, synapse_id, new) → atomic regen with history

This module is the canonical place to perform those operations. The
validation pipeline (``synapse_validation.persist``) and the planned P9
Mem0-style incremental updater both go through these helpers, so the
versioning invariant ("currently-valid claims have valid_to IS NULL"
and "every supersede sets BOTH valid_to and superseded_by") stays
enforced in one place.

All functions are async / SQLAlchemy 2 / AsyncSession compatible — the
same idiom the routers already use.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.synapse import SynapseClaim


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def current_claims(
    db: AsyncSession, synapse_id: str,
) -> list[SynapseClaim]:
    """Return the currently-valid claims for a synapse.

    A claim is "current" iff ``valid_to IS NULL``. Ordered by
    ``valid_from`` so the rendering order is stable (oldest-true-first
    matches how the synapse summary was originally composed).
    """
    stmt = (
        select(SynapseClaim)
        .where(SynapseClaim.synapse_id == synapse_id)
        .where(SynapseClaim.valid_to.is_(None))
        .order_by(SynapseClaim.valid_from)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def claims_as_of(
    db: AsyncSession, synapse_id: str, as_of_iso: str,
) -> list[SynapseClaim]:
    """Return the claims that were valid for ``synapse_id`` at ``as_of_iso``.

    Definition of "valid at time T":
        valid_from <= T AND (valid_to IS NULL OR valid_to > T)

    Use this for audit / replay queries — "show me what the synapse
    looked like on 2026-03-01". Ordered by valid_from like current_claims.
    """
    stmt = (
        select(SynapseClaim)
        .where(SynapseClaim.synapse_id == synapse_id)
        .where(SynapseClaim.valid_from <= as_of_iso)
        .where(
            or_(
                SynapseClaim.valid_to.is_(None),
                SynapseClaim.valid_to > as_of_iso,
            )
        )
        .order_by(SynapseClaim.valid_from)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def supersede_claims(
    db: AsyncSession,
    *,
    synapse_id: str,
    new_claims: Sequence[SynapseClaim],
    only_claim_ids: Sequence[str] | None = None,
    transition_ts: str | None = None,
) -> dict:
    """Atomically supersede currently-valid claims with new rows.

    Two modes:

        only_claim_ids = None
            Replace ALL currently-valid claims (full regen). Every old
            "current" row for the synapse gets its valid_to set and is
            linked via superseded_by to ONE new row each — we pick the
            new row at the same positional index when lengths line up,
            and round-robin otherwise (the superseded_by link is best-
            effort metadata, not an integrity constraint).

        only_claim_ids = [...]
            Replace exactly those old rows (incremental update). Old
            rows NOT in the list keep their valid_to=NULL state. This is
            the path the P9 Mem0-style updater will use.

    Args:
        db: Async session — flush/commit is the caller's job.
        synapse_id: Target synapse.
        new_claims: Already-constructed SynapseClaim rows. Their
            ``valid_from`` / ``updated_at`` are forced to ``transition_ts``
            so the supersede instant is a single timestamp; whatever the
            caller set on those fields is overwritten on purpose.
        only_claim_ids: Optional subset of OLD claim ids to supersede.
            None = supersede everything currently valid.
        transition_ts: ISO timestamp for the supersede instant. Default
            ``now()``.

    Returns:
        {"superseded": n_old, "added": n_new, "ts": transition_ts}
    """
    ts = transition_ts or _now_iso()

    # Resolve the OLD rows we're closing out.
    where_clauses = [
        SynapseClaim.synapse_id == synapse_id,
        SynapseClaim.valid_to.is_(None),
    ]
    if only_claim_ids is not None:
        where_clauses.append(SynapseClaim.id.in_(list(only_claim_ids)))
    old_stmt = select(SynapseClaim).where(and_(*where_clauses))
    old_rows = list((await db.execute(old_stmt)).scalars().all())

    # Stamp the new rows with the same supersede instant so every
    # downstream query sees one atomic transition.
    new_list = list(new_claims)
    for nc in new_list:
        nc.valid_from = ts
        nc.valid_to = None
        nc.updated_at = ts
        nc.created_at = nc.created_at or ts
        db.add(nc)

    # Close the old rows. Link each old row to a "best-match" new row
    # by positional index — when lengths match this is intuitive; when
    # they don't we round-robin so every old row has SOMETHING in
    # superseded_by rather than NULL ("dropped without successor").
    if old_rows:
        for idx, old in enumerate(old_rows):
            old.valid_to = ts
            old.updated_at = ts
            if new_list:
                old.superseded_by = new_list[idx % len(new_list)].id

        # SQLAlchemy emits one UPDATE per row above — for a few claims
        # that's negligible. If a synapse ever has hundreds of claims
        # the call site can switch to ``db.execute(update(...))`` in
        # bulk; deferring that until measured to matter.

    return {"superseded": len(old_rows), "added": len(new_list), "ts": ts}


async def supersede_claim_by_id(
    db: AsyncSession,
    *,
    old_claim_id: str,
    new_claim: SynapseClaim,
    transition_ts: str | None = None,
) -> dict:
    """Supersede exactly one claim by id.

    Convenience for incremental updates that touch one claim at a time.
    The new claim is inserted with ``valid_to=NULL`` and the old row
    gets its ``valid_to`` set to ``transition_ts``.
    """
    ts = transition_ts or _now_iso()
    old = await db.get(SynapseClaim, old_claim_id)
    if old is None:
        return {"superseded": 0, "added": 0, "ts": ts, "error": "old_not_found"}
    if old.valid_to is not None:
        return {
            "superseded": 0, "added": 0, "ts": ts,
            "error": "old_already_superseded",
        }

    new_claim.valid_from = ts
    new_claim.valid_to = None
    new_claim.updated_at = ts
    new_claim.created_at = new_claim.created_at or ts
    db.add(new_claim)

    old.valid_to = ts
    old.updated_at = ts
    old.superseded_by = new_claim.id
    return {"superseded": 1, "added": 1, "ts": ts}


async def close_synapse_claims(
    db: AsyncSession, synapse_id: str, *, transition_ts: str | None = None,
) -> int:
    """Mark every currently-valid claim of a synapse as no-longer-valid.

    Used when a synapse is rejected outright — we don't want stale
    claims to keep showing up as "current". Returns the number of rows
    closed. Idempotent on a synapse with no currently-valid claims.
    """
    ts = transition_ts or _now_iso()
    stmt = (
        update(SynapseClaim)
        .where(SynapseClaim.synapse_id == synapse_id)
        .where(SynapseClaim.valid_to.is_(None))
        .values(valid_to=ts, updated_at=ts)
    )
    result = await db.execute(stmt)
    # SQLAlchemy's CursorResult.rowcount works for our SQLite backend.
    return int(result.rowcount or 0)
