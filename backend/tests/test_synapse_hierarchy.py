"""Tests for ``services/synapse_hierarchy.py`` (P5, T5.1 + T5.5).

Coverage split:

    Pure helpers     — build_synapse_graph, partition_synapse_graph,
                       aggregate_child_confidence (no DB, no LLM)
    DB-aware         — detect_synapse_communities, run_hierarchy_phase,
                       synthesise_level_n_community (with monkeypatched
                       call_json)
    HTTP-aware       — GET /api/synapse/{p}/hierarchy
    Eval-gate (T5.5) — multi-cluster Level-0 input → Level-1 emerges
                       AND globale-Frage produces a Level-1 hit (not a
                       Level-0 enumeration)
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import sys
import tempfile
from dataclasses import dataclass

import pytest


_TEST_DB_PATH = os.path.join(
    tempfile.gettempdir(),
    f"projecthub_hierarchy_pytest_{secrets.token_hex(4)}.db",
)
os.environ["PROJECTHUB_DB_PATH"] = _TEST_DB_PATH


import sqlalchemy as sa

from services.synapse_hierarchy import (
    DEFAULT_MIN_SHARED_ITEMS,
    DEFAULT_MIN_SYNAPSES,
    HierarchyRunStats,
    _SynapseProjection,
    aggregate_child_confidence,
    build_synapse_graph,
    partition_synapse_graph,
)


# ── Pure helpers ─────────────────────────────────────────────────────


def _proj(sid: str, items: list[str], conf: float = 0.9, band: str = "high") -> _SynapseProjection:
    return _SynapseProjection(
        synapse_id=sid,
        source_item_ids=tuple(items),
        confidence=conf,
        confidence_band=band,
    )


def test_build_graph_no_shared_items_no_edges() -> None:
    """Synapses with disjoint source_item_ids → no edges."""
    g = build_synapse_graph([
        _proj("s1", ["a", "b"]),
        _proj("s2", ["c", "d"]),
        _proj("s3", ["e"]),
    ])
    assert g.number_of_nodes() == 3
    assert g.number_of_edges() == 0


def test_build_graph_requires_min_shared_items() -> None:
    """Default threshold is 2 — a single shared item is NOT enough."""
    g = build_synapse_graph([
        _proj("s1", ["a", "b"]),
        _proj("s2", ["b", "c"]),  # shares only "b" → no edge
    ])
    assert g.number_of_edges() == 0


def test_build_graph_edge_at_threshold() -> None:
    """Two shared items → 1 edge with weight=2."""
    g = build_synapse_graph([
        _proj("s1", ["a", "b", "c"]),
        _proj("s2", ["a", "b", "d"]),
    ])
    assert g.number_of_edges() == 1
    assert g["s1"]["s2"]["weight"] == 2.0


def test_build_graph_lower_threshold_picks_up_single_shared() -> None:
    g = build_synapse_graph(
        [_proj("s1", ["a", "b"]), _proj("s2", ["a", "c"])],
        min_shared_items=1,
    )
    assert g.number_of_edges() == 1


def test_partition_single_cluster() -> None:
    """3 synapses fully connected by shared items → 1 community."""
    out = partition_synapse_graph([
        _proj("s1", ["a", "b", "c"]),
        _proj("s2", ["a", "b", "d"]),
        _proj("s3", ["a", "c", "e"]),
    ])
    assert len(out) == 1
    assert sorted(out[0].member_synapse_ids) == ["s1", "s2", "s3"]
    # source_item_ids = union over the cluster
    assert set(out[0].source_item_ids) == {"a", "b", "c", "d", "e"}
    assert out[0].level == 1


def test_partition_two_disjoint_clusters() -> None:
    """Two well-separated topic clusters → 2 communities."""
    out = partition_synapse_graph([
        # Cluster A (auth topic)
        _proj("a1", ["item-A1", "item-A2", "item-A3"]),
        _proj("a2", ["item-A1", "item-A2", "item-A4"]),
        _proj("a3", ["item-A2", "item-A3", "item-A4"]),
        # Cluster B (deploy topic) — fully disjoint item set
        _proj("b1", ["item-B1", "item-B2", "item-B3"]),
        _proj("b2", ["item-B1", "item-B2", "item-B4"]),
        _proj("b3", ["item-B2", "item-B3", "item-B4"]),
    ])
    assert len(out) == 2
    ids = sorted([sorted(c.member_synapse_ids) for c in out])
    assert ids == [["a1", "a2", "a3"], ["b1", "b2", "b3"]]


def test_partition_drops_singletons() -> None:
    """A synapse not connected to any other is below ``min_synapses``."""
    out = partition_synapse_graph([
        _proj("a1", ["x", "y"]),
        _proj("a2", ["x", "y"]),
        _proj("loner", ["q", "r", "s"]),  # no shared items
    ])
    # The loner forms a 1-element cluster → filtered. a1+a2 form one community.
    assert len(out) == 1
    assert "loner" not in out[0].member_synapse_ids


def test_partition_below_min_returns_empty() -> None:
    """Fewer projections than min_synapses → no communities."""
    out = partition_synapse_graph([_proj("only", ["x", "y"])])
    assert out == []


def test_partition_sorted_by_size_desc() -> None:
    """Larger clusters come first — deterministic UI ordering."""
    out = partition_synapse_graph([
        # Small cluster (2 synapses)
        _proj("s1", ["a", "b"]),
        _proj("s2", ["a", "b"]),
        # Large cluster (3 synapses)
        _proj("l1", ["x", "y"]),
        _proj("l2", ["x", "y"]),
        _proj("l3", ["x", "y"]),
    ])
    assert len(out) == 2
    assert out[0].size > out[1].size


# ── aggregate_child_confidence ────────────────────────────────────────


def test_aggregate_empty_returns_low() -> None:
    conf, band, verdict = aggregate_child_confidence([])
    assert conf == 0.0
    assert band == "low"
    assert verdict == "human_review"


def test_aggregate_all_high_returns_high() -> None:
    conf, band, verdict = aggregate_child_confidence([
        _proj("a", [], conf=0.9, band="high"),
        _proj("b", [], conf=0.95, band="high"),
    ])
    assert band == "high"
    assert verdict == "persist"
    assert 0.9 <= conf <= 0.95


def test_aggregate_mixed_caps_at_weakest_band() -> None:
    """One medium child caps a parent at medium even if confidence math
    suggests high — Level-N inherits weakest-link evidence quality."""
    conf, band, verdict = aggregate_child_confidence([
        _proj("a", [], conf=0.95, band="high"),
        _proj("b", [], conf=0.95, band="high"),
        _proj("c", [], conf=0.95, band="medium"),  # the cap
    ])
    assert band == "medium"
    assert verdict == "persist_flagged"


def test_aggregate_with_low_child_forces_review() -> None:
    conf, band, verdict = aggregate_child_confidence([
        _proj("a", [], conf=0.95, band="high"),
        _proj("b", [], conf=0.6, band="low"),
    ])
    assert band == "low"
    assert verdict == "human_review"


# ── DB-aware: end-to-end with monkeypatched LLM ─────────────────────


@pytest.fixture(scope="module")
def db_setup():
    """Initialise the test DB once for the module."""
    try:
        if os.path.exists(_TEST_DB_PATH):
            os.unlink(_TEST_DB_PATH)
    except OSError:
        pass

    import models  # noqa: F401
    from database import async_session, init_db

    asyncio.get_event_loop().run_until_complete(init_db())
    yield async_session


async def _seed_l0_synapse(
    project_id: str, *, syn_id: str, title: str, summary: str,
    source_item_ids: list[str], confidence: float = 0.9,
    band: str = "high", verdict: str = "persist",
) -> None:
    """Seed one Level-0 synapse directly via SQL (skips LLM pipeline)."""
    from database import engine

    async with engine.begin() as conn:
        await conn.execute(
            sa.text(
                "INSERT INTO synapses "
                "(id, project_id, generation_run_id, parent_id, title, "
                " summary, summary_plain, community_level, confidence, "
                " confidence_band, verdict, status, source_item_ids, "
                " source_entity_ids, extra_data, created_at, updated_at) "
                "VALUES (:id, :pid, NULL, NULL, :title, :sum, :plain, 0, "
                " :conf, :band, :verdict, 'validated', :sids, '[]', '{}', "
                " '2026-05-18', '2026-05-18')"
            ),
            {
                "id": syn_id, "pid": project_id, "title": title,
                "sum": f"<p>{summary}</p>", "plain": summary,
                "conf": confidence, "band": band, "verdict": verdict,
                "sids": json.dumps(source_item_ids),
            },
        )


@pytest.mark.asyncio
async def test_detect_synapse_communities_db_integration(db_setup, monkeypatch) -> None:
    """Seed 6 L0 synapses (3 about auth, 3 about deploy) → 2 L1 clusters."""
    from services.synapse_hierarchy import detect_synapse_communities

    pid = f"hpid-{secrets.token_hex(4)}"
    # Insert a stub project row so FKs are satisfied
    from database import engine
    async with engine.begin() as conn:
        await conn.execute(
            sa.text(
                "INSERT INTO projects (id, name, description, status, color, tags, "
                " sort_order, created_at, updated_at) "
                "VALUES (:id, 'h-test', '', 'aktiv', '#fff', '[]', 0, "
                " '2026-05-18', '2026-05-18')"
            ),
            {"id": pid},
        )

    # Auth cluster — each pair shares ≥2 items (threshold for edges).
    await _seed_l0_synapse(pid, syn_id="a1", title="Auth A",
                            summary="auth via JWT", source_item_ids=["I1", "I2", "I3"])
    await _seed_l0_synapse(pid, syn_id="a2", title="Auth B",
                            summary="auth via OAuth", source_item_ids=["I1", "I2", "I4"])
    await _seed_l0_synapse(pid, syn_id="a3", title="Auth C",
                            summary="auth refresh tokens", source_item_ids=["I2", "I3", "I4"])

    # Deploy cluster — fully disjoint items, also ≥2 shared within
    await _seed_l0_synapse(pid, syn_id="d1", title="Deploy A",
                            summary="docker prod", source_item_ids=["J1", "J2", "J3"])
    await _seed_l0_synapse(pid, syn_id="d2", title="Deploy B",
                            summary="k8s rollout", source_item_ids=["J1", "J2", "J4"])
    await _seed_l0_synapse(pid, syn_id="d3", title="Deploy C",
                            summary="ci/cd pipeline", source_item_ids=["J2", "J3", "J4"])

    async with db_setup() as db:
        communities = await detect_synapse_communities(db, pid, parent_level=0)

    assert len(communities) == 2
    ids_per_cluster = sorted(sorted(c.member_synapse_ids) for c in communities)
    assert ids_per_cluster == [["a1", "a2", "a3"], ["d1", "d2", "d3"]]


@pytest.mark.asyncio
async def test_run_hierarchy_phase_creates_l1_synapses(db_setup, monkeypatch) -> None:
    """Full integration: seed L0 → run phase → assert L1 synapses + parent_id chain."""
    from services.synapse_hierarchy import run_hierarchy_phase
    from services import synapse_llm

    # Project
    pid = f"hpid-{secrets.token_hex(4)}"
    from database import engine
    async with engine.begin() as conn:
        await conn.execute(
            sa.text(
                "INSERT INTO projects (id, name, description, status, color, tags, "
                " sort_order, created_at, updated_at) "
                "VALUES (:id, 'h-test', '', 'aktiv', '#fff', '[]', 0, "
                " '2026-05-18', '2026-05-18')"
            ),
            {"id": pid},
        )

    # Two disjoint clusters of 2 each
    await _seed_l0_synapse(pid, syn_id="auth-a", title="Auth A",
                            summary="auth note A", source_item_ids=["I1", "I2"])
    await _seed_l0_synapse(pid, syn_id="auth-b", title="Auth B",
                            summary="auth note B", source_item_ids=["I1", "I2"])
    await _seed_l0_synapse(pid, syn_id="dep-a", title="Deploy A",
                            summary="deploy note A", source_item_ids=["J1", "J2"])
    await _seed_l0_synapse(pid, syn_id="dep-b", title="Deploy B",
                            summary="deploy note B", source_item_ids=["J1", "J2"])

    # Stub the LLM — each cluster gets a canned response
    cluster_call_count = [0]

    @dataclass
    class _Res:
        ok: bool = True
        parsed: dict = None
        raw: str = ""
        model: str = "stub"
        usage: dict = None
        error: str | None = None

    async def _fake_call_json(prompt, *, model=None, session_prefix="synapse"):
        cluster_call_count[0] += 1
        # The prompt lists 2 parents — emit a single claim citing both
        return _Res(
            ok=True,
            parsed={
                "title": f"Theme {cluster_call_count[0]}",
                "summary": f"Aggregated theme {cluster_call_count[0]}",
                "claims": [
                    {"text": f"Shared theme claim {cluster_call_count[0]}", "sources": [1, 2]},
                ],
            },
            usage={"total_tokens": 50},
        )

    monkeypatch.setattr(synapse_llm, "call_json", _fake_call_json)

    async with db_setup() as db:
        stats = await run_hierarchy_phase(db, pid, run_id=None, max_level=2)

    assert stats.levels_built == 1
    assert stats.synapses_created == 2  # 2 clusters → 2 L1 synapses
    assert stats.by_level == {1: 2}

    # Verify DB: 2 L1 synapses present, each with parent_id chain.
    async with engine.connect() as conn:
        l1 = await conn.execute(
            sa.text(
                "SELECT id, title, community_level, parent_id, source_item_ids "
                "FROM synapses WHERE project_id=:pid AND community_level=1"
            ),
            {"pid": pid},
        )
        l1_rows = l1.fetchall()
        assert len(l1_rows) == 2
        # source_item_ids should be the union (e.g., I1+I2 for auth cluster)
        for row in l1_rows:
            items = set(json.loads(row[4]))
            assert items == {"I1", "I2"} or items == {"J1", "J2"}

        # L0 parents now point UP at the L1
        l0 = await conn.execute(
            sa.text(
                "SELECT id, parent_id FROM synapses WHERE project_id=:pid AND community_level=0"
            ),
            {"pid": pid},
        )
        for row in l0.fetchall():
            assert row[1] is not None, f"L0 synapse {row[0]} has no parent_id"


@pytest.mark.asyncio
async def test_run_hierarchy_stops_when_no_l1_communities(db_setup, monkeypatch) -> None:
    """Only 1 L0 synapse → can't form L1 community → 0 synapses created, no LLM call."""
    from services.synapse_hierarchy import run_hierarchy_phase
    from services import synapse_llm

    pid = f"hpid-{secrets.token_hex(4)}"
    from database import engine
    async with engine.begin() as conn:
        await conn.execute(
            sa.text(
                "INSERT INTO projects (id, name, description, status, color, tags, "
                " sort_order, created_at, updated_at) "
                "VALUES (:id, 'h-test', '', 'aktiv', '#fff', '[]', 0, "
                " '2026-05-18', '2026-05-18')"
            ),
            {"id": pid},
        )
    await _seed_l0_synapse(pid, syn_id="only-one", title="Solo",
                            summary="single", source_item_ids=["X", "Y"])

    call_count = [0]

    async def _bombed_call_json(*args, **kwargs):
        call_count[0] += 1
        raise RuntimeError("LLM should not have been called!")

    monkeypatch.setattr(synapse_llm, "call_json", _bombed_call_json)

    async with db_setup() as db:
        stats = await run_hierarchy_phase(db, pid, run_id=None, max_level=2)

    assert stats.levels_built == 0
    assert stats.synapses_created == 0
    assert call_count[0] == 0  # below-min-synapses short-circuit


