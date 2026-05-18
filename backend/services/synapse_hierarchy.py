"""Hierarchical synapses (P5, T5.1).

Microsoft-GraphRAG-style: a second Louvain pass over the *Synapses* of
level N-1 yields Level-N "Themen-Bündel" — meta-synapses that summarise
groups of related Level-0 findings. Enables globale Fragen ("What are
the architectural tensions in this project?") that flat Level-0 synapses
can't answer comfortably (the user would need to skim all 10-50 of them).

Design (vs. v1 entity-level community detection):

    Layer 0  Communities of ENTITIES (existing — services/synapse_communities)
             → Level-0 Synapses (existing — services/synapse_synthesis)
    Layer 1+ Communities of SYNAPSES (this module)
             → Level-N Synapses (this module)

Graph edges between Level-(N-1) synapses:
    * Co-citation: shared ``source_item_ids`` ≥ ``min_shared_items`` →
      weight = number of shared items
    * (future) Cosine on summary embeddings ≥ 0.7 — not in v1 because
      synapse summaries aren't embedded yet; co-citation alone gives
      sensible clusters on real-world data.

Level-N synapses are produced by a separate LLM call whose ``sources``
are the parent synapses' summaries (NOT raw KnowledgeItems). The
``draft_claims`` carry ``source_synapse_ids`` (vs ``source_item_ids`` at
Level 0) so the lineage is unambiguous in the DB.

**Validation strategy for Level-N:** we do NOT route them through the
full validate_synapse pipeline. Their truthfulness is already grounded
by the validated children. Confidence is aggregated from the children's
confidences (currently: average, capped at the lowest child band). This
keeps token cost bounded and avoids the "ground a synapse-claim against
a synapse-summary" semantic question — which is a v2 research topic.

Pipeline integration: services/synapse_pipeline.py calls
``run_hierarchy_phase`` after the Level-0 validate phase, but only when
``settings.brain_hierarchical_synapses_enabled`` is True. The whole
module is invisible until that flag flips.
"""
from __future__ import annotations

import json
import logging
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import networkx as nx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("projecthub.synapse.hierarchy")

# Same seed as services/synapse_communities to keep all clustering in the
# project reproducible across runs.
_LOUVAIN_SEED = 42

# A community at the synapse layer must have at least this many members
# before we synthesise a Level-N summary for it — a 1-synapse "cluster"
# isn't a theme-bundle.
DEFAULT_MIN_SYNAPSES = 2

# Co-citation edge threshold: two synapses must share at least this many
# source items before they get connected. 2 is the SoTA MS-GraphRAG
# default — a single shared item is too noisy.
DEFAULT_MIN_SHARED_ITEMS = 2

# How many parent synapses we send into the level-N LLM prompt at most.
_MAX_PARENT_SYNAPSES_IN_PROMPT = 8
# Each parent's summary truncation cap inside the level-N prompt.
_MAX_PARENT_SUMMARY_CHARS = 800


# ── Data types ──────────────────────────────────────────────────────────


@dataclass
class SynapseCommunity:
    """A detected cluster of Level-(N-1) synapses → one Level-N synapse.

    ``member_synapse_ids`` is the cluster of parents. ``source_item_ids``
    is the union of every member's source_item_ids — used for provenance
    on the Level-N synapse (so leaf items remain reachable).
    """

    member_synapse_ids: list[str]
    source_item_ids: list[str]
    level: int = 1

    @property
    def size(self) -> int:
        return len(self.member_synapse_ids)


# Lightweight projection passed into the graph builder so we can keep it
# pure (no DB). Caller maps from real ``Synapse`` rows.
@dataclass
class _SynapseProjection:
    synapse_id: str
    source_item_ids: tuple[str, ...]
    confidence: float
    confidence_band: str


# ── Pure helpers ───────────────────────────────────────────────────────


def build_synapse_graph(
    projections: list[_SynapseProjection],
    *,
    min_shared_items: int = DEFAULT_MIN_SHARED_ITEMS,
) -> nx.Graph:
    """Build the synapse co-citation graph (no DB).

    One node per synapse; one edge between two synapses iff they share at
    least ``min_shared_items`` source items. Edge weight = shared count.
    """
    graph = nx.Graph()
    for p in projections:
        graph.add_node(p.synapse_id)

    n = len(projections)
    for i in range(n):
        a = projections[i]
        a_items = set(a.source_item_ids)
        if not a_items:
            continue
        for j in range(i + 1, n):
            b = projections[j]
            shared = a_items & set(b.source_item_ids)
            if len(shared) >= min_shared_items:
                graph.add_edge(a.synapse_id, b.synapse_id, weight=float(len(shared)))
    return graph


