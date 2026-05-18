"""Hybrid retrieval with Reciprocal-Rank-Fusion (T2.7).

Combines two ranked lists:

    FTS5 (BM25)   — keyword/term matches via ``knowledge_items_fts``
    Cosine        — dot-product on packed embedding BLOBs

into a single Top-K list using **Reciprocal Rank Fusion** (k=60).

Why RRF instead of weighted score combination:
    * BM25 scores and cosine similarities live on different scales
      (typically ``-50..0`` and ``-1..+1``), so a naive linear combo
      requires per-corpus tuning. RRF only uses ranks; it's
      hyperparameter-free at the application layer.
    * RRF is robust to either side returning fewer than top_k items
      (one channel can be empty and the fused list is still sensible).
    * Cormack et al. 2009 showed RRF beats most learned combinations
      on TREC; the same result has held up on RAG benchmarks since 2024.

Pure-Python implementation — no numpy. With <5000 items per project the
cost is ~50ms which is dwarfed by the network round-trip to the embedder.
For larger corpora the right answer is a vector index (faiss/sqlite-vec),
not a numpy upgrade.

Public surface:

    pack_vector(vec)       — list[float] → bytes (float32 little-endian)
    unpack_vector(blob)    — bytes → list[float]
    cosine_similarity(a,b) — pure-python cosine in [-1, 1]
    rrf_merge(rankings,k)  — list-of-lists → fused ranking
    async hybrid_search(db, project_id, query, top_k, embedder, mode)
                           — db-aware orchestrator, used by /search
"""
from __future__ import annotations

import logging
import math
import re
import struct
from dataclasses import dataclass, field
from typing import Iterable

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from models.knowledge import KnowledgeItem
from services.embedding.protocol import Embedder, EmbeddingError

logger = logging.getLogger("projecthub.hybrid")


# ── Pure helpers (no DB, no I/O) ─────────────────────────────────────────


def pack_vector(vec: Iterable[float]) -> bytes:
    """Pack a vector as little-endian float32 BLOB.

    ``embedding`` column stores these blobs; ``unpack_vector`` is the
    inverse. Keep the dtype stable (float32) so cross-process and
    cross-version reads agree.
    """
    seq = tuple(float(v) for v in vec)
    return struct.pack(f"<{len(seq)}f", *seq)


def unpack_vector(blob: bytes | None) -> list[float]:
    """Unpack a float32 BLOB. Returns ``[]`` on ``None`` / empty / odd length."""
    if not blob:
        return []
    n, rem = divmod(len(blob), 4)
    if rem or n == 0:
        return []
    return list(struct.unpack(f"<{n}f", blob))


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine in [-1, 1]; returns 0 for zero-length / dim-mismatch.

    Uses ``sum`` over generator pairs — Python's C-level reduction is
    fast enough for any project that fits the no-numpy budget.
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / math.sqrt(norm_a * norm_b)


def rrf_merge(rankings: list[list[str]], *, k: int = 60) -> list[str]:
    """Reciprocal Rank Fusion over N ranked id-lists.

    Args:
        rankings: each inner list is a ranked sequence of ids (best first).
            Lists may have different lengths and need not share items.
        k: RRF constant (default 60 per Cormack et al.). Higher k flattens
            the ranking influence of any single channel.

    Returns:
        A fused list of unique ids, best first. An id present in multiple
        rankings is boosted; an id present in only one channel still
        appears, just lower.
    """
    if not rankings:
        return []
    scores: dict[str, float] = {}
    for ranked in rankings:
        if not ranked:
            continue
        for rank, item_id in enumerate(ranked, start=1):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank)
    # Stable secondary sort: id when scores tie so the order is reproducible
    # across runs and test runs.
    return [
        item_id for item_id, _ in sorted(
            scores.items(), key=lambda kv: (-kv[1], kv[0])
        )
    ]


# ── FTS5 sanitiser (mirrors the one in routers/knowledge.py) ────────────


_FTS_RESERVED = {"AND", "OR", "NOT", "NEAR"}


def _sanitize_fts_query(query: str) -> str:
    """Quote-wrap each term so an FTS5 ``MATCH`` is robust to user input."""
    terms: list[str] = []
    for word in query.split():
        cleaned = re.sub(r"[^\wäöüÄÖÜß]", "", word)
        if cleaned and len(cleaned) >= 2 and cleaned.upper() not in _FTS_RESERVED:
            terms.append(f'"{cleaned}"')
    return " OR ".join(terms)


# ── DB-aware searches ────────────────────────────────────────────────────


async def _fts_top_k(
    db: AsyncSession, project_id: str, query: str, top_k: int
) -> list[str]:
    """Top-K item ids ranked by FTS5 BM25. Empty list on parse failure."""
    sanitized = _sanitize_fts_query(query)
    if not sanitized:
        return []
    try:
        res = await db.execute(
            text(
                "SELECT ki.id FROM knowledge_items_fts fts "
                "JOIN knowledge_items ki ON ki.rowid = fts.rowid "
                "WHERE knowledge_items_fts MATCH :q "
                "AND ki.project_id = :pid "
                "ORDER BY fts.rank LIMIT :lim"
            ),
            {"q": sanitized, "pid": project_id, "lim": top_k},
        )
        return [row[0] for row in res.all()]
    except Exception as e:
        logger.debug("FTS5 search failed for project=%s: %s", project_id, e)
        return []


