"""Multi-strategy rerank adapter for Auto-Mode (Phase 3b).

Sits between a provider's raw hit pool and the orchestrator's Stage-3
mini-summary step. Picks the best available reranking strategy per call:

    bm25            — Stage-1 only (no LLM, no embedder, deterministic).
                      Default for the Normal-Mode profile and for tiny
                      pools (≤ ``rerank_top_k`` items — nothing to rerank).
    bm25_embedding  — Stage-1 → embed Query + Chunks via Brain's
                      ``LiteLLMEmbedder`` and cosine-sort. Deterministic,
                      parallelisable, no LLM call per chunk.
    bm25_brain      — Stage-1 → Brain's ``LLMJudgeReranker`` (T3.1).
                      Highest precision but costliest; needs
                      ``brain_reranker_enabled``.
    bm25_llm        — Stage-1 → in-module LLM-batch reranker. Pure
                      fallback when no Brain pieces are wired but the
                      Tief profile still wants semantic ordering.
    llm_only        — skip Stage-1, hand everything to the LLM batch
                      reranker. Opt-in; expensive on big pools.
    none            — no rerank at all; return input order, truncated.
    auto            — runtime pick:
                          brain_reranker_enabled + reranker_available → bm25_brain
                          brain_embedding_enabled + embedder healthy  → bm25_embedding
                          allow_llm_rerank_fallback                   → bm25_llm
                          else                                        → bm25

The adapter operates on the provider-agnostic ``Finding`` shape (NOT on
``services.retrieval.hybrid.SearchHit`` — that one is KB-specific). For
the ``bm25_brain`` strategy we synthesise minimal SearchHit-like dicts
from Findings so Brain's reranker can reuse its prompt+cache machinery
verbatim — but we hand the result back as ``Finding``s.

The adapter never raises into callers: on any failure it falls back to
the BM25 ordering (or input order if even BM25 returned nothing). This
matches Brain's `LLMJudgeReranker` contract, which is the right
defensive default — a rerank glitch should never blow up an Auto-Mode
run.

All LLM/embed calls accept an opaque ``budget`` token (will be the
``BudgetTracker`` from P3c). For now it's typed as ``Any | None`` and
the adapter calls ``budget.reserve("rerank"|"embedding", n)`` only if
non-None — keeps this module shippable before P3c lands.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable, Literal

from services.research_providers._bm25 import ScoredChunk, score_chunks
from services.research_providers.base import Finding

logger = logging.getLogger("projecthub.research.rerank")

RerankMode = Literal[
    "auto", "none", "bm25", "bm25_embedding", "bm25_brain", "bm25_llm", "llm_only",
]


# ── Strategy descriptor ────────────────────────────────────────────────────


@dataclass
class RerankResult:
    """Output of ``RerankAdapter.rerank``.

    Carries the reordered findings plus telemetry the orchestrator
    persists into ``ResearchFinding.extra_data.rerank`` so the UI can
    show which strategy ran on which finding.
    """

    findings: list[Finding]
    strategy_used: str  # one of RerankMode minus "auto"
    fallback_reason: str | None = None  # set when auto downgraded


# ── Health probes (cheap, cached for one rerank call) ──────────────────────


def _brain_reranker_available() -> bool:
    """Cheap probe: is Brain's T3.1 reranker wired and on?

    Imports lazily so test/dev environments without the brain package
    still load this module fine.
    """
    try:
        from services.retrieval.reranker import get_default_reranker

        return get_default_reranker() is not None
    except Exception:  # pragma: no cover — defensive
        return False


def _brain_embedder_available() -> bool:
    """Cheap probe: is Brain's T2.2 embedder configured?

    We only check the flag here; the actual reachability test happens
    when ``embed_one`` first runs. The adapter degrades on
    ``EmbeddingError`` at that point.
    """
    try:
        from config import settings

        return bool(getattr(settings, "brain_embedding_enabled", False))
    except Exception:  # pragma: no cover — defensive
        return False


def _pick_auto_mode(*, allow_llm_fallback: bool) -> tuple[RerankMode, str | None]:
    """Resolve ``mode="auto"`` to a concrete strategy.

    Returns ``(mode, reason)`` where ``reason`` is the first chosen
    rationale — surfaces in the UI / logs so the user can tell *why*
    they got which Stage-2 path on a given run.
    """
    if _brain_reranker_available():
        return "bm25_brain", "brain_reranker_enabled"
    if _brain_embedder_available():
        return "bm25_embedding", "brain_embedding_enabled"
    if allow_llm_fallback:
        return "bm25_llm", "llm_rerank_fallback"
    return "bm25", "no_semantic_path_available"


# ── Stage 1 (BM25) ─────────────────────────────────────────────────────────


def _bm25_top_n(query: str, findings: list[Finding], top_n: int) -> list[Finding]:
    """Run BM25 on (title + snippet) and return the top ``top_n`` findings.

    Title is doubled in the input — a manually-curated title typically
    holds more signal than the snippet's first 300 chars and BM25 has
    no way of knowing that on its own.
    """
    if not findings:
        return []

    triples = [
        (
            f.source_ref,
            f"{f.title} {f.title} {f.snippet}",
            f,
        )
        for f in findings
    ]
    scored = score_chunks(query, triples)
    return [sc.chunk for sc in scored[: max(1, top_n)]]


# ── Stage 2: embedding-cosine ──────────────────────────────────────────────


async def _rerank_embedding(
    query: str,
    findings: list[Finding],
    *,
    top_k: int,
    budget: Any | None,
) -> list[Finding]:
    """Embed query + chunks via Brain's LiteLLMEmbedder; cosine-sort.

    On any error we fall back to the input order — caller wraps this
    so the failure is logged but never raised out.
    """
    from services.embedding.litellm_router import LiteLLMEmbedder
    from services.embedding.protocol import EmbeddingError
    from services.retrieval.hybrid import cosine_similarity

    est_tokens = len(findings) * 100 + 1000
    if budget is not None and hasattr(budget, "reserve"):
        await budget.reserve("embedding", est_tokens)

    embedder = LiteLLMEmbedder()
    try:
        # Embed query + every finding's (title+snippet) in a single batch.
        texts = [query] + [f"{f.title}\n{f.snippet}" for f in findings]
        vectors = await embedder.embed(texts)
    except EmbeddingError as e:
        logger.warning("embedding rerank failed: %s", e)
        raise
    if not vectors or len(vectors) != len(texts):
        raise RuntimeError(
            f"embedder returned {len(vectors) if vectors else 0} vectors for {len(texts)} texts"
        )

    # The embedder doesn't return a token count, so we commit the estimate.
    # rerank/embedding are exempt from the total-cap; the commit is for
    # observability (snapshot.by_category["embedding"] in the run log).
    if budget is not None and hasattr(budget, "commit"):
        await budget.commit("embedding", est_tokens)

    q_vec = vectors[0]
    scored = [
        (cosine_similarity(q_vec, v), idx, f)
        for idx, (f, v) in enumerate(zip(findings, vectors[1:]))
    ]
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [f for _, _, f in scored[: max(1, top_k)]]


# ── Stage 2: Brain LLM-judge reranker ──────────────────────────────────────


async def _rerank_brain(
    query: str,
    findings: list[Finding],
    *,
    top_k: int,
    budget: Any | None,
) -> list[Finding]:
    """Hand findings to Brain's LLMJudgeReranker by adapting to SearchHit.

    Brain expects ``list[SearchHit]`` (KnowledgeItem-backed) but only
    reads ``item.id`` / ``item.title`` / ``item.content_plain`` / score
    inside its prompt. We synthesise a minimal stand-in object that
    exposes the same three attributes — enough to let Brain's prompt
    build correctly without us mutating the KB.

    On failure the Brain reranker returns the input order (its
    contract); we propagate that as-is.
    """
    from services.retrieval.hybrid import SearchHit
    from services.retrieval.reranker import get_default_reranker

    reranker = get_default_reranker()
    if reranker is None:
        raise RuntimeError("brain_reranker_enabled is off")

    est_tokens = len(findings) * 600 + 1500
    if budget is not None and hasattr(budget, "reserve"):
        await budget.reserve("rerank", est_tokens)

    # Minimal shim that quacks like KnowledgeItem for the reranker's
    # ``_format_numbered_docs`` reader.
    class _FindingItemShim:
        __slots__ = ("id", "title", "content_plain")

        def __init__(self, f: Finding):
            # Encode provider in the id so reranker output cache stays
            # unique per (query, finding-set).
            self.id = f.source_ref
            self.title = f.title or "(ohne Titel)"
            self.content_plain = f.snippet or (f.full_content or "")

    hits = [
        SearchHit(item=_FindingItemShim(f), score=0.0, source={"bm25"})  # type: ignore[arg-type]
        for f in findings
    ]
    reranked = await reranker.rerank(query, hits, top_k=top_k, db=None)

    # Brain's reranker doesn't surface token usage (it's behind its own
    # OfflineCache), so we commit the estimate. Exempt category → no
    # impact on the total-cap; this is purely audit-trail.
    if budget is not None and hasattr(budget, "commit"):
        await budget.commit("rerank", est_tokens)

    # Map back to findings by source_ref (the id we set on the shim).
    by_ref = {f.source_ref: f for f in findings}
    out: list[Finding] = []
    for h in reranked:
        ref = getattr(h.item, "id", None)
        if ref and ref in by_ref:
            out.append(by_ref[ref])
    # If the reranker dropped ids (shouldn't happen but defend), fall back
    # to BM25 order to preserve top_k stability.
    if len(out) < min(top_k, len(findings)):
        seen = {f.source_ref for f in out}
        for f in findings:
            if f.source_ref not in seen:
                out.append(f)
                if len(out) >= top_k:
                    break
    return out[: max(1, top_k)]


# ── Stage 2: in-module LLM-batch reranker (fallback) ──────────────────────


_LLM_RERANK_PROMPT = """Du bist ein Relevanz-Sortierer. Bewerte für die Anfrage jeden Eintrag 0.0–1.0.

