"""Lateral-expansion engine for Tief-Mode (P7).

"Links und rechts schauen" from the design doc: take the Top-K
high-confidence findings of the previous hop, extract the entities
they mention, rank those entities by relevance to the original topic,
and plan one follow-up sub-query per top entity. The pipeline then
runs Phase 2 (SEARCH) again on the new sub-queries — building the
"hop tree" the UI renders as Round 0 → keycloak-broker → Round 1.

Three LLM stages, all budget-gated:

    1. extract_entities_from_finding(...)  — 1 call per finding
       category: "entity_extract"
    2. rank_by_relevance(...)              — 1 call per hop (batch)
       category: "lateral_plan"
    3. plan_lateral_subquery(...)          — 1 call per top entity
       category: "lateral_plan"

Hard caps from Spec §5.4 (enforced *here*, not just in the prompt):
    * max 5 entities per finding
    * max 6 lateral sub-queries per hop
    * max 2 hops total (the pipeline owns the hop counter)
    * entity dedup over all rounds
    * relevance_cutoff filter (default 0.5)
    * budget pressure ≥ critical → skip the next hop entirely

The module NEVER raises into the pipeline. Any LLM failure / parse
failure → drop the entity and continue. The worst-case is a hop that
yields zero sub-queries; the pipeline finalises Normal-style.
"""
from __future__ import annotations

import json
import logging
import re
import secrets
from dataclasses import dataclass, field
from typing import Iterable

from services.research_budget import (
    BudgetDegradation,
    BudgetTracker,
    _llm_call_with_budget,
)
from services.research_planner import SubQueryPlan
from services.synapse_llm import call_json

logger = logging.getLogger("projecthub.research.lateral")


# ── Tuning constants ───────────────────────────────────────────────────────

#: Max entities the extractor accepts per finding (spec §5.4).
_MAX_ENTITIES_PER_FINDING = 5

#: Minimum entity name length after normalisation. Single-char tokens
#: are LLM noise — drop them outright.
_MIN_ENTITY_LENGTH = 3

#: DE+EN stopwords kept consistent with the project_notes / bm25 modules
#: so the lexical filters all share the same noise floor.
_ENTITY_STOPWORDS: frozenset[str] = frozenset({
    "der", "die", "das", "und", "oder", "ist", "war", "sind",
    "mit", "für", "von", "zu", "auf", "im", "den", "ein", "eine",
    "the", "and", "or", "is", "was", "are", "with", "for", "of",
    "to", "in", "a", "an", "this", "that", "these", "those", "it",
})


# ── Value types ────────────────────────────────────────────────────────────


@dataclass
class ExtractedEntity:
    """One entity extracted from one finding."""

    name: str
    normalized: str
    confidence: float  # 0-1, LLM-reported
    source_finding_id: str


@dataclass
class RankedEntity:
    """An entity that survived dedup + filtering + relevance ranking."""

    name: str
    normalized: str
    extraction_confidence: float
    relevance: float  # 0-1, LLM-judged against the topic
    source_finding_ids: list[str] = field(default_factory=list)
    extra_freq: int = 1  # how many findings mentioned this entity


# ── Pure helpers ──────────────────────────────────────────────────────────


def _normalize_name(name: str) -> str:
    """Lowercased + whitespace-collapsed canonical form."""
    if not name:
        return ""
    return " ".join(name.lower().strip().split())