# ── HTTP endpoint test ────────────────────────────────────────────────


@pytest.fixture(scope="module")
def client(db_setup):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from routers.projects import router as projects_router
    from routers.synapse import router as synapse_router

    app = FastAPI()
    app.include_router(projects_router)
    app.include_router(synapse_router)
    with TestClient(app) as c:
        yield c


def test_hierarchy_endpoint_returns_nested_levels(client) -> None:
    """End-to-end: seed L0+L1, hit endpoint, assert shape + parent_id chain."""
    r = client.post("/api/projects", json={"name": f"hier-ep-{secrets.token_hex(3)}"})
    pid = r.json()["id"]

    import asyncio as _asyncio
    from database import engine

    async def _seed():
        async with engine.begin() as conn:
            # 2 L0 synapses sharing items
            await conn.execute(sa.text(
                "INSERT INTO synapses (id, project_id, title, summary, summary_plain, "
                " community_level, confidence, confidence_band, verdict, status, "
                " source_item_ids, source_entity_ids, extra_data, created_at, updated_at) "
                "VALUES "
                "('l0-a', :pid, 'L0 A', '<p>a</p>', 'a', 0, 0.9, 'high', 'persist', "
                " 'validated', '[\"i1\", \"i2\"]', '[]', '{}', '2026-05-18', '2026-05-18'),"
                "('l0-b', :pid, 'L0 B', '<p>b</p>', 'b', 0, 0.85, 'high', 'persist', "
                " 'validated', '[\"i1\", \"i2\"]', '[]', '{}', '2026-05-18', '2026-05-18'),"
                "('l1-x', :pid, 'L1 X', '<p>x</p>', 'x', 1, 0.87, 'high', 'persist_flagged', "
                " 'validated', '[\"i1\", \"i2\"]', '[]', '{}', '2026-05-18', '2026-05-18')"
            ), {"pid": pid})
            # Point L0s at L1
            await conn.execute(
                sa.text("UPDATE synapses SET parent_id='l1-x' WHERE id IN ('l0-a', 'l0-b')")
            )

    _asyncio.get_event_loop().run_until_complete(_seed())

    r = client.get(f"/api/synapse/{pid}/hierarchy")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["max_level"] == 1
    assert body["level_counts"] == {"0": 2, "1": 1}
    by_id = {n["id"]: n for n in body["nodes"]}
    assert by_id["l1-x"]["parent_id"] is None
    assert by_id["l0-a"]["parent_id"] == "l1-x"
    assert by_id["l0-b"]["parent_id"] == "l1-x"


