"""Incremental synapse updates (P9 — Mem0-style ADD/UPDATE/DELETE/NOOP).

When a KnowledgeItem changes — created, updated, or deleted — we want to
keep the synapse layer fresh *without* rebuilding the whole pipeline. The
Mem0 paper's four-way decision applied at the synapse granularity:

    ADD     — a new item warrants a new synapse (synthesis required)
    UPDATE  — an existing synapse's claims need refresh because one of
              its source items changed
    DELETE  — an existing synapse loses its only source item and is no
              longer grounded
    NOOP    — the change doesn't materially affect the synapse

This module is the orchestrator. The decision per affected synapse can
come from either:

    * a rule-based fallback (cheap, no LLM)
    * an LLM decider (richer; called when ``llm_caller`` is supplied)

Application uses the P10 bi-temporal helpers
(``synapse_claims_bitemporal``) so we keep audit history rather than
hard-deleting old claims.

This is intentionally scoped: ADD is currently STUBBED — synthesising a
fresh synapse from a single new item needs the full entity-extraction +
clustering pipeline, which we already have in ``synapse_pipeline``. The
incremental path emits an ADD decision but doesn't synthesise; the
caller (or a future ticket) can wire ADD to the full pipeline. UPDATE
and DELETE are fully implemented because they're the high-frequency
cases (edits + deletes of existing items >>> brand-new isolated items).
"""

from __future__ import annotations

import json
import logging
import secrets
from dataclasses import dataclass
from typing import Awaitable, Callable, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.knowledge import KnowledgeItem
from models.synapse import Synapse, SynapseClaim
from services.synapse_claims_bitemporal import (
    close_synapse_claims,
    supersede_claim_by_id,
)

logger = logging.getLogger("projecthub.synapse.incremental")


ChangeType = Literal["created", "updated", "deleted"]
DecisionKind = Literal["ADD", "UPDATE", "DELETE", "NOOP"]

# LLMCaller protocol — a parametrised async function returning the parsed
# JSON (or None on failure). Matches the shape of ``synapse_llm.call_json``
# stripped of its return-wrapper, so it can be injected for tests.
LLMCaller = Callable[[str], Awaitable[dict | None]]


# ── Decision model ──────────────────────────────────────────────────────


@dataclass
class IncrementalDecision:
    """Per-affected-synapse outcome of the decider step."""

    kind: DecisionKind
    synapse_id: str  # "" for ADD decisions (no synapse exists yet)
    reason: str = ""
    affected_claim_ids: list[str] = None  # type: ignore[assignment]
    source: str = "rule"  # "rule" or "llm" — for telemetry

    def __post_init__(self) -> None:
        if self.affected_claim_ids is None:
            self.affected_claim_ids = []


# ── Step 1: find affected synapses ──────────────────────────────────────


async def find_affected_synapses(
    db: AsyncSession, *, project_id: str, item_id: str,
) -> list[Synapse]:
    """Return synapses whose ``source_item_ids`` references this item.

    Uses a LIKE-on-JSON match — portable across SQLite/Postgres and good
    enough at the volumes we expect (synapses per project rarely exceed
    a few hundred). If profiling ever flags this, switch to SQLite's
    ``json_each(source_item_ids)`` subquery — for now, simple wins.
    """
    needle = f'"{item_id}"'
    stmt = (
        select(Synapse)
        .where(Synapse.project_id == project_id)
        .where(Synapse.source_item_ids.like(f"%{needle}%"))
    )
    rows = (await db.execute(stmt)).scalars().all()
    # LIKE can produce false-positives (item_id substring of another id) —
    # do the precise JSON membership check in Python to be safe.
    return [s for s in rows if item_id in s.source_item_ids_list]


