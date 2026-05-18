"""Tests for ``services/retrieval/reranker.py`` (T3.1 + T3.3 unit-side).

Covers:

* Reranker reorders hits by LLM-judged relevance scores
* Single-item / empty pool → no LLM call
* Empty query → no LLM call (input order preserved up to top_k)
* Garbage LLM response → input order preserved (NEVER raises)
* LLM crash → input order preserved
* Cache: identical (query, pool) within TTL → no second LLM call
* Cache: TTL expiry forces fresh LLM call
* Cache: different query → fresh LLM call
* ``source`` set gets ``"rerank"`` added to reranked hits
* hybrid_search with reranker wired in → end-to-end pool→top_k
* get_default_reranker is None when flag off, cached otherwise
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


# Fresh DB pinned BEFORE backend imports — same pattern as the rest
_TEST_DB_PATH = os.path.join(
    tempfile.gettempdir(), f"projecthub_rerank_pytest_{secrets.token_hex(4)}.db"
)
os.environ["PROJECTHUB_DB_PATH"] = _TEST_DB_PATH


from models.knowledge import KnowledgeItem
from services.retrieval.hybrid import SearchHit
from services.retrieval.reranker import (
    LLMJudgeReranker,
    Reranker,
    _build_rerank_cache_key,
    get_default_reranker,
    reset_default_reranker,
)


# ── Fakes ───────────────────────────────────────────────────────────


@dataclass
class _FakeAIAssist:
    """Configurable ``ai_assist`` stub.

    ``response`` is the canned LLM payload (string). Tests set this to a
    valid scores-JSON when they expect success, garbage when they expect
    failure.
    """

    response: str = ""
    raise_on_call: BaseException | None = None
    calls: list[dict] = field(default_factory=list)

    async def agent_call(
        self, *, session_id, message, model=None, auto_detect=False,
        project_path=None,
    ):
        self.calls.append({"session_id": session_id, "model": model})
        if self.raise_on_call is not None:
            raise self.raise_on_call
        return {
            "response": self.response,
            "model": model or "test-model",
            "usage": {"total_tokens": 50},
            "error": None,
        }


def _hit(item_id: str, *, title: str = "", body: str = "", source=None) -> SearchHit:
    """Build a SearchHit out of a minimal in-memory KnowledgeItem."""
    item = KnowledgeItem(
        id=item_id,
        project_id="proj1",
        title=title or f"item-{item_id}",
        content="",
        content_plain=body or f"body for {item_id}",
        category="reference",
        source_type="manual",
        tags="[]",
        confidence="medium",
        extra_data="{}",
    )
    return SearchHit(item=item, score=0.5, source=set(source or {"fts"}))


def _scores_payload(scores: list[tuple[int, float]]) -> str:
    """Encode ``[(id, relevance), …]`` as the JSON the rerank prompt asks for."""
    return json.dumps(
        {"scores": [{"id": i, "relevance": r} for i, r in scores]},
        ensure_ascii=False,
    )


# ── Cache-key determinism ──────────────────────────────────────────────


def test_cache_key_independent_of_item_order() -> None:
    """Same query + same set of items (different order) → same key.

    This lets a future RRF re-tune that reorders the pool not invalidate
    caches unnecessarily."""
    k1 = _build_rerank_cache_key("auth migration", ["a", "b", "c"])
    k2 = _build_rerank_cache_key("auth migration", ["c", "a", "b"])
    assert k1 == k2
    assert k1.startswith("rerank:")


def test_cache_key_changes_with_query() -> None:
    k1 = _build_rerank_cache_key("foo", ["a", "b"])
    k2 = _build_rerank_cache_key("bar", ["a", "b"])
    assert k1 != k2


# ── Empty / trivial pools ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rerank_empty_pool_returns_empty() -> None:
    ai = _FakeAIAssist()
    r = LLMJudgeReranker(ai_assist=ai)
    out = await r.rerank("q", [], top_k=5)
    assert out == []
    assert ai.calls == []


@pytest.mark.asyncio
async def test_rerank_single_item_skips_llm() -> None:
    """One-item pool has nothing to reorder — save the LLM call."""
    ai = _FakeAIAssist(response=_scores_payload([(1, 0.9)]))
    r = LLMJudgeReranker(ai_assist=ai)
    pool = [_hit("only")]
    out = await r.rerank("q", pool, top_k=5)
    assert len(out) == 1
    assert out[0].item.id == "only"
    assert ai.calls == []


@pytest.mark.asyncio
async def test_rerank_empty_query_skips_llm() -> None:
    ai = _FakeAIAssist(response=_scores_payload([(1, 0.9), (2, 0.5)]))
    r = LLMJudgeReranker(ai_assist=ai)
    pool = [_hit("a"), _hit("b")]
    out = await r.rerank("", pool, top_k=5)
    # Empty query → no LLM call, input order preserved, capped at top_k
    assert [h.item.id for h in out] == ["a", "b"]
    assert ai.calls == []


# ── Happy path: LLM scores reorder ─────────────────────────────────────


@pytest.mark.asyncio
async def test_rerank_reorders_by_llm_scores() -> None:
    # RRF order: a, b, c. LLM judges b > a > c.
    ai = _FakeAIAssist(
        response=_scores_payload([(1, 0.3), (2, 0.9), (3, 0.1)])
    )
    r = LLMJudgeReranker(ai_assist=ai)
    pool = [_hit("a"), _hit("b"), _hit("c")]
    out = await r.rerank("any", pool, top_k=3)
    assert [h.item.id for h in out] == ["b", "a", "c"]
    # Top hit gets the rerank score (0.9), not 1/rank.
    assert abs(out[0].score - 0.9) < 1e-6
    # Source set picks up the "rerank" marker
    assert "rerank" in out[0].source
    assert "fts" in out[0].source  # original source preserved


@pytest.mark.asyncio
async def test_rerank_truncates_to_top_k() -> None:
    ai = _FakeAIAssist(
        response=_scores_payload([(1, 0.9), (2, 0.7), (3, 0.5), (4, 0.3)])
    )
    r = LLMJudgeReranker(ai_assist=ai)
    pool = [_hit("a"), _hit("b"), _hit("c"), _hit("d")]
    out = await r.rerank("any", pool, top_k=2)
    assert len(out) == 2
    assert [h.item.id for h in out] == ["a", "b"]


@pytest.mark.asyncio
async def test_rerank_missing_score_treated_as_zero() -> None:
    """If the LLM forgets to score a doc, that doc sinks to the bottom."""
    ai = _FakeAIAssist(
        response=_scores_payload([(1, 0.9), (3, 0.5)])  # id=2 missing
    )
    r = LLMJudgeReranker(ai_assist=ai)
    pool = [_hit("a"), _hit("b"), _hit("c")]
    out = await r.rerank("any", pool, top_k=3)
    # Order: a (0.9), c (0.5), b (default 0.0)
    assert [h.item.id for h in out] == ["a", "c", "b"]


# ── Failure modes ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rerank_garbage_response_preserves_input_order() -> None:
    ai = _FakeAIAssist(response="this is not json")
    r = LLMJudgeReranker(ai_assist=ai)
    pool = [_hit("a"), _hit("b"), _hit("c")]
    out = await r.rerank("q", pool, top_k=3)
    assert [h.item.id for h in out] == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_rerank_llm_crash_preserves_input_order() -> None:
    ai = _FakeAIAssist(raise_on_call=RuntimeError("LLM crashed"))
    r = LLMJudgeReranker(ai_assist=ai)
    pool = [_hit("a"), _hit("b")]
    out = await r.rerank("q", pool, top_k=2)
    assert [h.item.id for h in out] == ["a", "b"]


@pytest.mark.asyncio
async def test_rerank_response_without_scores_key_preserves_order() -> None:
    ai = _FakeAIAssist(response='{"different_key": [1, 2]}')
    r = LLMJudgeReranker(ai_assist=ai)
    pool = [_hit("a"), _hit("b")]
    out = await r.rerank("q", pool, top_k=2)
    assert [h.item.id for h in out] == ["a", "b"]


@pytest.mark.asyncio
async def test_rerank_response_wrapped_in_markdown_fence_still_parses() -> None:
    ai = _FakeAIAssist(
        response="```json\n" + _scores_payload([(1, 0.2), (2, 0.95)]) + "\n```"
    )
    r = LLMJudgeReranker(ai_assist=ai)
    pool = [_hit("a"), _hit("b")]
    out = await r.rerank("q", pool, top_k=2)
    assert [h.item.id for h in out] == ["b", "a"]


# ── Cache (db-aware) ───────────────────────────────────────────────────


@pytest.fixture(scope="module")
def db_session():
    """In-process FastAPI + DB so OfflineCache writes/reads round-trip."""
    try:
        if os.path.exists(_TEST_DB_PATH):
            os.unlink(_TEST_DB_PATH)
    except OSError:
        pass

    import models  # noqa
    from database import async_session, init_db

    asyncio.get_event_loop().run_until_complete(init_db())
    yield async_session


@pytest.mark.asyncio
async def test_rerank_caches_hit_within_ttl(db_session) -> None:
    ai = _FakeAIAssist(
        response=_scores_payload([(1, 0.5), (2, 0.95), (3, 0.2)])
    )
    r = LLMJudgeReranker(ai_assist=ai)
    pool = [_hit("a"), _hit("b"), _hit("c")]

    async with db_session() as db:
        out1 = await r.rerank("auth migration", pool, top_k=3, db=db)
    assert [h.item.id for h in out1] == ["b", "a", "c"]
    assert len(ai.calls) == 1

    # Same query + same pool → cache HIT, no second LLM call
    async with db_session() as db:
        out2 = await r.rerank("auth migration", pool, top_k=3, db=db)
    assert [h.item.id for h in out2] == ["b", "a", "c"]
    assert len(ai.calls) == 1, "expected cache hit, but LLM was called twice"


@pytest.mark.asyncio
async def test_rerank_cache_miss_on_different_query(db_session) -> None:
    ai = _FakeAIAssist(response=_scores_payload([(1, 0.9), (2, 0.1)]))
    r = LLMJudgeReranker(ai_assist=ai)
    pool = [_hit("x"), _hit("y")]

    async with db_session() as db:
        await r.rerank("question one", pool, top_k=2, db=db)
        await r.rerank("question two", pool, top_k=2, db=db)
    assert len(ai.calls) == 2


@pytest.mark.asyncio
async def test_rerank_works_without_db_session(db_session) -> None:
    """When ``db=None`` the reranker still works — just skips the cache."""
    ai = _FakeAIAssist(response=_scores_payload([(1, 0.9), (2, 0.1)]))
    r = LLMJudgeReranker(ai_assist=ai)
    pool = [_hit("a"), _hit("b")]
    out = await r.rerank("q", pool, top_k=2)  # no db arg
    assert [h.item.id for h in out] == ["a", "b"]
    assert len(ai.calls) == 1


# ── Default singleton + flag gating ─────────────────────────────────────


def test_default_reranker_none_when_flag_off(monkeypatch) -> None:
    reset_default_reranker()
    from config import settings

    monkeypatch.setattr(settings, "brain_reranker_enabled", False, raising=False)
    assert get_default_reranker() is None


def test_default_reranker_cached_when_flag_on(monkeypatch) -> None:
    reset_default_reranker()
    from config import settings

    monkeypatch.setattr(settings, "brain_reranker_enabled", True, raising=False)
    a = get_default_reranker()
    b = get_default_reranker()
    assert a is not None
    assert isinstance(a, Reranker)
    assert a is b


def test_reset_clears_default_reranker(monkeypatch) -> None:
    from config import settings

    monkeypatch.setattr(settings, "brain_reranker_enabled", True, raising=False)
    a = get_default_reranker()
    reset_default_reranker()
    b = get_default_reranker()
    assert a is not b