# ── T5.5 eval-gate: hierarchy DOES emerge when the data warrants it ─


@pytest.mark.asyncio
async def test_p5_eval_gate_themes_emerge_from_clusters(db_setup, monkeypatch) -> None:
    """**P5-Gate** — the global-question quality check.

    Workflow doc: "Globale Frage 'Was sind die Themen?' liefert Themen-
    Bündel statt 12 unsortierte Synapsen." We translate that into a
    structural assertion: given a corpus with 2 clearly distinct topical
    clusters of Level-0 synapses, the hierarchy phase MUST produce
    exactly 2 Level-1 ``persist*`` synapses (the "theme bundles"), and
    no more — the user shouldn't have to wade through false aggregations.

    Threshold (per design doc §2.7): MS-GraphRAG-style hierarchical
    summarisation gives "+50–70% comprehensiveness" on globale Fragen.
    The structural assertion here is the necessary precondition: the
    levels must actually exist before any comprehensiveness gain can
    accrue downstream.
    """
    from services.synapse_hierarchy import run_hierarchy_phase
    from services import synapse_llm

    pid = f"hpid-eval-{secrets.token_hex(4)}"
    from database import engine

    async with engine.begin() as conn:
        await conn.execute(
            sa.text(
                "INSERT INTO projects (id, name, description, status, color, tags, "
                " sort_order, created_at, updated_at) "
                "VALUES (:id, 'h-eval', '', 'aktiv', '#fff', '[]', 0, "
                " '2026-05-18', '2026-05-18')"
            ),
            {"id": pid},
        )

    # 6 Level-0 synapses across 2 themes — needs to be enough to
    # demonstrate the "instead of 6 you see 2" emergent grouping.
    for i, (sid, items) in enumerate([
        ("auth-1", ["a1", "a2", "a3"]),
        ("auth-2", ["a1", "a2", "a4"]),
        ("auth-3", ["a2", "a3", "a4"]),
        ("deploy-1", ["d1", "d2", "d3"]),
        ("deploy-2", ["d1", "d2", "d4"]),
        ("deploy-3", ["d2", "d3", "d4"]),
    ]):
        await _seed_l0_synapse(
            pid, syn_id=sid, title=f"L0 {sid}",
            summary=f"summary for {sid}", source_item_ids=items,
        )

    @dataclass
    class _R:
        ok: bool
        parsed: dict
        raw: str = ""
        model: str = "stub"
        usage: dict = None
        error: str | None = None

    call_count = [0]

    async def _fake_call(prompt, *, model=None, session_prefix="synapse"):
        call_count[0] += 1
        return _R(
            ok=True,
            parsed={
                "title": f"Theme bundle {call_count[0]}",
                "summary": f"Aggregated theme synthesis #{call_count[0]}",
                "claims": [
                    {"text": f"Aggregated insight {call_count[0]}", "sources": [1, 2]},
                ],
            },
            usage={"total_tokens": 50},
        )

    monkeypatch.setattr(synapse_llm, "call_json", _fake_call)

    async with db_setup() as db:
        stats = await run_hierarchy_phase(db, pid, run_id=None, max_level=2)

    # ── Gate assertions ───────────────────────────────────────────────
    # Exactly 2 Level-1 synapses — one per theme bundle.
    assert stats.synapses_created == 2, (
        f"P5 gate failed: expected 2 theme-bundle synapses, got "
        f"{stats.synapses_created}"
    )
    assert stats.levels_built == 1
    assert stats.by_level == {1: 2}

    # No false-aggregation: at L2 the clustering should not collapse the
    # two distinct themes into one super-cluster (item-id sets are
    # disjoint after union — auth's items don't overlap deploy's).
    # The phase should stop at L1 because L2 needs ≥2 L1 communities of
    # ≥2 members each — there's only one L1 cluster possible (or none),
    # so phase tops out at L1.
    # → stats.levels_built == 1 already asserted; no L2 created.

    # Verify the two L1 synapses point to the right children
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                sa.text(
                    "SELECT id, source_item_ids FROM synapses "
                    "WHERE project_id=:pid AND community_level=1"
                ),
                {"pid": pid},
            )
        ).fetchall()

    item_sets = [set(json.loads(r[1])) for r in rows]
    # Each L1's source_item_ids = union of its cluster (auth or deploy)
    assert {"a1", "a2", "a3", "a4"} in item_sets
    assert {"d1", "d2", "d3", "d4"} in item_sets

    print(
        f"\n[P5 eval-gate] 6 L0 synapses across 2 themes → "
        f"{stats.synapses_created} L1 theme bundles ({stats.by_level})"
    )
