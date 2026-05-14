"""Community detection — second stage of the synapse pipeline.

Takes the resolved entity layer (Phase 1 output) and partitions it into
*communities* — clusters of densely-connected entities that, taken
together, represent one coherent theme. Each community becomes the input
to one synthesis call (Phase 3).

The entity graph is built from two complementary signals:

* **explicit relations** — ``KnowledgeEntityRelation`` rows the LLM
  extracted ("X depends on Y"),
* **co-mention** — two entities mentioned in the same ``KnowledgeItem``
  get an edge weighted by how many items they share.

Co-mention keeps the graph connected even when the LLM extracted few
explicit relations, so community detection degrades gracefully instead
of collapsing into singletons.

Algorithm: networkx Louvain (``louvain_communities``), seeded for
deterministic runs. Single-level for v1 — the data model carries
``community_level`` / ``parent_id`` for a future hierarchical pass.

The DB-free core (``partition_entity_graph``) is split out from the
DB-reading wrapper (``detect_communities``) so the graph algorithm can
be unit-tested with synthetic data.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import networkx as nx

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("projecthub.synapse")

# Louvain seed — fixed so a re-run over unchanged data is reproducible.
_LOUVAIN_SEED = 42

# A community is only worth synthesising if it has enough substance.
DEFAULT_MIN_ENTITIES = 2
DEFAULT_MIN_ITEMS = 2


@dataclass
class Community:
    """One detected cluster: the entities in it and the items they came from."""

    entity_ids: list[str]
    item_ids: list[str]
    level: int = 0

    @property
    def size(self) -> int:
        """Ranking key — communities backed by more source items come first."""
        return len(self.item_ids)


def partition_entity_graph(
    entity_ids: list[str],
    relations: list[tuple[str, str, float]],
    mentions: list[tuple[str, str]],
    *,
    min_entities: int = DEFAULT_MIN_ENTITIES,
    min_items: int = DEFAULT_MIN_ITEMS,
) -> list[Community]:
    """Pure graph core: entities + relations + mentions → communities.

    Args:
        entity_ids: every entity id in the project (each becomes a node).
        relations:  ``(source_entity_id, target_entity_id, weight)`` triples.
        mentions:   ``(entity_id, item_id)`` pairs.

    Returns communities sorted by source-item count (descending), filtered
    to those meeting ``min_entities`` / ``min_items``. No DB access — see
    ``detect_communities`` for the wired-up version.
    """
    if len(entity_ids) < min_entities:
        return []

    # Mention maps — drive co-mention edges and the community→items mapping.
    entity_to_items: dict[str, set[str]] = {}
    item_to_entities: dict[str, set[str]] = {}
    for entity_id, item_id in mentions:
        entity_to_items.setdefault(entity_id, set()).add(item_id)
        item_to_entities.setdefault(item_id, set()).add(entity_id)

    # Build the weighted undirected graph.
    graph = nx.Graph()
    graph.add_nodes_from(entity_ids)

    def _add_weight(u: str, v: str, w: float) -> None:
        if u == v:
            return
        if graph.has_edge(u, v):
            graph[u][v]["weight"] += w
        else:
            graph.add_edge(u, v, weight=w)

    for source_id, target_id, weight in relations:
        # Relations may reference entities that were cleared/recreated — guard.
        if graph.has_node(source_id) and graph.has_node(target_id):
            _add_weight(source_id, target_id, float(weight or 1))

    # Co-mention edges: every pair of entities sharing an item gets +1.
    for shared_entities in item_to_entities.values():
        members = sorted(shared_entities)
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                _add_weight(members[i], members[j], 1.0)

    # Louvain partition. With no edges every node is its own community —
    # those get filtered out by the min_* thresholds below.
    raw_communities = nx.community.louvain_communities(
        graph, weight="weight", seed=_LOUVAIN_SEED
    )

    # Map each community to its source items, filter, sort.
    communities: list[Community] = []
    for entity_set in raw_communities:
        if len(entity_set) < min_entities:
            continue
        item_ids: set[str] = set()
        for entity_id in entity_set:
            item_ids |= entity_to_items.get(entity_id, set())
        if len(item_ids) < min_items:
            continue
        communities.append(Community(
            entity_ids=sorted(entity_set),
            item_ids=sorted(item_ids),
        ))

    communities.sort(key=lambda c: c.size, reverse=True)
    logger.info(
        "partition_entity_graph: entities=%d edges=%d → %d communities",
        len(entity_ids), graph.number_of_edges(), len(communities),
    )
    return communities


async def detect_communities(
    db: AsyncSession,
    project_id: str,
    *,
    min_entities: int = DEFAULT_MIN_ENTITIES,
    min_items: int = DEFAULT_MIN_ITEMS,
) -> list[Community]:
    """Partition a project's entity graph into communities.

    Reads the entity layer for ``project_id`` and delegates to
    ``partition_entity_graph``. The caller (pipeline orchestrator) owns
    the DB session; this only reads.
    """
    # Deferred imports — keep the module's pure core (partition_entity_graph)
    # importable with just networkx, so it can be unit-tested standalone.
    from sqlalchemy import select
    from models.synapse import (
        KnowledgeEntity, KnowledgeEntityMention, KnowledgeEntityRelation,
    )

    entity_rows = await db.execute(
        select(KnowledgeEntity.id).where(KnowledgeEntity.project_id == project_id)
    )
    entity_ids = [row[0] for row in entity_rows.all()]

    mention_rows = await db.execute(
        select(KnowledgeEntityMention.entity_id, KnowledgeEntityMention.item_id)
        .join(KnowledgeEntity, KnowledgeEntity.id == KnowledgeEntityMention.entity_id)
        .where(KnowledgeEntity.project_id == project_id)
    )
    mentions = [(row[0], row[1]) for row in mention_rows.all()]

    relation_rows = await db.execute(
        select(
            KnowledgeEntityRelation.source_entity_id,
            KnowledgeEntityRelation.target_entity_id,
            KnowledgeEntityRelation.weight,
        ).where(KnowledgeEntityRelation.project_id == project_id)
    )
    relations = [
        (row[0], row[1], float(row[2] or 1)) for row in relation_rows.all()
    ]

    communities = partition_entity_graph(
        entity_ids, relations, mentions,
        min_entities=min_entities, min_items=min_items,
    )
    logger.info(
        "detect_communities: project=%s → %d communities", project_id, len(communities)
    )
    return communities