def partition_synapse_graph(
    projections: list[_SynapseProjection],
    *,
    level: int = 1,
    min_synapses: int = DEFAULT_MIN_SYNAPSES,
    min_shared_items: int = DEFAULT_MIN_SHARED_ITEMS,
) -> list[SynapseCommunity]:
    """Pure graph core: synapse projections → Level-N communities.

    Empty / under-sized communities are filtered. Result is sorted by
    member-count descending for deterministic UI ordering.
    """
    if len(projections) < min_synapses:
        return []

    graph = build_synapse_graph(projections, min_shared_items=min_shared_items)

    # ``louvain_communities`` raises ZeroDivisionError on a graph with no
    # edges (deg_sum == 0). That's a degenerate case for us — no co-
    # citation signal means there are no meaningful clusters to detect.
    # Skip the partition and return empty.
    if graph.number_of_edges() == 0:
        logger.debug(
            "partition_synapse_graph: graph has %d nodes, 0 edges — skip Louvain",
            graph.number_of_nodes(),
        )
        return []

    raw_communities = nx.community.louvain_communities(
        graph, weight="weight", seed=_LOUVAIN_SEED
    )

    by_id = {p.synapse_id: p for p in projections}

    out: list[SynapseCommunity] = []
    for syn_set in raw_communities:
        if len(syn_set) < min_synapses:
            continue
        # Union of source items across the cluster — leaf-level provenance.
        all_items: set[str] = set()
        for sid in syn_set:
            proj = by_id.get(sid)
            if proj is not None:
                all_items |= set(proj.source_item_ids)
        out.append(SynapseCommunity(
            member_synapse_ids=sorted(syn_set),
            source_item_ids=sorted(all_items),
            level=level,
        ))

    out.sort(key=lambda c: c.size, reverse=True)
    logger.info(
        "partition_synapse_graph(level=%d): synapses=%d edges=%d → %d communities",
        level, len(projections), graph.number_of_edges(), len(out),
    )
    return out


def aggregate_child_confidence(
    children: list[_SynapseProjection],
) -> tuple[float, str, str]:
    """Compute (confidence, band, verdict) for a Level-N synapse from its
    children's already-validated confidence values.

    Rules:
        * confidence = mean(child_confidences), bounded to [0, 1]
        * band: derived from the same thresholds as Level-0, BUT capped
          at the weakest child's band — a parent shouldn't read as "high"
          if one of its children is "medium" (the parent inherits the
          weakest link of its evidence chain)
        * verdict: ``persist`` (high) | ``persist_flagged`` (medium) |
          ``human_review`` (low or empty)
    """
    if not children:
        return 0.0, "low", "human_review"

    mean = statistics.fmean(max(0.0, min(1.0, c.confidence)) for c in children)
    confidence = round(max(0.0, min(1.0, mean)), 3)

    bands = {c.confidence_band for c in children}
    if "low" in bands:
        capped_band = "low"
    elif "medium" in bands:
        capped_band = "medium"
    else:
        capped_band = "high"

    # Compute by-confidence band, then cap at child-band.
    if confidence >= 0.85:
        natural_band = "high"
    elif confidence >= 0.5:
        natural_band = "medium"
    else:
        natural_band = "low"

    # Take the worse of (natural, capped) — capped takes precedence when
    # children have inconsistent quality.
    band_rank = {"low": 0, "medium": 1, "high": 2}
    final_band = (
        natural_band
        if band_rank[natural_band] < band_rank[capped_band]
        else capped_band
    )

    if final_band == "high":
        verdict = "persist"
    elif final_band == "medium":
        verdict = "persist_flagged"
    else:
        verdict = "human_review"
    return confidence, final_band, verdict


def _projection_from_synapse(synapse: Any) -> _SynapseProjection:
    """Build a ``_SynapseProjection`` from a real ``Synapse`` model row."""
    return _SynapseProjection(
        synapse_id=synapse.id,
        source_item_ids=tuple(synapse.source_item_ids_list),
        confidence=float(synapse.confidence or 0.0),
        confidence_band=str(synapse.confidence_band or "low"),
    )


# ── LLM synthesis for Level-N ──────────────────────────────────────────