async def _cosine_top_k(
    db: AsyncSession,
    project_id: str,
    query_vector: list[float],
    top_k: int,
) -> list[str]:
    """Top-K item ids by cosine similarity over embedded items in the project.

    Loads (id, embedding) for every item with a non-null embedding. With
    <5000 items per project the in-process cost is negligible; the
    network round-trip to the embedder dominates the search latency
    anyway.
    """
    if not query_vector:
        return []
    res = await db.execute(
        select(KnowledgeItem.id, KnowledgeItem.embedding)
        .where(KnowledgeItem.project_id == project_id)
        .where(KnowledgeItem.embedding.is_not(None))
    )
    scored: list[tuple[float, str]] = []
    for item_id, blob in res.all():
        vec = unpack_vector(blob)
        if not vec:
            continue
        score = cosine_similarity(query_vector, vec)
        if score > 0.0:
            scored.append((score, item_id))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [item_id for _, item_id in scored[:top_k]]


# ── Public orchestrator ──────────────────────────────────────────────────


@dataclass
class SearchHit:
    """One scored result of ``hybrid_search``.

    ``score`` is the fused RRF score in mode=hybrid; the raw cosine
    or FTS5 score otherwise. ``source`` says which channel(s) contributed:
    ``{"fts", "cosine"}``.
    """

    item: KnowledgeItem
    score: float
    source: set[str] = field(default_factory=set)


async def hybrid_search(
    db: AsyncSession,
    project_id: str,
    query: str,
    *,
    top_k: int = 8,
    pool_size: int = 30,
    embedder: Embedder | None = None,
    mode: str = "hybrid",
    reranker: "object | None" = None,
) -> list[SearchHit]:
    """Search the project for items matching ``query``.

    Args:
        db: SQLAlchemy async session (caller owns commit semantics — read-only here).
        project_id: project to search inside.
        query: the user's search text.
        top_k: how many hits to return at the end.
        pool_size: per-channel candidate pool size before fusion.
            Larger pool → better recall for the rerank step in T3; default
            30 matches the design doc.
        embedder: required for ``mode`` in {hybrid, cosine}. ``None`` falls
            back to ``fts`` regardless of mode.
        mode: ``"fts"`` | ``"cosine"`` | ``"hybrid"``. Unknown modes default
            to hybrid.
        reranker: optional ``Reranker`` (see ``services/retrieval/reranker``).
            When given AND the fused pool has ≥2 items, the reranker
            reorders the pool by LLM-judged relevance before truncation
            to ``top_k``. ``None`` skips Stage 2 (caller gets RRF order).
            The reranker MUST be a Reranker-protocol instance; typing is
            ``object`` to avoid circular-import on the hybrid → reranker
            edge.

    Returns:
        Top-K ``SearchHit`` list ordered best-first.
    """
    if mode not in ("fts", "cosine", "hybrid"):
        mode = "hybrid"

    # If the caller asked for hybrid/cosine but didn't supply an embedder,
    # degrade silently to FTS — this is the "embedding disabled" code path.
    if mode != "fts" and embedder is None:
        mode = "fts"

    fts_ids: list[str] = []
    cos_ids: list[str] = []

    if mode in ("fts", "hybrid"):
        fts_ids = await _fts_top_k(db, project_id, query, pool_size)

    if mode in ("cosine", "hybrid") and embedder is not None:
        try:
            query_vec = await embedder.embed_one(query)
        except EmbeddingError as e:
            logger.warning(
                "hybrid_search: embedder error for project=%s: %s",
                project_id, e,
            )
            query_vec = []
        cos_ids = await _cosine_top_k(db, project_id, query_vec, pool_size)

    # ── Fuse ──
    if mode == "fts":
        fused = fts_ids
    elif mode == "cosine":
        fused = cos_ids
    else:
        fused = rrf_merge([fts_ids, cos_ids])

    if not fused:
        return []

    # When a reranker is plumbed in, hydrate the FULL pool (up to pool_size)
    # — Stage 2 needs all candidates available for the LLM-judge. Without
    # a reranker we slice to top_k immediately to save the hydrate round-trip.
    pool_for_hydrate = fused if reranker is not None else fused[:top_k]
    rank_of = {item_id: i + 1 for i, item_id in enumerate(pool_for_hydrate)}

    # ── Hydrate KnowledgeItems for the pool ids, preserving order ──
    items_res = await db.execute(
        select(KnowledgeItem).where(KnowledgeItem.id.in_(pool_for_hydrate))
    )
    items_by_id = {it.id: it for it in items_res.scalars().all()}

    fts_set = set(fts_ids)
    cos_set = set(cos_ids)
    pool_hits: list[SearchHit] = []
    for item_id in pool_for_hydrate:
        item = items_by_id.get(item_id)
        if item is None:
            continue
        source: set[str] = set()
        if item_id in fts_set:
            source.add("fts")
        if item_id in cos_set:
            source.add("cosine")
        # Score: 1.0 / rank gives a stable [0..1] approximation for callers
        # that just want "best first ordering"; RRF's raw score is harder
        # to interpret cross-query.
        score = 1.0 / rank_of[item_id]
        pool_hits.append(SearchHit(item=item, score=score, source=source))

    # ── Stage 2 — optional rerank ─────────────────────────────────────
    # The reranker MUST NOT raise; on failure it returns the input order.
    # We respect ``top_k`` regardless of whether rerank actually applied.
    if reranker is not None and len(pool_hits) > 1:
        try:
            return await reranker.rerank(query, pool_hits, top_k=top_k, db=db)
        except Exception as e:  # noqa: BLE001 — never sink the search call
            logger.warning(
                "hybrid_search: reranker raised, falling back to RRF order: %s", e
            )

    return pool_hits[:top_k]
