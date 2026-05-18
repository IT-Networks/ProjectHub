"""Tests for ``services/retrieval/hybrid.py`` (T2.7).

Pure helpers tested with parametrised values; the DB-aware
``hybrid_search`` is exercised via the in-process FastAPI app with a
fresh SQLite — same pattern as ``test_memory_router.py``.

Coverage:

* pack/unpack roundtrips through bytes correctly
* cosine handles dim-mismatch and zero-vectors without crashing
* RRF fuses two ranked lists, with boost for items in both
* hybrid_search degrades to FTS5 cleanly when no embedder
* hybrid_search returns items in fused order
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import secrets
import struct
import sys
import tempfile
from dataclasses import dataclass, field

import pytest


# Fresh test DB pinned BEFORE any backend import — same trick as
# test_memory_router does. Uses a different filename so the suites don't
# stomp each other when run in the same session.
_TEST_DB_PATH = os.path.join(
    tempfile.gettempdir(), f"projecthub_hybrid_pytest_{secrets.token_hex(4)}.db"
)
os.environ["PROJECTHUB_DB_PATH"] = _TEST_DB_PATH

from services.retrieval.hybrid import (
    SearchHit,
    cosine_similarity,
    hybrid_search,
    pack_vector,
    rrf_merge,
    unpack_vector,
)


# ── pack / unpack ────────────────────────────────────────────────────────


def test_pack_unpack_roundtrip() -> None:
    vec = [0.1, -0.2, 0.3, 1.5, -7.25]
    blob = pack_vector(vec)
    assert isinstance(blob, bytes)
    assert len(blob) == 4 * len(vec)
    out = unpack_vector(blob)
    assert len(out) == len(vec)
    for original, restored in zip(vec, out):
        assert math.isclose(original, restored, rel_tol=1e-6, abs_tol=1e-6)


def test_unpack_handles_empty_and_none() -> None:
    assert unpack_vector(None) == []
    assert unpack_vector(b"") == []


def test_unpack_handles_truncated_blob() -> None:
    """A blob with a non-multiple-of-4 length means upstream corruption —
    don't crash, just return ``[]`` so the caller treats the item as
    'not embedded' rather than poisoning the search."""
    assert unpack_vector(b"\x00\x00\x00") == []


# ── cosine_similarity ───────────────────────────────────────────────────


def test_cosine_identical_vectors_is_one() -> None:
    v = [1.0, 2.0, 3.0]
    assert math.isclose(cosine_similarity(v, v), 1.0, rel_tol=1e-6)


def test_cosine_orthogonal_vectors_is_zero() -> None:
    a = [1.0, 0.0]
    b = [0.0, 1.0]
    assert math.isclose(cosine_similarity(a, b), 0.0, abs_tol=1e-9)


def test_cosine_opposite_vectors_is_minus_one() -> None:
    a = [1.0, 2.0]
    b = [-1.0, -2.0]
    assert math.isclose(cosine_similarity(a, b), -1.0, rel_tol=1e-6)


def test_cosine_dim_mismatch_returns_zero() -> None:
    assert cosine_similarity([1.0, 2.0], [1.0, 2.0, 3.0]) == 0.0


def test_cosine_zero_vector_returns_zero() -> None:
    assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


# ── rrf_merge ────────────────────────────────────────────────────────────


def test_rrf_merge_single_list_preserves_order() -> None:
    out = rrf_merge([["a", "b", "c"]])
    assert out == ["a", "b", "c"]


def test_rrf_merge_boost_for_items_in_both_lists() -> None:
    """An item ranked top of one channel AND top of the other should beat
    items that only appear in one — RRF's whole point."""
    fts = ["a", "b", "c"]
    cos = ["b", "x", "y"]
    out = rrf_merge([fts, cos])
    assert out[0] == "b", out


def test_rrf_merge_handles_empty_lists() -> None:
    """RRF with one empty channel must not crash and must return the
    other channel verbatim."""
    out = rrf_merge([["a", "b"], []])
    assert out == ["a", "b"]


def test_rrf_merge_handles_completely_empty() -> None:
    assert rrf_merge([]) == []
    assert rrf_merge([[]]) == []


def test_rrf_merge_tie_break_is_deterministic() -> None:
    """Two items with identical RRF scores must order by id for
    reproducibility — flaky test outputs are a real time-sink."""
    # Both 'a' and 'b' appear only in slot 1 of disjoint lists → identical scores.
    out_1 = rrf_merge([["a"], ["b"]])
    out_2 = rrf_merge([["b"], ["a"]])
    # Scores identical, secondary sort on id → a always before b
    assert out_1 == ["a", "b"] and out_2 == ["a", "b"]


# ── hybrid_search (db-aware) ────────────────────────────────────────────