_LEVEL_N_PROMPT = """Du formulierst aus mehreren bereits-synthetisierten Wissens-Knoten EINEN übergeordneten Themen-Knoten (Level {level}).

**Eltern-Knoten (Level {parent_level}, bereits validiert):**
{parents}

---

Formuliere:
1. einen prägnanten **Titel** (max 100 Zeichen) für das gemeinsame Thema dieser Eltern,
2. eine **Synthese** (3-6 Sätze): das gemeinsame Bild, das aus den Eltern emergiert — Spannungen, Muster, Schlussfolgerungen auf höherer Ebene,
3. eine Liste **atomarer Aussagen** ("claims") — je eine überprüfbare Einzelaussage, jede mit den Eltern-Nummern, auf die sie sich stützt.

Antworte AUSSCHLIESSLICH als valides JSON (kein Markdown, keine Erklärung):
{{
  "title": "...",
  "summary": "...",
  "claims": [
    {{"text": "Eine überprüfbare Einzelaussage", "sources": [1, 3]}}
  ]
}}

Regeln:
- Jede Aussage in "claims" MUSS mindestens eine Eltern-Nummer in "sources" haben.
- Verwende nur Eltern-Nummern, die oben wirklich vorkommen.
- Aggregiere — wiederhole die Eltern nicht 1:1; finde das Übergeordnete.
- Wenn die Eltern keinen sinnvollen gemeinsamen Erkenntniswert haben: "claims" leer lassen."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def synthesise_level_n_community(
    db: AsyncSession,
    project_id: str,
    community: SynapseCommunity,
    *,
    run_id: str | None,
    parent_level: int,
    ai_assist: Any | None = None,
    model: str | None = None,
) -> Any | None:
    """Generate one Level-N synapse for a synapse-cluster.

    Returns the persisted ``Synapse`` (added to the session, ID set), or
    ``None`` if synthesis was empty / not actionable.

    Verdict / confidence are aggregated from the children — Level-N is
    NOT routed through the validate_synapse pipeline (see module
    docstring for the rationale).
    """
    # Late import keeps the module's pure core (partition_synapse_graph,
    # aggregate_child_confidence, build_synapse_graph) testable without
    # the full SQLAlchemy stack.
    from models.synapse import Synapse
    from services.synapse_llm import call_json, gen_id, merge_usage

    # Load the parent synapses (Level N-1).
    parents_res = await db.execute(
        select(Synapse).where(Synapse.id.in_(community.member_synapse_ids))
    )
    parents = list(parents_res.scalars().all())
    if not parents:
        return None

    # Order by confidence desc — strongest evidence first in the prompt;
    # the LLM weighs early-positioned items more.
    parents.sort(key=lambda s: (s.confidence or 0.0), reverse=True)
    parents = parents[:_MAX_PARENT_SYNAPSES_IN_PROMPT]

    # Number the parents 1..N for the prompt. Map back to ids for claims.
    index_to_parent_id: dict[int, str] = {}
    parent_lines: list[str] = []
    for i, p in enumerate(parents, start=1):
        index_to_parent_id[i] = p.id
        body = (p.summary_plain or "").strip()[:_MAX_PARENT_SUMMARY_CHARS]
        parent_lines.append(f"[{i}] {p.title}\n{body}")

    prompt = _LEVEL_N_PROMPT.format(
        level=community.level,
        parent_level=parent_level,
        parents="\n\n".join(parent_lines),
    )

    # We can't import the synapse_llm.call_json signature with ai_assist
    # injection (it picks up the global ai_assist client). Tests inject
    # via the lower-level ``call_json`` monkeypatch on synapse_llm itself,
    # or via the ``ai_assist`` arg if I add support — for now reuse the
    # standard path.
    res = await call_json(prompt, session_prefix=f"hierarchy-l{community.level}")
    if not res.ok or not isinstance(res.parsed, dict):
        return None

    title = str(res.parsed.get("title") or "").strip()
    summary = str(res.parsed.get("summary") or "").strip()
    if not title or not summary:
        return None

    raw_claims = res.parsed.get("claims") or []
    draft_claims: list[dict] = []
    if isinstance(raw_claims, list):
        for claim in raw_claims:
            if not isinstance(claim, dict):
                continue
            text = str(claim.get("text") or "").strip()
            if not text:
                continue
            parent_ids: list[str] = []
            for num in claim.get("sources") or []:
                try:
                    pid = index_to_parent_id.get(int(num))
                except (TypeError, ValueError):
                    continue
                if pid and pid not in parent_ids:
                    parent_ids.append(pid)
            if not parent_ids:
                continue
            draft_claims.append({"text": text, "source_synapse_ids": parent_ids})

    # Aggregate confidence/verdict from the children.
    projections = [_projection_from_synapse(p) for p in parents]
    confidence, band, verdict = aggregate_child_confidence(projections)

    synapse = Synapse(
        id=gen_id(),
        project_id=project_id,
        generation_run_id=run_id,
        title=title[:300],
        summary=f"<p>{summary}</p>",
        summary_plain=summary[:8000],
        community_level=community.level,
        confidence=confidence,
        confidence_band=band,
        verdict=verdict,
        # Level-N synapses skip the validate pipeline; they are "validated"
        # transitively through their already-validated parents. Marking
        # them ``validated`` here is consistent with the same-status check
        # the chat-context builder uses to surface them.
        status="validated",
    )
    # source_item_ids = union over the cluster — provenance chain to leaves
    synapse.source_item_ids_list = list(community.source_item_ids)
    # source_entity_ids — Level-N synapses don't directly own entities; we
    # leave it empty. (Aggregating from children entities is a v2 nicety.)
    synapse.source_entity_ids_list = []
    synapse.extra_data_dict = {
        "draft_claims": draft_claims,
        "synthesis_model": res.model,
        "level_n_aggregated": True,
        "parent_synapse_ids": [p.id for p in parents],
    }
    db.add(synapse)

    # Point each parent at the new Level-N synapse via parent_id.
    for p in parents:
        p.parent_id = synapse.id
        p.updated_at = _now()

    merge_usage_dest = {}
    merge_usage(merge_usage_dest, res.usage)
    logger.debug(
        "synthesise_level_n: created L%d synapse %s from %d parents, conf=%.2f",
        community.level, synapse.id, len(parents), confidence,
    )
    return synapse


# ── DB-aware orchestrator ──────────────────────────────────────────────


@dataclass
class HierarchyRunStats:
    levels_built: int = 0
    synapses_created: int = 0
    skipped_clusters: int = 0
    by_level: dict[int, int] = field(default_factory=dict)


async def detect_synapse_communities(
    db: AsyncSession,
    project_id: str,
    *,
    parent_level: int,
    min_synapses: int = DEFAULT_MIN_SYNAPSES,
    min_shared_items: int = DEFAULT_MIN_SHARED_ITEMS,
) -> list[SynapseCommunity]:
    """Read synapses at ``parent_level`` from the DB and partition them.

    Only ``status='validated'`` synapses with a ``persist*`` verdict are
    eligible parents — we don't aggregate noise into a higher level.
    """
    from models.synapse import Synapse

    rows = await db.execute(
        select(Synapse).where(
            Synapse.project_id == project_id,
            Synapse.community_level == parent_level,
            Synapse.status == "validated",
            Synapse.verdict.in_(["persist", "persist_flagged"]),
        )
    )
    synapses = list(rows.scalars().all())
    if len(synapses) < min_synapses:
        return []

    projections = [_projection_from_synapse(s) for s in synapses]
    return partition_synapse_graph(
        projections,
        level=parent_level + 1,
        min_synapses=min_synapses,
        min_shared_items=min_shared_items,
    )


async def run_hierarchy_phase(
    db: AsyncSession,
    project_id: str,
    *,
    run_id: str | None,
    max_level: int = 2,
    min_synapses: int = DEFAULT_MIN_SYNAPSES,
    min_shared_items: int = DEFAULT_MIN_SHARED_ITEMS,
) -> HierarchyRunStats:
    """Orchestrate the hierarchy phase: detect → synthesise → persist, per level.

    Iterates from Level 1 to ``max_level``. Each level reads the previous
    level's validated synapses, clusters them by co-citation, and emits
    Level-N synapses. Returns stats for the run-status row + SSE event.

    Caller (services/synapse_pipeline) owns commit semantics — we add
    rows to the session; the orchestrator commits in batches.
    """
    stats = HierarchyRunStats()

    for level in range(1, max_level + 1):
        parent_level = level - 1
        communities = await detect_synapse_communities(
            db, project_id,
            parent_level=parent_level,
            min_synapses=min_synapses,
            min_shared_items=min_shared_items,
        )
        if not communities:
            logger.info(
                "[hierarchy] no L%d communities for project %s — stopping ascent",
                level, project_id,
            )
            break

        created_this_level = 0
        for community in communities:
            try:
                syn = await synthesise_level_n_community(
                    db, project_id, community,
                    run_id=run_id,
                    parent_level=parent_level,
                )
            except Exception as e:  # noqa: BLE001 — one bad community must not kill the phase
                logger.warning(
                    "[hierarchy] L%d community failed (members=%d): %s",
                    level, community.size, e,
                )
                stats.skipped_clusters += 1
                continue
            if syn is None:
                stats.skipped_clusters += 1
                continue
            created_this_level += 1
            stats.synapses_created += 1

        # Commit each level before ascending — partial progress survives crashes
        await db.commit()
        stats.by_level[level] = created_this_level
        if created_this_level > 0:
            stats.levels_built = level
        else:
            logger.info(
                "[hierarchy] L%d produced 0 synapses — stopping ascent", level
            )
            break

    return stats
