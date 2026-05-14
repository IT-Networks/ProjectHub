"""Tests for the synapse community-detection core (Phase 2).

Exercises the DB-free ``partition_entity_graph`` — the risky algorithmic
part — with synthetic entity graphs. The DB-reading wrapper
(``detect_communities``) is covered by integration tests later.
"""
import pytest

from services.synapse_communities import Community, partition_entity_graph


def _ids(prefix: str, n: int) -> list[str]:
    return [f"{prefix}{i}" for i in range(n)]


def test_two_dense_clusters_separate():
    """Two groups, dense within / sparse between → two communities."""
    a = _ids("a", 4)  # cluster A entities
    b = _ids("b", 4)  # cluster B entities
    entity_ids = a + b

    # Dense explicit relations inside each cluster, one weak bridge a0-b0.
    relations = []
    for grp in (a, b):
        for i in range(len(grp)):
            for j in range(i + 1, len(grp)):
                relations.append((grp[i], grp[j], 5.0))
    relations.append((a[0], b[0], 1.0))  # weak bridge

    # Each cluster's entities are co-mentioned across its own items.
    mentions = []
    for k, e in enumerate(a):
        mentions.append((e, "itemA1"))
        mentions.append((e, "itemA2"))
    for k, e in enumerate(b):
        mentions.append((e, "itemB1"))
        mentions.append((e, "itemB2"))

    communities = partition_entity_graph(entity_ids, relations, mentions)

    assert len(communities) == 2
    # Every community keeps its entities together — no cross-contamination.
    for c in communities:
        assert set(c.entity_ids) <= set(a) or set(c.entity_ids) <= set(b)
    # Communities are sorted by item count (here both have 2 → stable, no crash).
    assert all(isinstance(c, Community) for c in communities)


def test_min_entities_filter_drops_singletons():
    """An isolated entity forms a singleton community → filtered out."""
    entity_ids = _ids("a", 3) + ["lonely"]
    relations = [("a0", "a1", 3.0), ("a1", "a2", 3.0), ("a0", "a2", 3.0)]
    mentions = [
        ("a0", "i1"), ("a1", "i1"), ("a2", "i1"),
        ("a0", "i2"), ("a1", "i2"),
        ("lonely", "i3"),  # only mention, no relations
    ]
    communities = partition_entity_graph(entity_ids, relations, mentions)

    assert len(communities) == 1
    assert "lonely" not in communities[0].entity_ids
    assert set(communities[0].entity_ids) == {"a0", "a1", "a2"}


def test_min_items_filter_drops_thin_communities():
    """A real cluster whose entities only appear in one item is dropped."""
    entity_ids = _ids("a", 3)
    relations = [("a0", "a1", 3.0), ("a1", "a2", 3.0)]
    mentions = [("a0", "i1"), ("a1", "i1"), ("a2", "i1")]  # all in ONE item

    # Default min_items=2 → dropped.
    assert partition_entity_graph(entity_ids, relations, mentions) == []
    # min_items=1 → kept.
    kept = partition_entity_graph(entity_ids, relations, mentions, min_items=1)
    assert len(kept) == 1
    assert kept[0].item_ids == ["i1"]


def test_co_mention_only_still_clusters():
    """No explicit relations — co-mention edges alone must form communities."""
    a = _ids("a", 3)
    b = _ids("b", 3)
    entity_ids = a + b
    relations: list = []  # none

    mentions = []
    # Cluster A entities share two items; cluster B entities share two others.
    for e in a:
        mentions += [(e, "iA1"), (e, "iA2")]
    for e in b:
        mentions += [(e, "iB1"), (e, "iB2")]

    communities = partition_entity_graph(entity_ids, relations, mentions)

    assert len(communities) == 2
    for c in communities:
        assert set(c.entity_ids) <= set(a) or set(c.entity_ids) <= set(b)


def test_empty_and_tiny_inputs():
    assert partition_entity_graph([], [], []) == []
    # Below min_entities threshold.
    assert partition_entity_graph(["a0"], [], [("a0", "i1")]) == []


def test_relations_referencing_unknown_entities_are_ignored():
    """A relation pointing at a cleared/unknown entity must not crash."""
    entity_ids = _ids("a", 3)
    relations = [
        ("a0", "a1", 3.0),
        ("a1", "a2", 3.0),
        ("a0", "ghost", 9.0),     # unknown target
        ("phantom", "a2", 9.0),   # unknown source
    ]
    mentions = [("a0", "i1"), ("a1", "i1"), ("a2", "i2"), ("a0", "i2")]
    communities = partition_entity_graph(entity_ids, relations, mentions)

    assert len(communities) == 1
    assert "ghost" not in communities[0].entity_ids
    assert "phantom" not in communities[0].entity_ids


def test_deterministic_across_runs():
    """Seeded Louvain → identical partition on repeated calls."""
    a, b = _ids("a", 4), _ids("b", 4)
    entity_ids = a + b
    relations = []
    for grp in (a, b):
        for i in range(len(grp)):
            for j in range(i + 1, len(grp)):
                relations.append((grp[i], grp[j], 4.0))
    mentions = [(e, "iA") for e in a] + [(e, "iA2") for e in a]
    mentions += [(e, "iB") for e in b] + [(e, "iB2") for e in b]

    first = partition_entity_graph(entity_ids, relations, mentions)
    second = partition_entity_graph(entity_ids, relations, mentions)

    as_tuples = lambda cs: sorted(tuple(c.entity_ids) for c in cs)
    assert as_tuples(first) == as_tuples(second)


def test_communities_sorted_by_item_count_desc():
    """Bigger communities (more source items) come first."""
    a = _ids("a", 3)   # will be backed by 4 items
    b = _ids("b", 3)   # will be backed by 2 items
    entity_ids = a + b
    relations = []
    for grp in (a, b):
        for i in range(len(grp)):
            for j in range(i + 1, len(grp)):
                relations.append((grp[i], grp[j], 4.0))

    mentions = []
    for e in a:
        mentions += [(e, f"iA{n}") for n in range(4)]
    for e in b:
        mentions += [(e, f"iB{n}") for n in range(2)]

    communities = partition_entity_graph(entity_ids, relations, mentions)
    assert len(communities) == 2
    assert communities[0].size >= communities[1].size
    assert communities[0].size == 4


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
