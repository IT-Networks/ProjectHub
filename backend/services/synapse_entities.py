"""Entity extraction & resolution — first stage of the synapse pipeline.

For each ``KnowledgeItem`` in a project an LLM extracts the *entities*
(concepts, components, systems, decisions, …) and the *relations* between
them. Entities are then resolved — surface variants merged into one
``KnowledgeEntity`` row — so the downstream community detection (Phase 2)
operates on a clean concept graph rather than raw strings.

Resolution without embeddings (Spike 2026-05-14: no local ML stack) is a
three-tier cascade, cheapest first:

    1. exact match on ``name_normalized``
    2. fuzzy match — token-set Jaccard ≥ AUTO_MERGE_JACCARD → auto-merge
    3. ambiguous band (AMBIGUOUS_JACCARD … AUTO_MERGE_JACCARD) → one
       batched LLM adjudication call per item

A generation run does a *clean rebuild*: ``clear_project_entities`` wipes
the entity layer first, so within one run the in-memory ``_EntityIndex``
only ever contains entities created by that same run.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.knowledge import KnowledgeItem
from models.synapse import (
    KnowledgeEntity, KnowledgeEntityMention, KnowledgeEntityRelation,
)
from services.synapse_llm import call_json, gen_id, merge_usage

logger = logging.getLogger("projecthub.synapse")

# Entity types the extractor is allowed to emit; anything else → "concept".
VALID_ENTITY_TYPES = {
    "concept", "component", "person", "system",
    "technology", "process", "decision",
}

# Resolution thresholds (token-set Jaccard over normalized names).
AUTO_MERGE_JACCARD = 0.85   # ≥ this → silently merge into the existing entity
AMBIGUOUS_JACCARD = 0.55    # [this, AUTO_MERGE) → ask the LLM to adjudicate

# Truncation budget for an item's text in the extraction prompt.
_MAX_ITEM_CHARS = 4000


_EXTRACTION_PROMPT = """Du bist ein Wissens-Analyst. Extrahiere aus dem folgenden Wissenseintrag die zentralen Entitäten und ihre Beziehungen.

**Titel:** {title}
**Kategorie:** {category}

---
{content}
---

Eine *Entität* ist ein benanntes Konzept, eine Komponente, ein System, eine Technologie, ein Prozess, eine Entscheidung oder eine Person — etwas, das auch in anderen Einträgen vorkommen könnte. Keine generischen Wörter.

Antworte AUSSCHLIESSLICH als valides JSON (kein Markdown, keine Erklärung):
{{
  "entities": [
    {{"name": "Prägnanter Entitätsname", "type": "concept|component|person|system|technology|process|decision", "description": "Ein Satz, was es ist"}}
  ],
  "relations": [
    {{"source": "Entitätsname", "target": "Entitätsname", "description": "Wie sie zusammenhängen"}}
  ]
}}

Regeln:
- Maximal 12 Entitäten, nur die wirklich tragenden.
- "source" und "target" in "relations" MÜSSEN exakt einem "name" aus "entities" entsprechen.
- Wenn der Eintrag inhaltlich leer ist: leere Listen zurückgeben."""


_ADJUDICATION_PROMPT = """Du prüfst, ob neu extrahierte Entitäten mit bereits bekannten identisch sind (nur Schreibvarianten / Synonyme).

**Kontext (Eintrag):** {title}

Für jeden Fall: ist die neue Entität DASSELBE wie einer der Kandidaten, oder etwas Eigenständiges?

{cases}

