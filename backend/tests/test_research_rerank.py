"""Tests for the multi-strategy RerankAdapter (P3b).

Covers:

    * ``mode="auto"`` runtime picker (flag matrix)
    * Each concrete strategy in isolation: bm25, bm25_embedding,
      bm25_brain, bm25_llm, llm_only, none
    * Failure modes — every Stage-2 strategy falls back to BM25 order
      on any exception (the adapter MUST NOT raise into the caller)
    * Top-K + bm25_top_n truncation
    * Budget reservation (mock budget tracker counts calls)
    * Empty / single-item input short-circuits

All LLM/embedder calls are monkeypatched — no real network.
"""
from __future__ import annotations

import asyncio
import os
import secrets
import tempfile

import pytest

_TEST_DB_PATH = os.path.join(
    tempfile.gettempdir(),
    f"projecthub_pytest_rerank_{secrets.token_hex(4)}.db",
)
os.environ["PROJECTHUB_DB_PATH"] = _TEST_DB_PATH


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── Fixtures ───────────────────────────────────────────────────────────────


def _f(ref: str, title: str, snippet: str = ""):
    from services.research_providers.base import Finding

    return Finding(
        provider_key="test",
        source_ref=ref,
        title=title,
        snippet=snippet,
    )


def _findings() -> list:
    """Mixed bag where BM25 unambiguously prefers c1+c2 for 'PKCE'."""
    return [
        _f("c1", "PKCE flow", "Service X uses OAuth2 PKCE for auth"),
        _f("c2", "PKCE rollout", "rolling out PKCE across all services"),
        _f("c3", "weather", "report sunny tomorrow"),
        _f("c4", "policy", "refresh-token policy 90 days"),
        _f("c5", "other", "totally unrelated"),
    ]


class _FakeBudget:
    """Mock for the future BudgetTracker — just counts reservations."""

    def __init__(self):
        self.reservations: list[tuple[str, int]] = []

    async def reserve(self, category: str, tokens: int) -> None:
        self.reservations.append((category, tokens))


# ── mode="bm25" / "none" ───────────────────────────────────────────────────


def test_bm25_mode_orders_by_score_and_truncates():
    from services.research_rerank import RerankAdapter

    adapter = RerankAdapter()
    result = _run(adapter.rerank(
        "PKCE flow", _findings(), mode="bm25", top_k=2,
    ))
    assert result.strategy_used == "bm25"
    refs = [f.source_ref for f in result.findings]
    assert refs == ["c1", "c2"]


def test_none_mode_returns_input_order_truncated():
    from services.research_rerank import RerankAdapter

    adapter = RerankAdapter()
    result = _run(adapter.rerank(
        "PKCE", _findings(), mode="none", top_k=3,
    ))
    assert result.strategy_used == "none"
    refs = [f.source_ref for f in result.findings]
    assert refs == ["c1", "c2", "c3"]  # input order


def test_empty_input_returns_none_strategy():
    from services.research_rerank import RerankAdapter

    result = _run(RerankAdapter().rerank("anything", [], mode="bm25"))
    assert result.strategy_used == "none"
    assert result.findings == []


def test_single_item_short_circuits_to_none():
    """One finding has nothing to rerank — adapter avoids the LLM call."""
    from services.research_rerank import RerankAdapter

    result = _run(RerankAdapter().rerank(
        "PKCE", [_f("c1", "Only one")], mode="bm25_llm",
    ))
    assert result.strategy_used == "none"
    assert len(result.findings) == 1


# ── mode="auto" picker ─────────────────────────────────────────────────────


def test_auto_picks_brain_reranker_when_available(monkeypatch):
    from services.research_rerank import RerankAdapter
    import services.research_rerank as rr

    monkeypatch.setattr(rr, "_brain_reranker_available", lambda: True)
    monkeypatch.setattr(rr, "_brain_embedder_available", lambda: True)

    captured: dict = {}

    async def fake_brain(query, findings, *, top_k, budget):
        captured["called"] = True
        return findings[:top_k]

    monkeypatch.setattr(rr, "_rerank_brain", fake_brain)

    result = _run(RerankAdapter().rerank(
        "PKCE", _findings(), mode="auto", top_k=2,
    ))
    assert captured.get("called")
    assert result.strategy_used == "bm25_brain"
    assert result.fallback_reason == "brain_reranker_enabled"


def test_auto_falls_to_embedding_when_no_brain_reranker(monkeypatch):
    from services.research_rerank import RerankAdapter
    import services.research_rerank as rr

    monkeypatch.setattr(rr, "_brain_reranker_available", lambda: False)
    monkeypatch.setattr(rr, "_brain_embedder_available", lambda: True)

    async def fake_embed(query, findings, *, top_k, budget):
        return findings[:top_k]

    monkeypatch.setattr(rr, "_rerank_embedding", fake_embed)

    result = _run(RerankAdapter().rerank(
        "PKCE", _findings(), mode="auto", top_k=2,
    ))
    assert result.strategy_used == "bm25_embedding"
    assert result.fallback_reason == "brain_embedding_enabled"