@pytest.fixture(scope="module")
def client():
    """One in-process FastAPI client per test module."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    try:
        if os.path.exists(_TEST_DB_PATH):
            os.unlink(_TEST_DB_PATH)
    except OSError:
        pass

    import models  # noqa: F401
    from database import init_db
    from routers.memory import router as memory_router
    from routers.projects import router as projects_router

    asyncio.get_event_loop().run_until_complete(init_db())

    app = FastAPI()
    app.include_router(projects_router)
    app.include_router(memory_router)

    with TestClient(app) as c:
        yield c


def _new_project(client) -> str:
    r = client.post("/api/projects", json={"name": f"hybrid-{secrets.token_hex(3)}"})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def _seed_item(
    client, project_id: str, *, title: str, body: str,
    embedding: list[float] | None = None,
) -> str:
    """Push one item via /api/memory/v1/extract then optionally write an
    embedding directly via async SQL (no UPDATE endpoint exists yet — that
    comes with T2.5).

    ``async`` so callers from inside an ``@pytest.mark.asyncio`` test can
    just ``await _seed_item(...)`` without fighting the event-loop policy.
    """
    client.put(
        "/api/memory/v1/workspaces",
        json={"project_id": project_id, "workspace_path": f"/work/hybrid-{project_id}"},
    )
    r = client.post(
        "/api/memory/v1/extract",
        json={
            "session_id": "seed",
            "workspace": f"/work/hybrid-{project_id}",
            "facts": [{"text": body, "type": "reference", "tags": [], "confidence": 0.7}],
        },
    )
    assert r.status_code == 200, r.text
    item_id = r.json()["created_item_ids"][0]

    if embedding is not None:
        import sqlalchemy as sa

        from database import engine

        async with engine.begin() as conn:
            await conn.execute(
                sa.text(
                    "UPDATE knowledge_items SET embedding=:e, embedding_model='test-model' "
                    "WHERE id=:id"
                ),
                {"e": pack_vector(embedding), "id": item_id},
            )
    return item_id


@dataclass
class _StubEmbedder:
    """Deterministic embedder for hybrid_search tests."""

    vectors: dict[str, list[float]] = field(default_factory=dict)
    dim: int = 3
    model_id: str = "stub"
    name: str = "stub"

    async def embed(self, texts):
        return [self.vectors.get(t, [0.0] * self.dim) for t in texts]

    async def embed_one(self, text):
        return self.vectors.get(text, [0.0] * self.dim)


@pytest.mark.asyncio
async def test_hybrid_search_degrades_to_fts_without_embedder(client) -> None:
    """mode=hybrid + embedder=None must NOT crash; must fall back to FTS5."""
    from database import async_session

    pid = _new_project(client)
    await _seed_item(client, pid, title="A", body="needle in the haystack sentinel A")
    await _seed_item(client, pid, title="B", body="unrelated text about other things B")

    async with async_session() as db:
        out = await hybrid_search(db, pid, "sentinel", top_k=5, embedder=None, mode="hybrid")
    assert len(out) >= 1
    assert any("sentinel" in (h.item.content_plain or "").lower() for h in out)


@pytest.mark.asyncio
async def test_hybrid_search_mode_fts_returns_fts_results(client) -> None:
    from database import async_session

    pid = _new_project(client)
    await _seed_item(client, pid, title="A", body="purple zebra crossing the road")
    await _seed_item(client, pid, title="B", body="orange octopus on a unicycle")

    async with async_session() as db:
        out = await hybrid_search(db, pid, "zebra", top_k=5, embedder=None, mode="fts")
    assert len(out) == 1
    assert "zebra" in out[0].item.content_plain
    assert out[0].source == {"fts"}


@pytest.mark.asyncio
async def test_hybrid_search_mode_cosine_uses_embeddings(client) -> None:
    """mode=cosine ignores FTS and ranks purely by cosine similarity."""
    from database import async_session

    pid = _new_project(client)
    # Two items: the body text differs to keep FTS5 from matching.
    near_id = await _seed_item(
        client, pid, title="near", body="alpha", embedding=[1.0, 0.0, 0.0]
    )
    await _seed_item(client, pid, title="far", body="beta", embedding=[-1.0, 0.0, 0.0])

    embedder = _StubEmbedder(vectors={"query": [1.0, 0.0, 0.0]}, dim=3)

    async with async_session() as db:
        out = await hybrid_search(
            db, pid, "query", top_k=2, embedder=embedder, mode="cosine"
        )
    assert len(out) >= 1
    assert out[0].item.id == near_id
    assert out[0].source == {"cosine"}


@pytest.mark.asyncio
async def test_hybrid_search_fuses_both_channels(client) -> None:
    """An item that appears in BOTH the FTS5 hits AND the cosine hits
    should outrank an item that only appears in one — that's the whole
    point of RRF."""
    from database import async_session

    pid = _new_project(client)
    # Three items:
    #   - both:        contains "frobnicate" (FTS hit) + nearby embedding (cosine hit)
    #   - fts-only:    contains "frobnicate" but far-apart embedding
    #   - cosine-only: doesn't mention "frobnicate" but has the matching embedding
    both_id = await _seed_item(
        client, pid, title="both",
        body="we will frobnicate the widgets",
        embedding=[1.0, 0.0, 0.0],
    )
    await _seed_item(
        client, pid, title="fts-only",
        body="frobnicate the gizmos",
        embedding=[-1.0, 0.0, 0.0],
    )
    await _seed_item(
        client, pid, title="cosine-only",
        body="completely unrelated prose about cats",
        embedding=[1.0, 0.0, 0.0],
    )

    embedder = _StubEmbedder(vectors={"frobnicate": [1.0, 0.0, 0.0]}, dim=3)

    async with async_session() as db:
        out = await hybrid_search(
            db, pid, "frobnicate", top_k=3, embedder=embedder, mode="hybrid"
        )
    # both_id must be #1 — present in both channels.
    assert out[0].item.id == both_id, [(h.item.id, h.source) for h in out]
    assert out[0].source == {"fts", "cosine"}


@pytest.mark.asyncio
async def test_hybrid_search_unknown_mode_falls_back_to_hybrid(client) -> None:
    from database import async_session

    pid = _new_project(client)
    await _seed_item(client, pid, title="A", body="needlefinder is the unique token")

    async with async_session() as db:
        out = await hybrid_search(
            db, pid, "needlefinder", top_k=2,
            embedder=None, mode="totally-bogus",
        )
    # Unknown mode → defaults to hybrid → without embedder degrades to FTS
    assert len(out) >= 1