Antworte AUSSCHLIESSLICH als valides JSON — eine Entscheidung pro Fall, in derselben Reihenfolge:
{{
  "decisions": [
    {{"case": 1, "same_as": "<exakter Kandidatenname oder null>"}}
  ]
}}
"same_as": null bedeutet eigenständige neue Entität."""


# --- Text helpers -----------------------------------------------------------

def normalize_name(name: str) -> str:
    """Lowercase, collapse whitespace — the dedupe/lookup key."""
    return " ".join(name.lower().split())


def _token_set(normalized: str) -> frozenset[str]:
    return frozenset(t for t in normalized.split() if len(t) > 1)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if not inter:
        return 0.0
    return inter / len(a | b)


# --- In-memory index over the entities created so far this run -------------

class _EntityIndex:
    def __init__(self) -> None:
        self._by_norm: dict[str, KnowledgeEntity] = {}
        self._all: list[KnowledgeEntity] = []

    def add(self, entity: KnowledgeEntity) -> None:
        self._by_norm[entity.name_normalized] = entity
        self._all.append(entity)

    def exact(self, norm: str) -> KnowledgeEntity | None:
        return self._by_norm.get(norm)

    def fuzzy(self, norm: str, threshold: float) -> list[tuple[KnowledgeEntity, float]]:
        toks = _token_set(norm)
        scored = [
            (e, _jaccard(toks, _token_set(e.name_normalized)))
            for e in self._all
        ]
        out = [(e, s) for e, s in scored if s >= threshold]
        out.sort(key=lambda x: x[1], reverse=True)
        return out


@dataclass
class ExtractionStats:
    items_processed: int = 0
    entities_created: int = 0
    mentions_created: int = 0
    relations_created: int = 0
    items_failed: int = 0
    usage: dict = field(default_factory=dict)


# --- Public API -------------------------------------------------------------

async def clear_project_entities(db: AsyncSession, project_id: str) -> None:
    """Wipe the entity layer for a project — called before a clean rebuild.

    Deletes relations and mentions explicitly (SQLite FK cascade is not
    reliably enabled), then the entities themselves.
    """
    await db.execute(
        delete(KnowledgeEntityRelation).where(
            KnowledgeEntityRelation.project_id == project_id
        )
    )
    # Mentions are keyed by entity_id, not project_id — scope via subquery.
    entity_ids_subq = select(KnowledgeEntity.id).where(
        KnowledgeEntity.project_id == project_id
    )
    await db.execute(
        delete(KnowledgeEntityMention).where(
            KnowledgeEntityMention.entity_id.in_(entity_ids_subq)
        )
    )
    await db.execute(
        delete(KnowledgeEntity).where(KnowledgeEntity.project_id == project_id)
    )
    await db.commit()


async def extract_from_item(item: KnowledgeItem) -> tuple[list[dict], list[dict], dict]:
    """Run the LLM extractor on one item.

    Returns ``(entities, relations, usage)`` — entities/relations are the
    sanitised raw dicts from the LLM, ``usage`` is the call's token usage.
    On any failure both lists are empty.
    """
    content = (item.content_plain or "").strip()
    if not content:
        return [], [], {}

    prompt = _EXTRACTION_PROMPT.format(
        title=item.title or "(ohne Titel)",
        category=item.category or "reference",
        content=content[:_MAX_ITEM_CHARS],
    )
    res = await call_json(prompt, session_prefix="entity-extract")
    if not res.ok or not isinstance(res.parsed, dict):
        return [], [], res.usage

    raw_entities = res.parsed.get("entities") or []
    raw_relations = res.parsed.get("relations") or []
    entities = _sanitise_entities(raw_entities)
    relations = _sanitise_relations(raw_relations)
    return entities, relations, res.usage


async def extract_project_entities(
    db: AsyncSession,
    project_id: str,
    *,
    stats: ExtractionStats | None = None,
) -> ExtractionStats:
    """Extract & resolve entities for every KnowledgeItem in a project.

    Caller (the pipeline orchestrator) owns the DB session and the clean
    rebuild — this assumes ``clear_project_entities`` already ran.
    """
    stats = stats or ExtractionStats()

    items_res = await db.execute(
        select(KnowledgeItem).where(KnowledgeItem.project_id == project_id)
    )
    items = list(items_res.scalars().all())

    index = _EntityIndex()
    # (source_id, target_id) -> [descriptions]  — accumulated, persisted once.
    relation_acc: dict[tuple[str, str], list[str]] = {}

    for item in items:
        try:
            entities, relations, usage = await extract_from_item(item)
        except Exception as e:  # never let one bad item kill the run
            logger.warning("entity extraction failed for item %s: %s", item.id, e)
            stats.items_failed += 1
            continue

        merge_usage(stats.usage, usage)
        stats.items_processed += 1

        name_to_id = await _resolve_item_entities(
            db, project_id, item, entities, index, stats
        )

        for rel in relations:
            sid = name_to_id.get(normalize_name(rel["source"]))
            tid = name_to_id.get(normalize_name(rel["target"]))
            if sid and tid and sid != tid:
                relation_acc.setdefault((sid, tid), []).append(rel["description"])

        # Commit per item so a crash mid-run still leaves a consistent prefix.
        await db.commit()

    await _persist_relations(db, project_id, relation_acc, stats)
    await db.commit()
    return stats


# --- Internal: per-item entity resolution -----------------------------------

async def _resolve_item_entities(
    db: AsyncSession,
    project_id: str,
    item: KnowledgeItem,
    entities: list[dict],
    index: _EntityIndex,
    stats: ExtractionStats,
) -> dict[str, str]:
    """Resolve one item's extracted entities to KnowledgeEntity ids.

    Returns ``{name_normalized: entity_id}`` for this item's entities so
    the caller can map relations. Creates mentions as a side effect.
    """
    name_to_id: dict[str, str] = {}
    # Cases that need LLM adjudication: (raw_entity, norm, candidates).
    ambiguous: list[tuple[dict, str, list[tuple[KnowledgeEntity, float]]]] = []

    for raw in entities:
        norm = normalize_name(raw["name"])
        if not norm or norm in name_to_id:
            continue  # empty or duplicate within this item

        entity = index.exact(norm)
        if entity is None:
            cands = index.fuzzy(norm, AMBIGUOUS_JACCARD)
            if cands and cands[0][1] >= AUTO_MERGE_JACCARD:
                entity = cands[0][0]
            elif cands:
                ambiguous.append((raw, norm, cands))
                continue  # defer — decided after the adjudication call

        entity = entity or _create_entity(db, project_id, raw, index, stats)
        _add_mention(db, entity, item, stats)
        name_to_id[norm] = entity.id

    if ambiguous:
        decisions = await _llm_adjudicate(item, ambiguous, stats)
        for (raw, norm, cands), same_as in zip(ambiguous, decisions):
            entity = None
            if same_as:
                same_norm = normalize_name(same_as)
                entity = index.exact(same_norm) or next(
                    (e for e, _ in cands if e.name_normalized == same_norm), None
                )
            entity = entity or _create_entity(db, project_id, raw, index, stats)
            if norm not in name_to_id:
                _add_mention(db, entity, item, stats)
                name_to_id[norm] = entity.id

    return name_to_id


def _create_entity(
    db: AsyncSession,
    project_id: str,
    raw: dict,
    index: _EntityIndex,
    stats: ExtractionStats,
) -> KnowledgeEntity:
    entity = KnowledgeEntity(
        id=gen_id(),
        project_id=project_id,
        name=raw["name"][:200],
        name_normalized=normalize_name(raw["name"])[:200],
        entity_type=raw["type"],
        description=raw["description"][:1000],
        mention_count=0,
    )
    db.add(entity)
    index.add(entity)
    stats.entities_created += 1
    return entity


def _add_mention(
    db: AsyncSession,
    entity: KnowledgeEntity,
    item: KnowledgeItem,
    stats: ExtractionStats,
) -> None:
    db.add(KnowledgeEntityMention(
        id=gen_id(),
        entity_id=entity.id,
        item_id=item.id,
    ))
    entity.mention_count += 1
    stats.mentions_created += 1


async def _llm_adjudicate(
    item: KnowledgeItem,
    ambiguous: list[tuple[dict, str, list[tuple[KnowledgeEntity, float]]]],
    stats: ExtractionStats,
) -> list[str | None]:
    """One batched call deciding, per ambiguous entity, if it's a known one.

    Returns a list aligned with ``ambiguous``: each element is a canonical
    candidate name (→ merge) or ``None`` (→ create new). On any failure
    every case falls back to ``None`` — a spurious duplicate is cheaper
    than a wrong merge.
    """
    case_lines = []
    for i, (raw, _norm, cands) in enumerate(ambiguous, start=1):
        cand_names = ", ".join(f'"{e.name}"' for e, _ in cands[:5])
        case_lines.append(
            f'Fall {i}: neue Entität "{raw["name"]}" ({raw["description"]}) '
            f'— Kandidaten: {cand_names}'
        )

    prompt = _ADJUDICATION_PROMPT.format(
        title=item.title or "(ohne Titel)",
        cases="\n".join(case_lines),
    )
    res = await call_json(prompt, session_prefix="entity-adjudicate")
    merge_usage(stats.usage, res.usage)

    fallback: list[str | None] = [None] * len(ambiguous)
    if not res.ok or not isinstance(res.parsed, dict):
        return fallback

    decisions = res.parsed.get("decisions")
    if not isinstance(decisions, list):
        return fallback

    out: list[str | None] = list(fallback)
    for dec in decisions:
        if not isinstance(dec, dict):
            continue
        try:
            idx = int(dec.get("case", 0)) - 1
        except (TypeError, ValueError):
            continue
        if not (0 <= idx < len(out)):
            continue
        same_as = dec.get("same_as")
        if isinstance(same_as, str) and same_as.strip():
            # Only honour it if it actually names one of that case's candidates.
            valid = {e.name for e, _ in ambiguous[idx][2]}
            if same_as in valid:
                out[idx] = same_as
    return out


async def _persist_relations(
    db: AsyncSession,
    project_id: str,
    relation_acc: dict[tuple[str, str], list[str]],
    stats: ExtractionStats,
) -> None:
    """Write the accumulated entity relations; ``weight`` = times observed."""
    for (source_id, target_id), descriptions in relation_acc.items():
        db.add(KnowledgeEntityRelation(
            id=gen_id(),
            project_id=project_id,
            source_entity_id=source_id,
            target_entity_id=target_id,
            description=(descriptions[0] if descriptions else "")[:300],
            weight=len(descriptions),
        ))
        stats.relations_created += 1


# --- Internal: sanitisers ---------------------------------------------------

def _sanitise_entities(raw: object) -> list[dict]:
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for e in raw:
        if not isinstance(e, dict):
            continue
        name = str(e.get("name") or "").strip()
        if not name:
            continue
        etype = str(e.get("type") or "concept").strip().lower()
        if etype not in VALID_ENTITY_TYPES:
            etype = "concept"
        out.append({
            "name": name,
            "type": etype,
            "description": str(e.get("description") or "").strip(),
        })
    return out


def _sanitise_relations(raw: object) -> list[dict]:
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for r in raw:
        if not isinstance(r, dict):
            continue
        source = str(r.get("source") or "").strip()
        target = str(r.get("target") or "").strip()
        if not source or not target:
            continue
        out.append({
            "source": source,
            "target": target,
            "description": str(r.get("description") or "").strip(),
        })
    return out