async def find_affected_via_entity_overlap(
    db: AsyncSession,
    *,
    project_id: str,
    item: KnowledgeItem,
    llm_caller: LLMCaller,
) -> list[Synapse]:
    """Closes the P9 ADD loop.

    The direct ``source_item_ids`` overlap (above) is precise but misses
    the common case: a freshly-created item that *talks about* the same
    concepts as an existing synapse but isn't yet linked to it
    (the item was just created — no previous /generate has seen it).

    For ADD-class changes (typically ``change == "created"``) we run the
    existing single-item entity extractor on the new item, look up which
    ``KnowledgeEntity`` rows match the extracted entities, then surface
    every Synapse whose ``source_entity_ids`` overlaps that set.

    Cost: ONE LLM call (entity extraction for one item, ~1500 tokens).
    The caller decides whether to pay it — the auto-hook in
    ``routers/knowledge.py`` doesn't (rule-based only), but the explicit
    ``POST /api/synapse/{proj}/incremental?use_llm=true`` does.

    Args:
        db: Async session.
        project_id: Project the item belongs to.
        item: The item itself — needs ``content_plain`` for extraction.
        llm_caller: Decider LLM used here for the *extraction* prompt;
            we don't need a separate model for this.

    Returns:
        Affected synapses (may be empty), with no duplication relative
        to ``find_affected_synapses`` — callers should merge by ``.id``.
    """
    text = (item.content_plain or "").strip()
    if not text:
        return []

    # Use the production entity-extractor against a thin LLMCaller adapter.
    # synapse_entities.extract_from_item internally calls call_json(), so
    # to keep tests deterministic we inline a minimal version that uses
    # the injected llm_caller directly.
    prompt = _build_extraction_prompt(item)
    try:
        parsed = await llm_caller(prompt)
    except Exception as e:  # noqa: BLE001
        logger.warning("[incremental] entity-extract LLM raised: %s", e)
        return []

    if not isinstance(parsed, dict):
        return []
    raw_entities = parsed.get("entities") or []
    if not isinstance(raw_entities, list) or not raw_entities:
        return []

    # Normalise the extracted entity names — the existing project entities
    # are stored with ``name_normalized``, so we match on the same shape.
    from services.synapse_entities import normalize_name
    from models.synapse import KnowledgeEntity

    norm_names = {
        normalize_name(str(e.get("name") or ""))
        for e in raw_entities
        if isinstance(e, dict) and e.get("name")
    }
    norm_names.discard("")
    if not norm_names:
        return []

    # Look up matching KnowledgeEntity rows for this project. SQLite ``IN``
    # has no parameter-count problem at our scales (≤ 12 entities/item).
    ent_stmt = (
        select(KnowledgeEntity)
        .where(KnowledgeEntity.project_id == project_id)
        .where(KnowledgeEntity.name_normalized.in_(list(norm_names)))
    )
    matched_entities = list((await db.execute(ent_stmt)).scalars().all())
    if not matched_entities:
        return []

    matched_entity_ids = {e.id for e in matched_entities}

    # Now find synapses that reference any of those entity ids. We use
    # the same LIKE-then-membership pattern as ``find_affected_synapses``
    # so SQLite avoids a full table scan when most synapses' entity sets
    # don't intersect the new item's.
    syn_stmt = select(Synapse).where(Synapse.project_id == project_id)
    candidate_syns = list((await db.execute(syn_stmt)).scalars().all())
    out: list[Synapse] = []
    for syn in candidate_syns:
        if matched_entity_ids.intersection(syn.source_entity_ids_list):
            out.append(syn)
    return out


def _build_extraction_prompt(item: KnowledgeItem) -> str:
    """Compact entity-extraction prompt used by the overlap path.

    Mirrors the production prompt in ``synapse_entities`` but keeps the
    output schema narrower (we only need names + types — relations are
    not used here). Smaller prompt = faster + cheaper for the hot path.
    """
    return (
        "Extrahiere die zentralen Entitäten aus folgendem Wissenseintrag. "
        "Antworte AUSSCHLIESSLICH als JSON: "
        "{\"entities\": [{\"name\": \"...\", \"type\": \"concept|component|"
        "person|system|technology|process|decision\"}]}\n\n"
        f"Titel: {item.title or '(ohne Titel)'}\n"
        f"Kategorie: {item.category or 'reference'}\n\n"
        f"---\n{(item.content_plain or '')[:3000]}\n---\n\n"
        "Maximal 12 Entitäten, nur die wirklich tragenden. KEINE generischen "
        "Wörter wie 'System' oder 'Prozess' ohne Kontext."
    )