def filter_high_value(
    entities: Iterable[ExtractedEntity],
    *,
    seen_normalized: set[str],
    min_length: int = _MIN_ENTITY_LENGTH,
    min_freq: int = 2,
    min_single_conf: float = 0.8,
) -> list[RankedEntity]:
    """Dedup + filter raw extracted entities into a value-grouped list.

    Rules (Spec §5.4):
        * drop entities < ``min_length`` chars or in the stopword set
        * drop entities already in ``seen_normalized`` (cross-round dedup)
        * keep entities that EITHER appear in ≥ ``min_freq`` findings
          (multi-source signal) OR were extracted with ≥
          ``min_single_conf`` from at least one finding (high-confidence
          single-source signal)
    """
    by_norm: dict[str, RankedEntity] = {}
    for ent in entities:
        norm = ent.normalized
        if (
            len(norm) < min_length
            or norm in _ENTITY_STOPWORDS
            or norm in seen_normalized
        ):
            continue
        if norm in by_norm:
            existing = by_norm[norm]
            existing.extra_freq += 1
            existing.source_finding_ids.append(ent.source_finding_id)
            existing.extraction_confidence = max(
                existing.extraction_confidence, ent.confidence,
            )
        else:
            by_norm[norm] = RankedEntity(
                name=ent.name,
                normalized=norm,
                extraction_confidence=ent.confidence,
                relevance=0.0,  # filled in by rank_by_relevance
                source_finding_ids=[ent.source_finding_id],
                extra_freq=1,
            )

    high_value: list[RankedEntity] = []
    for re_ in by_norm.values():
        if re_.extra_freq >= min_freq or re_.extraction_confidence >= min_single_conf:
            high_value.append(re_)
    # Deterministic order so downstream tests don't flap.
    high_value.sort(key=lambda r: (-r.extra_freq, -r.extraction_confidence, r.normalized))
    return high_value


# ── Stage 1: Entity extraction (1 LLM call per finding) ──────────────────


_EXTRACT_PROMPT = """\
Extrahiere bis zu {max_entities} relevante Entitäten aus dem Text. Entitäten sind
benannte Konzepte (Services, Personen, Komponenten, Technologien, Versionen,
Policies, Issue-Keys, Domain-Begriffe). KEINE Allgemeinwörter.

TEXT:
{text}

ANTWORTE AUSSCHLIESSLICH als valides JSON-Array:
[
  {{"name": "Kanonische Schreibweise", "confidence": 0.85}},
  ...
]
"""


async def extract_entities_from_finding(
    finding_id: str,
    text: str,
    *,
    budget: BudgetTracker | None = None,
    model: str | None = None,
    max_entities: int = _MAX_ENTITIES_PER_FINDING,
) -> list[ExtractedEntity]:
    """Extract up to ``max_entities`` entities from a finding's text.

    Returns an empty list on any failure — the caller (the lateral
    orchestrator) treats zero entities as "skip this finding".
    """
    if not text or not text.strip():
        return []
    prompt = _EXTRACT_PROMPT.format(
        max_entities=max_entities, text=text.strip()[:2000],
    )
    try:
        result = await _llm_call_with_budget(
            budget, "entity_extract", 1000, 300,
            call_json, prompt, model=model, session_prefix="lateral-extract",
        )
    except BudgetDegradation:
        # Bubble — caller decides whether to skip this finding or
        # abort the whole hop based on the suggested_action.
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning("entity extract LLM call failed for %s: %s", finding_id, e)
        return []

    parsed = getattr(result, "parsed", None)
    if not isinstance(parsed, list):
        return []

    out: list[ExtractedEntity] = []
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        try:
            conf = float(entry.get("confidence", 0.5))
        except (TypeError, ValueError):
            conf = 0.5
        conf = max(0.0, min(1.0, conf))
        out.append(ExtractedEntity(
            name=name[:200],
            normalized=_normalize_name(name),
            confidence=conf,
            source_finding_id=finding_id,
        ))
        if len(out) >= max_entities:
            break
    return out


# ── Stage 2: Relevance ranking (1 LLM call per hop, batch) ───────────────


_RANK_PROMPT = """\
Bewerte für das gegebene Topic, wie relevant jede Entität für eine vertiefte
Recherche wäre. 0.0 = irrelevant, 1.0 = sehr relevant.

TOPIC: {topic}

ENTITÄTEN:
{entities_list}

ANTWORTE AUSSCHLIESSLICH als valides JSON-Array (eine Zeile pro Entität,
selbe Reihenfolge wie oben):
[
  {{"id": 1, "relevance": 0.9}},
  ...
]
"""