def test_auto_falls_to_llm_fallback_when_allowed_and_brain_off(monkeypatch):
    from services.research_rerank import RerankAdapter
    import services.research_rerank as rr

    monkeypatch.setattr(rr, "_brain_reranker_available", lambda: False)
    monkeypatch.setattr(rr, "_brain_embedder_available", lambda: False)

    async def fake_llm(query, findings, *, top_k, batch_size, budget, model):
        return findings[:top_k]

    monkeypatch.setattr(rr, "_rerank_llm_batch", fake_llm)

    result = _run(RerankAdapter().rerank(
        "PKCE", _findings(), mode="auto", top_k=2,
        allow_llm_fallback=True,
    ))
    assert result.strategy_used == "bm25_llm"
    assert result.fallback_reason == "llm_rerank_fallback"


def test_auto_lands_on_bm25_when_nothing_else_available(monkeypatch):
    from services.research_rerank import RerankAdapter
    import services.research_rerank as rr

    monkeypatch.setattr(rr, "_brain_reranker_available", lambda: False)
    monkeypatch.setattr(rr, "_brain_embedder_available", lambda: False)

    result = _run(RerankAdapter().rerank(
        "PKCE", _findings(), mode="auto", top_k=2,
        allow_llm_fallback=False,
    ))
    assert result.strategy_used == "bm25"
    assert result.fallback_reason == "no_semantic_path_available"


# ── mode="bm25_embedding" ──────────────────────────────────────────────────


def test_embedding_mode_uses_brain_embedder_and_sorts(monkeypatch):
    """A fake embedder where the query vector aligns with c2 →
    c2 must come first regardless of BM25 order."""
    from services.research_rerank import RerankAdapter
    import services.embedding.litellm_router as lr

    class _FakeEmbedder:
        async def embed(self, texts):
            # texts[0] is the query; texts[1..] are the findings (in
            # BM25-prefiltered order). We make text-aligned vectors so
            # cosine puts c2 first.
            out = []
            for t in texts:
                if "query" in t.lower() or "PKCE" in t:
                    out.append([1.0, 0.0, 0.0])
                else:
                    out.append([0.0, 1.0, 0.0])
            # Force c2 to align with the query.
            for i, t in enumerate(texts[1:], start=1):
                if "rollout" in t.lower():
                    out[i] = [1.0, 0.0, 0.0]
            return out

        async def embed_one(self, text):
            return (await self.embed([text]))[0]

    monkeypatch.setattr(lr, "LiteLLMEmbedder", lambda: _FakeEmbedder())

    budget = _FakeBudget()
    result = _run(RerankAdapter().rerank(
        "PKCE rollout", _findings(),
        mode="bm25_embedding",
        top_k=2,
        budget=budget,
    ))
    assert result.strategy_used == "bm25_embedding"
    refs = [f.source_ref for f in result.findings]
    assert refs[0] == "c2", f"c2 should be top after embed-cosine, got {refs}"
    assert any(cat == "embedding" for cat, _ in budget.reservations)


def test_embedding_failure_falls_back_to_bm25(monkeypatch):
    from services.research_rerank import RerankAdapter
    import services.embedding.litellm_router as lr
    from services.embedding.protocol import EmbeddingError

    class _BrokenEmbedder:
        async def embed(self, texts):
            raise EmbeddingError("upstream down")

    monkeypatch.setattr(lr, "LiteLLMEmbedder", lambda: _BrokenEmbedder())

    result = _run(RerankAdapter().rerank(
        "PKCE", _findings(), mode="bm25_embedding", top_k=2,
    ))
    # Adapter MUST NOT raise.
    assert result.strategy_used == "bm25"
    assert result.fallback_reason and "stage2_failed" in result.fallback_reason
    assert result.findings[0].source_ref == "c1"


# ── mode="bm25_brain" ──────────────────────────────────────────────────────


def test_brain_mode_uses_default_reranker_with_finding_shim(monkeypatch):
    from services.research_rerank import RerankAdapter
    import services.retrieval.reranker as rer

    captured_ids: list[str] = []

    class _FakeBrainReranker:
        async def rerank(self, query, hits, *, top_k, db=None):
            for h in hits:
                captured_ids.append(h.item.id)
            # Reverse so the test can detect the rerank actually applied.
            return list(reversed(hits[:top_k]))

    monkeypatch.setattr(rer, "get_default_reranker", lambda: _FakeBrainReranker())

    result = _run(RerankAdapter().rerank(
        "PKCE", _findings(),
        mode="bm25_brain",
        top_k=3,
        bm25_top_n=4,
    ))
    assert result.strategy_used == "bm25_brain"
    # We passed the BM25 top-4 through the shim into Brain.
    assert len(captured_ids) >= 2
    assert all(cid.startswith("c") for cid in captured_ids)