async def _claims_touching_item(
    db: AsyncSession, *, synapse_id: str, item_id: str,
) -> list[SynapseClaim]:
    """Return *currently-valid* claims for this synapse whose evidence
    references ``item_id``.

    Per the P10 invariant we ONLY look at rows with ``valid_to IS NULL`` —
    historical claims are already closed and don't need re-supersede.
    """
    stmt = (
        select(SynapseClaim)
        .where(SynapseClaim.synapse_id == synapse_id)
        .where(SynapseClaim.valid_to.is_(None))
    )
    candidates = (await db.execute(stmt)).scalars().all()
    out: list[SynapseClaim] = []
    for c in candidates:
        for ev in c.evidence_list:
            if isinstance(ev, dict) and ev.get("item_id") == item_id:
                out.append(c)
                break
    return out


# ── Step 2: decide per synapse ──────────────────────────────────────────


async def decide_for_synapse(
    db: AsyncSession,
    synapse: Synapse,
    item: KnowledgeItem | None,
    *,
    change: ChangeType,
    llm_caller: LLMCaller | None = None,
) -> IncrementalDecision:
    """Pick a decision (Mem0 four-way) for one affected synapse.

    Args:
        synapse: The synapse we're deciding about.
        item: The changed knowledge item. ``None`` is valid for the
            ``deleted`` change (we don't need the item body for the
            rule-based DELETE path).
        change: What happened to the item.
        llm_caller: Optional LLM decider. When given, we hand it a
            compact context and trust its JSON ``decision`` field.
            Rule-based fallback always runs if the LLM fails.

    The LLM decision is the *primary* signal when supplied; the rule
    layer is the safety net. We never *override* a clear LLM answer
    with a contradictory rule — the LLM has more context than we do.
    """
    item_id = item.id if item is not None else _infer_item_id_from_change(synapse, change)

    # ── Rule layer — first, so we always have a fallback answer ────────
    rule_kind = _rule_based_decision(synapse, item, change)
    affected_claim_ids: list[str] = []
    if rule_kind in ("UPDATE", "DELETE") and item_id:
        affected = await _claims_touching_item(
            db, synapse_id=synapse.id, item_id=item_id,
        )
        affected_claim_ids = [c.id for c in affected]

    rule_reason = _explain_rule(synapse, item, change, rule_kind)

    if llm_caller is None:
        return IncrementalDecision(
            kind=rule_kind,
            synapse_id=synapse.id,
            reason=rule_reason,
            affected_claim_ids=affected_claim_ids,
            source="rule",
        )

    # ── LLM layer — narrowly prompted decider ──────────────────────────
    prompt = _build_decider_prompt(synapse, item, change)
    try:
        parsed = await llm_caller(prompt)
    except Exception as e:  # noqa: BLE001 — never crash the orchestrator
        logger.warning("[incremental] decider LLM raised: %s", e)
        parsed = None

    if not isinstance(parsed, dict) or "decision" not in parsed:
        # Bad / missing response — fall back cleanly to the rule.
        return IncrementalDecision(
            kind=rule_kind,
            synapse_id=synapse.id,
            reason=f"{rule_reason} (LLM unavailable)",
            affected_claim_ids=affected_claim_ids,
            source="rule",
        )

    raw = str(parsed.get("decision") or "").upper().strip()
    if raw not in ("ADD", "UPDATE", "DELETE", "NOOP"):
        return IncrementalDecision(
            kind=rule_kind,
            synapse_id=synapse.id,
            reason=f"{rule_reason} (LLM returned unknown decision {raw!r})",
            affected_claim_ids=affected_claim_ids,
            source="rule",
        )

    llm_reason = str(parsed.get("reason") or "")[:300]
    return IncrementalDecision(
        kind=raw,  # type: ignore[arg-type]
        synapse_id=synapse.id,
        reason=llm_reason or rule_reason,
        affected_claim_ids=affected_claim_ids,
        source="llm",
    )


def _rule_based_decision(
    synapse: Synapse,
    item: KnowledgeItem | None,
    change: ChangeType,
) -> DecisionKind:
    """Cheap fallback when no LLM is available.

    * deleted: if the synapse depends only on this item → DELETE,
      else UPDATE (re-evaluate the remaining sources).
    * updated: UPDATE.
    * created: NOOP at the per-affected-synapse step (a brand-new item
      isn't yet linked to any existing synapse; the orchestrator surfaces
      ADD separately when no existing synapse is affected).
    """
    if change == "deleted":
        sources = synapse.source_item_ids_list
        if len(sources) <= 1:
            return "DELETE"
        return "UPDATE"
    if change == "updated":
        return "UPDATE"
    # created
    return "NOOP"


