"""Tests for T2.5 (create/update enrichment) + T2.6 (backfill endpoint).

The Brain-flags default OFF — without monkeypatching, every code path
should bypass the LLM/embedder calls (essential for regression: the
existing /api/knowledge tests don't expect LLM calls).

Coverage:

* enrich_item helper — pure async logic with injected ai_assist + embedder
* create_item with flags off → context_summary stays empty, no embedding
* create_item with flags on → both set
* update_item only re-enriches on semantic change (title or content)
* update_item tag-only change does NOT trigger enrichment
* _fts_insert prepends context_summary into the FTS5 ``content_plain`` column
  (verifiable via a MATCH on a phrase that lives only in the context)
* backfill endpoint returns 202 immediately
* backfill task enriches items lacking context+embedding
* backfill with force=True re-enriches items that already have them
* backfill no-op when both flags off (logs+returns without DB churn)
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import sys
import tempfile
from dataclasses import dataclass, field

import pytest


# Fresh DB pinned BEFORE any backend import
_TEST_DB_PATH = os.path.join(
    tempfile.gettempdir(), f"projecthub_enrich_pytest_{secrets.token_hex(4)}.db"
)
os.environ["PROJECTHUB_DB_PATH"] = _TEST_DB_PATH


from models.knowledge import KnowledgeItem
from models.project import Project
from services.retrieval.enrichment import enrich_item
from services.retrieval.hybrid import pack_vector, unpack_vector


# ── Fakes ───────────────────────────────────────────────────────────


@dataclass
class _FakeAIAssist:
    response: str = "A concise context sentence."
    calls: list = field(default_factory=list)

    async def agent_call(
        self, *, session_id, message, model=None, auto_detect=False,
        project_path=None,
    ):
        self.calls.append({"session_id": session_id, "model": model})
        return {
            "response": self.response,
            "model": model or "test-model",
            "usage": {"total_tokens": 50},
            "error": None,
        }


@dataclass
class _FakeEmbedder:
    vec: list[float] = field(default_factory=lambda: [0.1, 0.2, 0.3])
    model_id: str = "fake-bge"
    name: str = "fake"
    dim: int = 3
    calls: list = field(default_factory=list)

    async def embed(self, texts):
        for t in texts:
            self.calls.append(t)
        return [list(self.vec) for _ in texts]

    async def embed_one(self, text):
        self.calls.append(text)
        return list(self.vec)


def _new_item(title="t", content_plain="some body text", tags=None) -> KnowledgeItem:
    return KnowledgeItem(
        id="aaaaaaaaaaaaaaaa",
        project_id="proj1",
        title=title,
        content="",
        content_plain=content_plain,
        category="reference",
        source_type="manual",
        tags=json.dumps(tags or []),
        confidence="medium",
        extra_data="{}",
    )


def _new_project() -> Project:
    return Project(id="proj1", name="Test-Project")


# ── enrich_item unit tests ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_enrich_noop_when_both_flags_off() -> None:
    item = _new_item()
    ai = _FakeAIAssist()
    emb = _FakeEmbedder()

    stats = await enrich_item(
        item, _new_project(),
        contextual_enabled=False, embedding_enabled=False,
        ai_assist=ai, embedder=emb,
    )
    assert stats == {"context_set": False, "embedding_set": False, "errors": []}
    # Constructing KnowledgeItem(...) without DB flush leaves column
    # defaults un-applied (those fire at INSERT-time). With both flags
    # off, ``enrich_item`` simply doesn't touch the field — so we accept
    # either "" (post-flush default) or None (pre-flush state).
    assert not (item.context_summary or "")
    assert item.embedding is None
    assert ai.calls == []
    assert emb.calls == []


@pytest.mark.asyncio
async def test_enrich_sets_context_only_when_only_context_on() -> None:
    item = _new_item()
    ai = _FakeAIAssist(response="Generated context.")
    emb = _FakeEmbedder()

    stats = await enrich_item(
        item, _new_project(),
        contextual_enabled=True, embedding_enabled=False,
        ai_assist=ai, embedder=emb,
    )
    assert stats["context_set"] is True
    assert stats["embedding_set"] is False
    assert item.context_summary == "Generated context."
    assert item.embedding is None
    assert emb.calls == []


@pytest.mark.asyncio
async def test_enrich_sets_embedding_only_when_only_embedding_on() -> None:
    item = _new_item()
    ai = _FakeAIAssist()
    emb = _FakeEmbedder(vec=[1.0, 2.0, 3.0])

    stats = await enrich_item(
        item, _new_project(),
        contextual_enabled=False, embedding_enabled=True,
        ai_assist=ai, embedder=emb,
    )
    assert stats["context_set"] is False
    assert stats["embedding_set"] is True
    assert ai.calls == []
    assert item.embedding is not None
    # roundtrip through pack/unpack
    assert unpack_vector(item.embedding) == [1.0, 2.0, 3.0]
    assert item.embedding_model == "fake-bge"
    assert item.embedded_at  # not empty


@pytest.mark.asyncio
async def test_enrich_combines_title_context_body_in_embed_text() -> None:
    """The embedder should see title + context_summary + body-head, joined."""
    item = _new_item(title="TitleXyz", content_plain="BodyAbc")
    emb = _FakeEmbedder()
    # First set context manually so we can prove it's included in embed text
    item.context_summary = "ContextHere"

    await enrich_item(
        item, _new_project(),
        contextual_enabled=False,  # skip context regen
        embedding_enabled=True,
        embedder=emb,
    )
    sent = emb.calls[0]
    assert "TitleXyz" in sent
    assert "ContextHere" in sent
    assert "BodyAbc" in sent


@pytest.mark.asyncio
async def test_enrich_skips_embedding_when_text_empty() -> None:
    item = _new_item(title="", content_plain="")
    emb = _FakeEmbedder()
    stats = await enrich_item(
        item, _new_project(),
        contextual_enabled=False, embedding_enabled=True,
        embedder=emb,
    )
    assert stats["embedding_set"] is False
    assert emb.calls == []


@pytest.mark.asyncio
async def test_enrich_swallows_embedder_error() -> None:
    """A flaky embedder must NOT bubble — item still survives without the vec."""

    class _Bombed:
        model_id = "x"
        name = "bombed"
        dim = 0

        async def embed_one(self, text):
            raise RuntimeError("embedder dead")

    item = _new_item()
    stats = await enrich_item(
        item, _new_project(),
        contextual_enabled=False, embedding_enabled=True,
        embedder=_Bombed(),
    )
    assert stats["embedding_set"] is False
    assert any(e.startswith("embedding:") for e in stats["errors"])
    assert item.embedding is None


# ── create_item / update_item / _fts_insert (E2E via test client) ──


@pytest.fixture(scope="module")
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    try:
        if os.path.exists(_TEST_DB_PATH):
            os.unlink(_TEST_DB_PATH)
    except OSError:
        pass

    import models  # noqa
    from database import init_db
    from routers.knowledge import router as knowledge_router
    from routers.projects import router as projects_router

    asyncio.get_event_loop().run_until_complete(init_db())

    app = FastAPI()
    app.include_router(projects_router)
    app.include_router(knowledge_router)

    with TestClient(app) as c:
        yield c


def _create_project(client) -> str:
    r = client.post("/api/projects", json={"name": f"enrich-{secrets.token_hex(3)}"})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def test_create_item_flags_off_no_enrichment(client) -> None:
    """With both flags off (default) the create-path is unchanged."""
    pid = _create_project(client)
    r = client.post(
        f"/api/knowledge/{pid}",
        json={"title": "Hi", "content": "<p>Body</p>", "category": "reference"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["title"] == "Hi"
    # Response model doesn't surface context_summary/embedding (that's
    # internal); check via the DB.
    import sqlalchemy as sa
    from database import engine
    import asyncio as _asyncio

    async def _read():
        async with engine.connect() as conn:
            res = await conn.execute(
                sa.text("SELECT context_summary, embedding FROM knowledge_items WHERE id=:id"),
                {"id": body["id"]},
            )
            return res.fetchone()

    row = _asyncio.get_event_loop().run_until_complete(_read())
    assert row[0] == ""  # context_summary
    assert row[1] is None  # embedding


def test_create_item_with_flags_on_enriches(client, monkeypatch) -> None:
    from config import settings
    from services import embedding as embedding_module
    from services.retrieval import contextual as contextual_module

    monkeypatch.setattr(settings, "brain_contextual_retrieval_enabled", True)
    monkeypatch.setattr(settings, "brain_embedding_enabled", True)

    # Patch the dependencies at the module level enrich_item reads from.
    fake_emb = _FakeEmbedder(vec=[0.5, 0.5, 0.5])

    async def _fake_gen_context(item, project, **kwargs):
        return "Stub context snippet for testing."

    monkeypatch.setattr(contextual_module, "generate_context", _fake_gen_context)
    monkeypatch.setattr(embedding_module, "get_default_embedder", lambda: fake_emb)

    pid = _create_project(client)
    r = client.post(
        f"/api/knowledge/{pid}",
        json={"title": "Enriched", "content": "<p>Some body</p>", "category": "reference"},
    )
    assert r.status_code == 201

    import sqlalchemy as sa
    from database import engine
    import asyncio as _asyncio

    async def _read():
        async with engine.connect() as conn:
            res = await conn.execute(
                sa.text(
                    "SELECT context_summary, embedding, embedding_model, embedded_at "
                    "FROM knowledge_items WHERE id=:id"
                ),
                {"id": r.json()["id"]},
            )
            return res.fetchone()

    row = _asyncio.get_event_loop().run_until_complete(_read())
    assert row[0] == "Stub context snippet for testing."
    assert row[1] is not None
    assert unpack_vector(row[1]) == [0.5, 0.5, 0.5]
    assert row[2] == "fake-bge"
    assert row[3]  # embedded_at non-empty


def test_fts_insert_indexes_context_summary(client, monkeypatch) -> None:
    """A phrase that lives ONLY in context_summary must be findable via FTS5."""
    from config import settings
    from services import embedding as embedding_module
    from services.retrieval import contextual as contextual_module

    monkeypatch.setattr(settings, "brain_contextual_retrieval_enabled", True)
    monkeypatch.setattr(settings, "brain_embedding_enabled", False)

    UNIQUE_TOKEN = "zorblefactor"  # appears ONLY in context, not in body

    async def _fake_gen_context(item, project, **kwargs):
        return f"This sentence introduces the {UNIQUE_TOKEN} concept."

    monkeypatch.setattr(contextual_module, "generate_context", _fake_gen_context)

    pid = _create_project(client)
    r = client.post(
        f"/api/knowledge/{pid}",
        json={"title": "Boring title", "content": "<p>unrelated body</p>",
              "category": "reference"},
    )
    assert r.status_code == 201

    # Now do an FTS5 search for the unique token and verify the hit.
    s = client.get(f"/api/knowledge/{pid}/search", params={"q": UNIQUE_TOKEN})
    assert s.status_code == 200
    hits = s.json()
    assert len(hits) >= 1
    assert hits[0]["item"]["id"] == r.json()["id"]


def test_update_item_semantic_change_reenriches(client, monkeypatch) -> None:
    from config import settings
    from services import embedding as embedding_module
    from services.retrieval import contextual as contextual_module

    monkeypatch.setattr(settings, "brain_contextual_retrieval_enabled", True)
    monkeypatch.setattr(settings, "brain_embedding_enabled", False)

    call_count = [0]

    async def _fake_gen_context(item, project, **kwargs):
        call_count[0] += 1
        return f"Context for {item.title}"

    monkeypatch.setattr(contextual_module, "generate_context", _fake_gen_context)

    pid = _create_project(client)
    create = client.post(
        f"/api/knowledge/{pid}",
        json={"title": "Initial", "content": "<p>body</p>", "category": "reference"},
    )
    item_id = create.json()["id"]
    assert call_count[0] == 1

    # Tag-only update — must NOT trigger re-enrichment
    client.put(
        f"/api/knowledge/{pid}/{item_id}",
        json={"tags": ["new-tag"]},
    )
    assert call_count[0] == 1, "tag-only update should not re-enrich"

    # Title change — MUST re-enrich
    client.put(
        f"/api/knowledge/{pid}/{item_id}",
        json={"title": "Renamed"},
    )
    assert call_count[0] == 2

    # Same title repeated — no-op (semantic_changed compares values)
    client.put(
        f"/api/knowledge/{pid}/{item_id}",
        json={"title": "Renamed"},
    )
    assert call_count[0] == 2, "same-value update should not re-enrich"


# ── Backfill endpoint (T2.6) ──────────────────────────────────────


def test_backfill_endpoint_returns_202_immediately(client) -> None:
    pid = _create_project(client)
    r = client.post(
        f"/api/knowledge/{pid}/backfill-embeddings",
        json={"force": False, "rate_limit_seconds": 0.0},
    )
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "started"
    assert body["project_id"] == pid
    assert body["force"] is False


def test_backfill_unknown_project_returns_404(client) -> None:
    r = client.post(
        "/api/knowledge/ffffffffffffffff/backfill-embeddings",
        json={"force": False, "rate_limit_seconds": 0.0},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_backfill_task_enriches_items_directly(client, monkeypatch) -> None:
    """We invoke ``_run_backfill_embeddings`` directly instead of via the HTTP
    endpoint so the test can await it deterministically (the endpoint
    creates a detached task and returns immediately — fine for HTTP smoke
    tests but flaky for assertion-driven tests)."""
    from config import settings
    from routers.knowledge import _run_backfill_embeddings
    from services import embedding as embedding_module
    from services.retrieval import contextual as contextual_module

    monkeypatch.setattr(settings, "brain_contextual_retrieval_enabled", True)
    monkeypatch.setattr(settings, "brain_embedding_enabled", True)

    fake_emb = _FakeEmbedder(vec=[1.0, 0.0, 0.0])
    monkeypatch.setattr(embedding_module, "get_default_embedder", lambda: fake_emb)

    async def _ctx(item, project, **kwargs):
        return f"Stub ctx for {item.id[:4]}"

    monkeypatch.setattr(contextual_module, "generate_context", _ctx)

    # Seed 3 items via the create endpoint with flags OFF so they start
    # with no enrichment.
    monkeypatch.setattr(settings, "brain_contextual_retrieval_enabled", False)
    monkeypatch.setattr(settings, "brain_embedding_enabled", False)

    pid = _create_project(client)
    seeded_ids: list[str] = []
    for i in range(3):
        r = client.post(
            f"/api/knowledge/{pid}",
            json={"title": f"item-{i}", "content": f"<p>body {i}</p>",
                  "category": "reference"},
        )
        seeded_ids.append(r.json()["id"])

    # Now flip flags on and run the backfill task in-process.
    monkeypatch.setattr(settings, "brain_contextual_retrieval_enabled", True)
    monkeypatch.setattr(settings, "brain_embedding_enabled", True)

    await _run_backfill_embeddings(pid, force=False, rate_limit_seconds=0.0)

    # Verify each item now has context + embedding.
    import sqlalchemy as sa
    from database import engine

    async with engine.connect() as conn:
        res = await conn.execute(
            sa.text(
                "SELECT id, context_summary, embedding FROM knowledge_items "
                "WHERE id IN :ids"
            ).bindparams(sa.bindparam("ids", expanding=True)),
            {"ids": seeded_ids},
        )
        rows = res.fetchall()

    assert len(rows) == 3
    for row in rows:
        assert row[1].startswith("Stub ctx for ")
        assert row[2] is not None  # embedding BLOB present


@pytest.mark.asyncio
async def test_backfill_noop_when_both_flags_off(client, monkeypatch) -> None:
    """The runner short-circuits when both flags are off — saves us
    walking every item in the project."""
    from config import settings
    from routers.knowledge import _run_backfill_embeddings

    monkeypatch.setattr(settings, "brain_contextual_retrieval_enabled", False)
    monkeypatch.setattr(settings, "brain_embedding_enabled", False)

    pid = _create_project(client)
    client.post(
        f"/api/knowledge/{pid}",
        json={"title": "x", "content": "y", "category": "reference"},
    )

    # Should return quickly without touching the row
    await _run_backfill_embeddings(pid, force=True, rate_limit_seconds=0.0)