def test_brain_mode_falls_back_when_reranker_not_configured(monkeypatch):
    """brain_reranker_enabled=False → get_default_reranker returns None →
    fall back to BM25 order."""
    from services.research_rerank import RerankAdapter
    import services.retrieval.reranker as rer

    monkeypatch.setattr(rer, "get_default_reranker", lambda: None)

    result = _run(RerankAdapter().rerank(
        "PKCE", _findings(), mode="bm25_brain", top_k=2,
    ))
    assert result.strategy_used == "bm25"
    assert result.fallback_reason and "stage2_failed" in result.fallback_reason


# ── mode="bm25_llm" ────────────────────────────────────────────────────────


def test_llm_mode_uses_call_json_and_normalises_scores(monkeypatch):
    from services.research_rerank import RerankAdapter
    import services.research_rerank as rr

    async def fake_call_json(prompt, model=None, session_prefix=None):
        # Always says batch index 2 is best (1-based id=2).
        class R:
            parsed = [{"id": 1, "score": 0.1}, {"id": 2, "score": 0.9}]
            ok = True
            usage = {"total_tokens": 100}
        return R()

    # Patch where the symbol is resolved (inside research_rerank).
    import services.synapse_llm as sll
    monkeypatch.setattr(sll, "call_json", fake_call_json)

    budget = _FakeBudget()
    result = _run(RerankAdapter().rerank(
        "PKCE", _findings(),
        mode="bm25_llm",
        top_k=2,
        bm25_top_n=2,
        batch_size=2,
        budget=budget,
    ))
    assert result.strategy_used == "bm25_llm"
    # bm25_top_n=2 → stage1 yields c1,c2 (BM25 winners) → LLM picks index 2
    # → c2 should land first.
    assert result.findings[0].source_ref == "c2"
    assert result.findings[1].source_ref == "c1"
    assert any(cat == "rerank" for cat, _ in budget.reservations)


def test_llm_mode_malformed_json_falls_back_to_zero_scores(monkeypatch):
    """When the LLM returns non-array junk, the batch keeps BM25 order
    (zero scores) — adapter still doesn't raise."""
    from services.research_rerank import RerankAdapter
    import services.synapse_llm as sll

    async def fake_call_json(*a, **k):
        class R:
            parsed = "not a list at all"
            ok = True
            usage = {}
        return R()

    monkeypatch.setattr(sll, "call_json", fake_call_json)

    result = _run(RerankAdapter().rerank(
        "PKCE", _findings(),
        mode="bm25_llm",
        top_k=2,
        bm25_top_n=3,
        batch_size=3,
    ))
    assert result.strategy_used == "bm25_llm"
    # Tied at zero → input (BM25) order survives → c1 first.
    assert result.findings[0].source_ref == "c1"


def test_llm_mode_caps_at_max_batches(monkeypatch):
    """A pathological pool of 100 must not produce 100/15 = ~7 LLM calls
    when max_batches=2."""
    from services.research_rerank import RerankAdapter
    import services.synapse_llm as sll

    call_count = {"n": 0}

    async def counting_call_json(*a, **k):
        call_count["n"] += 1

        class R:
            parsed = []
            ok = True
            usage = {}
        return R()

    monkeypatch.setattr(sll, "call_json", counting_call_json)

    big_pool = [_f(f"c{i}", f"item {i}", f"snippet {i}") for i in range(100)]
    _run(RerankAdapter().rerank(
        "item", big_pool,
        mode="bm25_llm",
        top_k=5,
        bm25_top_n=100,  # let BM25 keep them all
        batch_size=15,
        max_batches=2,
    ))
    assert call_count["n"] <= 2


# ── mode="llm_only" ────────────────────────────────────────────────────────


def test_llm_only_skips_bm25_prefilter(monkeypatch):
    """``llm_only`` hands the full pool to the LLM (capped by max_batches),
    no BM25 cut. We assert that the call sees more items than bm25_top_n."""
    from services.research_rerank import RerankAdapter
    import services.synapse_llm as sll

    seen_batch_sizes: list[int] = []

    async def counting_call_json(prompt, model=None, session_prefix=None):
        # Count how many "[N] " items appear in the prompt.
        import re
        seen_batch_sizes.append(len(re.findall(r"^\[\d+\]", prompt, flags=re.M)))

        class R:
            parsed = []
            ok = True
            usage = {}
        return R()

    monkeypatch.setattr(sll, "call_json", counting_call_json)

    big_pool = [_f(f"c{i}", f"item {i}") for i in range(20)]
    _run(RerankAdapter().rerank(
        "item", big_pool,
        mode="llm_only",
        top_k=5,
        bm25_top_n=3,  # would normally cut to 3 — must be ignored
        batch_size=20,
        max_batches=1,
    ))
    # Without bm25 prefilter, the LLM saw the full 20 (capped by batch_size).
    assert seen_batch_sizes == [20]