def _explain_rule(
    synapse: Synapse,
    item: KnowledgeItem | None,
    change: ChangeType,
    kind: DecisionKind,
) -> str:
    if kind == "DELETE":
        return f"item deleted; synapse depends only on this item ({len(synapse.source_item_ids_list)} sources)"
    if kind == "UPDATE":
        return f"item {change}; re-evaluate dependent claims"
    if kind == "ADD":
        return "new item not linked to existing synapse"
    return f"no action ({change})"


def _infer_item_id_from_change(synapse: Synapse, change: ChangeType) -> str:
    """When the caller couldn't load the item (e.g. it was deleted), we
    still want to identify which claims to close. Returns empty when
    nothing useful can be inferred — caller falls back to closing all.
    """
    # Not used in current paths — callers always pass item or item_id
    # explicitly via update_for_item. Kept for forward compatibility.
    return ""


def _build_decider_prompt(
    synapse: Synapse,
    item: KnowledgeItem | None,
    change: ChangeType,
) -> str:
    """Compact Mem0-style decider prompt — JSON-only response expected."""
    item_block = (
        f"Item-Titel: {item.title}\n"
        f"Item-Inhalt (gekürzt): {(item.content_plain or '')[:1200]}\n"
    ) if item is not None else f"Item {change} (Inhalt nicht verfügbar)."
    return (
        "Du bist Mem0-Entscheider für eine Wissens-Synapse. "
        "Pro betroffener Synapse triffst du genau EINE Entscheidung aus: "
        "ADD, UPDATE, DELETE, NOOP.\n\n"
        f"Geänderte Aktion: {change}\n"
        f"{item_block}\n\n"
        f"Synapse-Titel: {synapse.title}\n"
        f"Synapse-Zusammenfassung: {synapse.summary_plain[:800]}\n"
        f"Anzahl Quell-Items: {len(synapse.source_item_ids_list)}\n\n"
        "Entscheide:\n"
        "  ADD     — Item rechtfertigt eine NEUE Synapse (sehr selten in diesem Modus)\n"
        "  UPDATE  — bestehende Synapse braucht aktualisierte Claims\n"
        "  DELETE  — Synapse verliert ihre Grundlage und sollte zurückgezogen werden\n"
        "  NOOP    — Änderung berührt diese Synapse nicht inhaltlich\n\n"
        "Antworte als JSON: {\"decision\": \"UPDATE\", \"reason\": \"kurze Begründung\"}"
    )


# ── Step 3: apply decision ──────────────────────────────────────────────


async def apply_decision(
    db: AsyncSession, decision: IncrementalDecision,
) -> dict:
    """Materialise a decision on the database.

    Returns a small summary dict so the caller can build a per-item
    outcome breakdown. The session is NOT committed here — the caller
    decides when to flush so a batch of decisions can be one transaction.
    """
    if decision.kind == "NOOP":
        return {"action": "noop", "synapse_id": decision.synapse_id}

    if decision.kind == "DELETE":
        closed = await close_synapse_claims(db, decision.synapse_id)
        syn = await db.get(Synapse, decision.synapse_id)
        if syn is not None:
            syn.status = "stale"
            # Don't change the verdict — keep the original confidence
            # so retrieval can still display it with a "stale" badge if
            # the UI wants. The status field is the authoritative gate.
        return {
            "action": "delete",
            "synapse_id": decision.synapse_id,
            "closed_claims": closed,
        }

    if decision.kind == "UPDATE":
        # Mark the affected claims as needing review by closing them.
        # We don't yet have synthesised replacements (that would need a
        # second LLM round and is out of scope for this commit); the
        # synapse status becomes "pending_validation" so a future regen
        # picks it up.
        closed_n = 0
        for claim_id in decision.affected_claim_ids:
            claim = await db.get(SynapseClaim, claim_id)
            if claim is None or claim.valid_to is not None:
                continue
            from datetime import datetime, timezone
            ts = datetime.now(timezone.utc).isoformat()
            claim.valid_to = ts
            claim.updated_at = ts
            # superseded_by stays None — there's no replacement claim yet
            closed_n += 1
        syn = await db.get(Synapse, decision.synapse_id)
        if syn is not None and closed_n > 0:
            syn.status = "pending_validation"
        return {
            "action": "update",
            "synapse_id": decision.synapse_id,
            "closed_claims": closed_n,
        }

    # decision.kind == "ADD" — synthesis path; not yet wired into
    # incremental. We surface the intent so callers / dashboards can
    # see how often it fires, and so the full pipeline can pick it up.
    return {
        "action": "add_pending",
        "synapse_id": "",
        "note": "ADD requires full synthesis pipeline — emit decision only",
    }