async def rank_by_relevance(
    entities: list[RankedEntity],
    topic: str,
    *,
    budget: BudgetTracker | None = None,
    model: str | None = None,
) -> list[RankedEntity]:
    """Score every entity by relevance to ``topic``; return in-place sorted desc.

    On any failure, returns the input list with ``relevance=0.0`` so
    the caller's cutoff filter drops everything — i.e. lateral expansion
    silently degrades to "no new sub-queries" rather than producing
    off-topic noise.
    """
    if not entities:
        return []

    entities_list = "\n".join(
        f"  [{i + 1}] {e.name}" for i, e in enumerate(entities)
    )
    prompt = _RANK_PROMPT.format(topic=topic.strip()[:500], entities_list=entities_list)

    try:
        result = await _llm_call_with_budget(
            budget, "lateral_plan", 800, 200,
            call_json, prompt, model=model, session_prefix="lateral-rank",
        )
    except BudgetDegradation:
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning("relevance rank LLM call failed: %s", e)
        return entities  # all zero — caller filters out

    parsed = getattr(result, "parsed", None)
    if not isinstance(parsed, list):
        return entities

    by_idx: dict[int, float] = {}
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        try:
            idx = int(entry.get("id", 0)) - 1
            rel = float(entry.get("relevance", 0.0))
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(entities):
            by_idx[idx] = max(0.0, min(1.0, rel))

    for i, e in enumerate(entities):
        e.relevance = by_idx.get(i, 0.0)
    entities.sort(key=lambda e: (-e.relevance, -e.extra_freq, e.normalized))
    return entities


# ── Stage 3: Per-entity sub-query planning (1 LLM call per entity) ───────


_PLAN_PROMPT = """\
Du planst EINE Sub-Frage für eine vertiefte Recherche.

ORIGINAL-TOPIC: {topic}
ENTITÄT: {entity}
VERFÜGBARE QUELLEN: {providers_list}

Formuliere genau EINE prägnante Sub-Frage, die die Beziehung der Entität
zum Original-Topic vertieft. Wähle 1–{max_providers} Quellen aus der Liste.

ANTWORTE AUSSCHLIESSLICH als valides JSON:
{{
  "question": "...",
  "providers": ["quelle_a", "quelle_b"],
  "rationale": "..."
}}
"""


async def plan_lateral_subquery(
    entity: RankedEntity,
    *,
    topic: str,
    enabled_providers: list[str],
    max_providers_per_sub_query: int,
    budget: BudgetTracker | None = None,
    model: str | None = None,
) -> SubQueryPlan | None:
    """Plan one lateral sub-query for the given entity.

    Returns ``None`` on any failure — caller skips this entity.
    """
    prompt = _PLAN_PROMPT.format(
        topic=topic.strip()[:500],
        entity=entity.name[:200],
        providers_list=", ".join(enabled_providers),
        max_providers=max_providers_per_sub_query,
    )
    try:
        result = await _llm_call_with_budget(
            budget, "lateral_plan", 1000, 300,
            call_json, prompt, model=model, session_prefix="lateral-plan",
        )
    except BudgetDegradation:
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "lateral plan LLM call failed for entity=%s: %s",
            entity.name, e,
        )
        return None

    parsed = getattr(result, "parsed", None)
    if not isinstance(parsed, dict):
        return None

    question = str(parsed.get("question") or "").strip()
    if not question:
        return None
    providers_in = parsed.get("providers")
    if not isinstance(providers_in, list):
        return None
    providers = [
        p for p in providers_in
        if isinstance(p, str) and p in enabled_providers
    ][:max_providers_per_sub_query]
    if not providers:
        return None

    return SubQueryPlan(
        id=f"lat-{entity.normalized[:30]}-{secrets.token_hex(4)}",
        question=question[:500],
        providers=providers,
        rationale=str(parsed.get("rationale") or "")[:300],
        priority=2,
        expected_cost="medium",
    )


# ── Orchestrator: expand_hop ─────────────────────────────────────────────


@dataclass
class HopExpansion:
    """Output of one lateral-expansion round."""

    hop: int
    new_sub_queries: list[SubQueryPlan]
    parent_finding_ids: dict[str, list[str]]  # sub_query_id → parent finding ids
    entity_focus: dict[str, str]              # sub_query_id → entity name
    relevance_scores: dict[str, float]        # sub_query_id → relevance
    extracted_count: int                       # total entities extracted
    surviving_count: int                       # after dedup + filter
    ranked_top: int                            # selected as top-N
    aborted_reason: str | None = None


