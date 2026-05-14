"""Synthesis — third stage of the synapse pipeline.

Takes a detected ``Community`` (a cluster of entities + their source
``KnowledgeItem``s) and asks the LLM to write one higher-order insight
node: a ``Synapse``.

The synthesis prompt forces *source citation* — every atomic claim the
model emits must point at the numbered source items it came from. Those
citations are carried into ``Synapse.extra_data["draft_claims"]`` and are
exactly what the Phase 4 validation pipeline needs (claim text + the
candidate source items to ground it against), so no separate
claim-decomposition call is required later.

A synapse is created with ``status="pending_validation"`` and
``verdict="human_review"`` — it is *not* trusted until validation runs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.knowledge import KnowledgeItem
from models.synapse import KnowledgeEntity, Synapse
from services.synapse_communities import Community
from services.synapse_llm import call_json, gen_id, merge_usage

logger = logging.getLogger("projecthub.synapse")

# Prompt-size guards — a community can pull in many items; cap what the
# LLM sees so the synthesis call stays within a sane token budget.
_MAX_SOURCE_ITEMS = 12
_MAX_ITEM_CHARS = 900
_MAX_ENTITY_NAMES = 20


_SYNTHESIS_PROMPT = """Du bist ein Wissens-Synthetisierer. Aus den folgenden zusammengehörigen Wissenseinträgen sollst du EINEN übergeordneten Erkenntnis-Knoten formulieren — eine Synthese, die über die Einzeleinträge hinausgeht und das gemeinsame Bild zeigt.

**Zentrale Konzepte dieses Clusters:** {entities}

**Quell-Einträge:**
{sources}

---

Formuliere:
1. einen prägnanten **Titel** (max 100 Zeichen),
2. eine **Synthese** (3-8 Sätze): das übergreifende Thema, Muster, Spannungen oder Schlussfolgerungen — NICHT nur eine Aufzählung der Einträge,
3. eine Liste **atomarer Aussagen** ("claims") — je eine überprüfbare Einzelaussage, jede mit den Quell-Nummern, auf die sie sich stützt.

Antworte AUSSCHLIESSLICH als valides JSON (kein Markdown, keine Erklärung):
{{
  "title": "...",
  "summary": "...",
  "claims": [
    {{"text": "Eine überprüfbare Einzelaussage", "sources": [1, 3]}}
  ]
}}

Regeln:
- Jede Aussage in "claims" MUSS mindestens eine Quell-Nummer in "sources" haben.
- Verwende nur Quell-Nummern, die oben wirklich vorkommen.
- Erfinde nichts, das nicht aus den Quell-Einträgen hervorgeht.
- Wenn die Einträge keinen sinnvollen gemeinsamen Erkenntniswert haben: "claims" leer lassen."""


@dataclass
class SynthesisStats:
    synapses_created: int = 0
    communities_skipped: int = 0
    usage: dict = field(default_factory=dict)


async def synthesise_communities(
    db: AsyncSession,
    project_id: str,
    communities: list[Community],
    run_id: str | None,
    *,
    stats: SynthesisStats | None = None,
) -> tuple[list[Synapse], SynthesisStats]:
    """Synthesise every community into a draft ``Synapse``.

    Returns the created (un-validated) synapses plus run stats. The caller
    owns the DB session; rows are flushed per community and committed at
    the end.
    """
    stats = stats or SynthesisStats()
    created: list[Synapse] = []

    for community in communities:
        try:
            synapse = await _synthesise_one(db, project_id, community, run_id, stats)
        except Exception as e:  # one bad community must not kill the run
            logger.warning("synthesis failed for a community: %s", e)
            synapse = None
        if synapse is None:
            stats.communities_skipped += 1
        else:
            created.append(synapse)
            stats.synapses_created += 1

    await db.commit()
    return created, stats


async def _synthesise_one(
    db: AsyncSession,
    project_id: str,
    community: Community,
    run_id: str | None,
    stats: SynthesisStats,
) -> Synapse | None:
    """Synthesise a single community → one draft Synapse (or None if empty)."""
    # Load source items, capped and ordered by substance (longest content first).
    items_res = await db.execute(
        select(KnowledgeItem).where(KnowledgeItem.id.in_(community.item_ids))
    )
    items = list(items_res.scalars().all())
    if not items:
        return None
    items.sort(key=lambda it: len(it.content_plain or ""), reverse=True)
    items = items[:_MAX_SOURCE_ITEMS]

    # Load entity names for the prompt header.
    entity_res = await db.execute(
        select(KnowledgeEntity.name).where(
            KnowledgeEntity.id.in_(community.entity_ids)
        )
    )
    entity_names = [row[0] for row in entity_res.all()][:_MAX_ENTITY_NAMES]

    # Number the sources 1..N — the LLM cites by number, we map back to id.
    index_to_item_id: dict[int, str] = {}
    source_lines: list[str] = []
    for i, item in enumerate(items, start=1):
        index_to_item_id[i] = item.id
        body = (item.content_plain or "").strip()[:_MAX_ITEM_CHARS]
        source_lines.append(f"[{i}] {item.title}\n{body}")

    prompt = _SYNTHESIS_PROMPT.format(
        entities=", ".join(entity_names) or "(keine)",
        sources="\n\n".join(source_lines),
    )
    res = await call_json(prompt, session_prefix="synthesis")
    merge_usage(stats.usage, res.usage)

    if not res.ok or not isinstance(res.parsed, dict):
        return None

    title = str(res.parsed.get("title") or "").strip()
    summary = str(res.parsed.get("summary") or "").strip()
    if not title or not summary:
        return None

    draft_claims = _map_claims(res.parsed.get("claims"), index_to_item_id)
    if not draft_claims:
        # No grounded claims → nothing the validation pipeline could verify.
        return None

    synapse = Synapse(
        id=gen_id(),
        project_id=project_id,
        generation_run_id=run_id,
        title=title[:300],
        summary=f"<p>{summary}</p>",
        summary_plain=summary[:8000],
        community_level=community.level,
        confidence=0.0,
        confidence_band="low",
        verdict="human_review",          # not trusted until validation runs
        status="pending_validation",
    )
    synapse.source_item_ids_list = [it.id for it in items]
    synapse.source_entity_ids_list = list(community.entity_ids)
    synapse.extra_data_dict = {
        "draft_claims": draft_claims,
        "synthesis_model": res.model,
    }
    db.add(synapse)
    return synapse


def _map_claims(
    raw_claims: object,
    index_to_item_id: dict[int, str],
) -> list[dict]:
    """Validate claims and map their source numbers back to item ids.

    Drops claims with no text or no resolvable source — an un-grounded
    claim has nothing for the validation pipeline to check.
    """
    if not isinstance(raw_claims, list):
        return []
    out: list[dict] = []
    for claim in raw_claims:
        if not isinstance(claim, dict):
            continue
        text = str(claim.get("text") or "").strip()
        if not text:
            continue
        source_item_ids: list[str] = []
        for num in claim.get("sources") or []:
            try:
                item_id = index_to_item_id.get(int(num))
            except (TypeError, ValueError):
                continue
            if item_id and item_id not in source_item_ids:
                source_item_ids.append(item_id)
        if not source_item_ids:
            continue
        out.append({"text": text, "source_item_ids": source_item_ids})
    return out