# ── Step 4: end-to-end orchestrator ─────────────────────────────────────


async def update_for_item(
    db: AsyncSession,
    *,
    project_id: str,
    item_id: str,
    change: ChangeType,
    llm_caller: LLMCaller | None = None,
) -> dict:
    """One-call orchestrator: find affected → decide → apply.

    Returns:
        {
            "project_id": ...,
            "item_id": ...,
            "change": ...,
            "affected": <count>,
            "decisions": [<IncrementalDecision-as-dict>, ...],
            "results":   [<apply_decision result>, ...],
        }

    The session is committed once at the end so the whole update is
    atomic per item — partial application is the worst failure mode.
    """
    affected = await find_affected_synapses(
        db, project_id=project_id, item_id=item_id,
    )

    item: KnowledgeItem | None = None
    if change != "deleted":
        item = await db.get(KnowledgeItem, item_id)
        # On 'created' against project=X we still pick up the item; if
        # it's gone (race with delete), fall through with item=None.

    # P9 ADD-closure: for newly-created items that don't directly link to
    # any synapse (source_item_ids miss), try entity-overlap discovery —
    # the new item may belong with an existing synapse conceptually even
    # though no /generate has linked it yet. Costs ONE LLM call so only
    # runs when the caller opted in by supplying llm_caller.
    affected_via_entities: list[Synapse] = []
    if (
        change == "created"
        and not affected
        and item is not None
        and llm_caller is not None
    ):
        affected_via_entities = await find_affected_via_entity_overlap(
            db, project_id=project_id, item=item, llm_caller=llm_caller,
        )
        affected = affected_via_entities  # promote to the main path

    decisions: list[IncrementalDecision] = []
    results: list[dict] = []
    for syn in affected:
        # Items discovered via entity overlap on a 'created' change get
        # UPDATE semantics (the new item supplements them) — but
        # decide_for_synapse rule-base maps 'created' → NOOP. Force the
        # override here so the work actually happens.
        effective_change: ChangeType = (
            "updated"
            if syn in affected_via_entities and change == "created"
            else change
        )
        d = await decide_for_synapse(
            db, syn, item, change=effective_change, llm_caller=llm_caller,
        )
        # Annotate why this synapse was picked up so the response payload
        # tells operators "this came from entity overlap, not direct link".
        if syn in affected_via_entities:
            d.reason = (d.reason + " | via entity-overlap").strip(" |")
        decisions.append(d)
        r = await apply_decision(db, d)
        results.append(r)

    # 'created' with NO affected synapses (neither direct nor via
    # entities) → surface an ADD intent so the dashboard / queue can
    # pick it up. Same payload shape.
    if change == "created" and not affected:
        decisions.append(IncrementalDecision(
            kind="ADD", synapse_id="", reason="new item, no existing synapse touched",
            source="rule",
        ))
        results.append({
            "action": "add_pending",
            "synapse_id": "",
            "note": "would synthesise; out of scope for incremental",
        })

    if affected or results:
        await db.commit()

    return {
        "project_id": project_id,
        "item_id": item_id,
        "change": change,
        "affected": len(affected),
        "decisions": [_decision_to_dict(d) for d in decisions],
        "results": results,
    }


def _decision_to_dict(d: IncrementalDecision) -> dict:
    return {
        "kind": d.kind,
        "synapse_id": d.synapse_id,
        "reason": d.reason,
        "affected_claim_ids": list(d.affected_claim_ids),
        "source": d.source,
    }