ANFRAGE: {query}

EINTRÄGE:
{chunks}

Antworte AUSSCHLIESSLICH als kompaktes JSON-Array, eine Zeile pro Eintrag:
[
  {{"id": 1, "score": 0.0}},
  ...
]
Keine Erklärungen, keine Markdown-Fences."""


async def _rerank_llm_batch(
    query: str,
    findings: list[Finding],
    *,
    top_k: int,
    batch_size: int,
    budget: Any | None,
    model: str | None = None,
) -> list[Finding]:
    """Rerank ``findings`` via N LLM batch calls; return top_k by score.

    Uses ``services.synapse_llm.call_json`` so token accounting + JSON
    extraction are consistent with the rest of ProjectHub.

    Each batch call asks the LLM to score up to ``batch_size`` chunks.
    Multi-batch results are normalised per batch (min-max) so a strict
    batch doesn't dominate a generous one.
    """
    from services.synapse_llm import call_json

    est_per_finding = 220
    est_overhead = 800
    if budget is not None and hasattr(budget, "reserve"):
        await budget.reserve("rerank", len(findings) * est_per_finding + est_overhead)

    if not findings:
        return []

    scored: list[tuple[float, int, Finding]] = []  # (score, stable_idx, finding)
    batches = [findings[i : i + batch_size] for i in range(0, len(findings), batch_size)]
    for batch in batches:
        chunks_text = "\n".join(
            f"[{i + 1}] {f.title[:120]}\n    {f.snippet[:400]}"
            for i, f in enumerate(batch)
        )
        prompt = _LLM_RERANK_PROMPT.format(query=query, chunks=chunks_text)
        try:
            result = await call_json(prompt, model=model, session_prefix="rerank")
        except Exception as e:  # noqa: BLE001
            logger.warning("LLM rerank batch failed: %s", e)
            # Per-batch fallback: keep BM25 order with zero score
            # (those findings won't outrank scored ones).
            for f in batch:
                scored.append((0.0, len(scored), f))
            continue

        # Commit ACTUAL usage from this batch — call_json returns LLMResult
        # with a .usage dict carrying total_tokens. Falls back to the
        # per-batch estimate if the LLM client didn't surface usage.
        if budget is not None and hasattr(budget, "commit"):
            usage = getattr(result, "usage", None) or {}
            actual = (
                int(usage.get("total_tokens", 0))
                if isinstance(usage, dict)
                else 0
            )
            if actual <= 0:
                actual = len(batch) * est_per_finding + est_overhead
            await budget.commit("rerank", actual)

        parsed = result.parsed if (result and result.ok) else None
        if not isinstance(parsed, list):
            for f in batch:
                scored.append((0.0, len(scored), f))
            continue

        # Normalise scores within this batch so cross-batch comparisons
        # don't depend on the LLM's mood. If everything's tied we keep
        # the input order via stable_idx.
        raw_scores: dict[int, float] = {}
        for entry in parsed:
            if not isinstance(entry, dict):
                continue
            try:
                idx = int(entry.get("id", 0)) - 1
                sc = float(entry.get("score", 0.0))
            except (TypeError, ValueError):
                continue
            if 0 <= idx < len(batch):
                raw_scores[idx] = max(0.0, min(1.0, sc))

        if not raw_scores:
            for f in batch:
                scored.append((0.0, len(scored), f))
            continue

        max_s = max(raw_scores.values())
        for i, f in enumerate(batch):
            s = raw_scores.get(i, 0.0)
            normalised = s / max_s if max_s > 0 else 0.0
            scored.append((normalised, len(scored), f))

    scored.sort(key=lambda t: (-t[0], t[1]))
    return [f for _, _, f in scored[: max(1, top_k)]]


# ── Adapter ───────────────────────────────────────────────────────────────


class RerankAdapter:
    """Public face of P3b.

    Constructed once per pipeline run (or once globally — it's stateless).
    Pass per-call settings via ``rerank()`` arguments.
    """

    async def rerank(
        self,
        query: str,
        findings: list[Finding],
        *,
        mode: RerankMode = "auto",
        top_k: int = 8,
        bm25_top_n: int = 15,
        batch_size: int = 15,
        max_batches: int = 10,
        allow_llm_fallback: bool = True,
        llm_model: str | None = None,
        budget: Any | None = None,
    ) -> RerankResult:
        """Return ``findings`` reordered + truncated to ``top_k``.

        Never raises. On any failure inside Stage 2 we fall back to the
        BM25 ordering and tag the reason on the result.
        """
        if not findings:
            return RerankResult(findings=[], strategy_used="none")

        # Early-exit: nothing meaningful to rerank.
        if mode == "none" or len(findings) <= 1:
            return RerankResult(
                findings=findings[: max(1, top_k)],
                strategy_used="none",
            )

        # Resolve auto first so the rest of the function works with a
        # concrete strategy name.
        fallback_reason: str | None = None
        if mode == "auto":
            mode, _why = _pick_auto_mode(allow_llm_fallback=allow_llm_fallback)
            fallback_reason = _why  # purely informational

        # Stage 1 — BM25 prefilter (used by every strategy except llm_only,
        # which sends the full pool straight to the LLM batch reranker).
        if mode == "llm_only":
            stage1 = findings  # no prefilter
        else:
            stage1 = _bm25_top_n(query, findings, bm25_top_n)

        if mode == "bm25":
            return RerankResult(
                findings=stage1[: max(1, top_k)],
                strategy_used="bm25",
                fallback_reason=fallback_reason,
            )

        # Stage 2 — pluggable; on failure → fall through to BM25 order.
        try:
            if mode == "bm25_embedding":
                ranked = await _rerank_embedding(
                    query, stage1, top_k=top_k, budget=budget,
                )
                return RerankResult(
                    findings=ranked,
                    strategy_used="bm25_embedding",
                    fallback_reason=fallback_reason,
                )
            if mode == "bm25_brain":
                ranked = await _rerank_brain(
                    query, stage1, top_k=top_k, budget=budget,
                )
                return RerankResult(
                    findings=ranked,
                    strategy_used="bm25_brain",
                    fallback_reason=fallback_reason,
                )
            if mode in ("bm25_llm", "llm_only"):
                # Cap how many batches we send so a pathologically large
                # pool can't run away. Drop the tail past the cap — it
                # was already at the BM25 bottom anyway.
                capped = stage1[: batch_size * max_batches]
                ranked = await _rerank_llm_batch(
                    query, capped,
                    top_k=top_k,
                    batch_size=batch_size,
                    budget=budget,
                    model=llm_model,
                )
                return RerankResult(
                    findings=ranked,
                    strategy_used=mode,
                    fallback_reason=fallback_reason,
                )
        except Exception as e:  # noqa: BLE001 — adapter NEVER raises
            logger.warning(
                "RerankAdapter Stage-2 failed (mode=%s): %s — falling back to BM25",
                mode, e,
            )
            return RerankResult(
                findings=stage1[: max(1, top_k)],
                strategy_used="bm25",
                fallback_reason=f"stage2_failed:{type(e).__name__}",
            )

        # Defensive: unknown mode → BM25 order.
        logger.warning("RerankAdapter: unknown mode %r — using bm25", mode)
        return RerankResult(
            findings=stage1[: max(1, top_k)],
            strategy_used="bm25",
            fallback_reason=f"unknown_mode:{mode}",
        )