async def expand_hop(
    hop: int,
    findings: list[dict],
    *,
    topic: str,
    enabled_providers: list[str],
    seen_entities: set[str],
    max_new_sub_queries: int,
    max_providers_per_sub_query: int,
    relevance_cutoff: float,
    budget: BudgetTracker | None = None,
    model: str | None = None,
) -> HopExpansion:
    """Run one lateral hop: entities → rank → plan sub-queries.

    Args:
        hop: hop number (1-based; the initial round is 0).
        findings: list of dicts ``{id, title, snippet, content}`` from
            the previous round's grounded findings. Caller filters
            high-confidence rows; we just process what's handed in.
        topic: original research topic — used for relevance ranking
            and the per-entity sub-query prompt.
        enabled_providers: full set of providers the project has on.
            Planner output is filtered to this set.
        seen_entities: normalised entities already explored in earlier
            hops (or by initial sub-queries). Mutated in place — when
            this hop accepts new entities, they're added.
        max_new_sub_queries / max_providers_per_sub_query /
        relevance_cutoff: from the depth profile.
        budget / model: forwarded to the LLM helpers.

    Returns:
        ``HopExpansion`` with the new sub-queries (already SubQueryPlan
        shape so the pipeline can persist + run them directly).
        ``aborted_reason`` is set on a budget-degraded short-circuit.
    """
    out = HopExpansion(
        hop=hop,
        new_sub_queries=[],
        parent_finding_ids={},
        entity_focus={},
        relevance_scores={},
        extracted_count=0,
        surviving_count=0,
        ranked_top=0,
    )

    if not findings:
        out.aborted_reason = "no_findings"
        return out

    # ─ Stage 1: extract entities per finding ─
    all_entities: list[ExtractedEntity] = []
    for f in findings:
        text = f.get("snippet") or f.get("content") or f.get("title") or ""
        try:
            ents = await extract_entities_from_finding(
                str(f.get("id") or ""),
                str(text),
                budget=budget,
                model=model,
            )
        except BudgetDegradation as e:
            logger.info("lateral hop %d aborted on entity_extract: %s", hop, e)
            out.aborted_reason = f"budget:{e.suggested_action}"
            return out
        all_entities.extend(ents)
    out.extracted_count = len(all_entities)

    # ─ Filter (dedup + length + stopwords + freq/conf) ─
    survivors = filter_high_value(all_entities, seen_normalized=seen_entities)
    out.surviving_count = len(survivors)
    if not survivors:
        out.aborted_reason = "no_surviving_entities"
        return out

    # ─ Stage 2: rank by relevance ─
    try:
        ranked = await rank_by_relevance(survivors, topic, budget=budget, model=model)
    except BudgetDegradation as e:
        out.aborted_reason = f"budget:{e.suggested_action}"
        return out

    # ─ Cutoff filter ─
    above_cutoff = [r for r in ranked if r.relevance >= relevance_cutoff]
    top = above_cutoff[:max_new_sub_queries]
    out.ranked_top = len(top)
    if not top:
        out.aborted_reason = "no_entity_above_cutoff"
        return out

    # ─ Stage 3: per-entity sub-query planning ─
    for re_ in top:
        try:
            sq = await plan_lateral_subquery(
                re_,
                topic=topic,
                enabled_providers=enabled_providers,
                max_providers_per_sub_query=max_providers_per_sub_query,
                budget=budget,
                model=model,
            )
        except BudgetDegradation as e:
            out.aborted_reason = f"budget:{e.suggested_action}"
            # Keep what we already have — the run still benefits.
            break
        if sq is None:
            continue

        # Wire lateral metadata into the SubQueryPlan via attrs the
        # pipeline reads (SubQueryPlan has no native fields for these,
        # so we maintain them in the parallel dicts on HopExpansion).
        out.new_sub_queries.append(sq)
        out.parent_finding_ids[sq.id] = list(re_.source_finding_ids)
        out.entity_focus[sq.id] = re_.name
        out.relevance_scores[sq.id] = re_.relevance
        # Mark this entity explored so subsequent hops won't repeat it.
        seen_entities.add(re_.normalized)

    return out
