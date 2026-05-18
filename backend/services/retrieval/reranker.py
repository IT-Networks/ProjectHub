"""Reranker — second-stage relevance ordering (P3, T3.1).

The retrieval pipeline runs in two stages:

    Stage 1  retrieve broadly        FTS5 ∪ Cosine, fused via RRF, top-30
    Stage 2  rerank precisely        LLM-as-Judge over the top-30, return top-K

Stage 2 is what this module owns. The reason for splitting:
    * RRF gives high recall (rarely misses a relevant doc) but mediocre
      precision (the order inside the top-30 is fuzzy).
    * A cross-encoder / LLM-as-Judge gives high precision (good ordering)
      but is too expensive to run over the entire corpus.

Two-stage = recall from Stage 1, precision from Stage 2. The Cormack+Cohere
literature is consistent: +10–35% accuracy over RAG with vanilla retrieval.

This module provides:

    Reranker            Protocol — pluggable backends (mirror Embedder)
    LLMJudgeReranker    default — one batched LLM call over the pool,
                                  cached for 5 min via OfflineCache
    get_default_reranker / reset_default_reranker
                        — settings-gated lazy singleton

Caching: ``(query, sorted_item_ids)`` → JSON list of ``{id, score, reason}``.
Re-asks of the same question within the TTL window are free.

The reranker NEVER raises into callers: on LLM/parse failure it returns
``items`` unchanged (input order preserved). The caller's call-site
treats that case as "rerank wasn't applied" which is identical behavior
to ``brain_reranker_enabled=False``.
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from models.cache import OfflineCache
from services.retrieval.hybrid import SearchHit

logger = logging.getLogger("projecthub.reranker")


# Defaults — tuned for an interactive chat-context use-case.
_DEFAULT_TTL_MINUTES = 5
_DEFAULT_TOP_K = 8

# How much of each item we feed into the rerank prompt. Title is always
# kept verbatim; body is capped so a single huge item can't dominate the
# token budget.
_MAX_TITLE_CHARS = 120
_MAX_BODY_CHARS = 400


# ── Protocol ────────────────────────────────────────────────────────────


@runtime_checkable
class Reranker(Protocol):
    """A pluggable second-stage reranker."""

    name: str

    async def rerank(
        self,
        query: str,
        hits: list[SearchHit],
        *,
        top_k: int = _DEFAULT_TOP_K,
        db: AsyncSession | None = None,
    ) -> list[SearchHit]:
        """Return ``hits`` reordered by query relevance, truncated to ``top_k``.

        MUST NOT raise; on failure (LLM down, parse error) return the
        input order capped at ``top_k`` — caller can't tell whether
        rerank was applied beyond inspecting ``SearchHit.source``.
        """
        ...


# ── LLM-as-Judge implementation ────────────────────────────────────────


_RERANK_PROMPT = """Du bewertest, wie relevant jedes Dokument für die Suchanfrage des Nutzers ist.

SUCHE: {query}

DOKUMENTE:
{numbered_docs}

Bewerte jedes Dokument auf einer Skala 0.0–1.0:
- 1.0 = direkt antwortet auf die Suche
- 0.7 = stark verwandt
- 0.4 = locker verwandt
- 0.0 = nicht relevant

Antworte AUSSCHLIESSLICH als valides JSON, keine Erklärung, kein Markdown:
{{
  "scores": [
    {{"id": 1, "relevance": 0.0}},
    {{"id": 2, "relevance": 0.0}}
  ]
}}

Regeln:
- Eine ``id`` pro Dokument oben — gleiche Reihenfolge, gleiche Zählung.
- ``relevance`` zwischen 0.0 und 1.0 (inklusive).
- Pessimistisch sein: lieber 0.3 als 0.6, wenn nicht eindeutig relevant."""


def _build_rerank_cache_key(query: str, item_ids: list[str]) -> str:
    """Deterministic cache key — same query + same pool == same answer.

    Sorted ids so the cache hit is independent of the upstream RRF order
    (which is itself deterministic, but a future re-tune of RRF shouldn't
    invalidate caches unnecessarily).
    """
    payload = query.strip() + "|" + "|".join(sorted(item_ids))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
    return f"rerank:{digest}"


class LLMJudgeReranker:
    """Default reranker — batched LLM call, OfflineCache TTL, tolerant to LLM jitter."""

    name = "llm-judge"

    def __init__(
        self,
        *,
        ai_assist: Any | None = None,
        model: str | None = None,
        ttl_minutes: int = _DEFAULT_TTL_MINUTES,
    ) -> None:
        self._ai_assist_override = ai_assist
        self._model = model
        self._ttl = timedelta(minutes=max(0, ttl_minutes))

    async def rerank(
        self,
        query: str,
        hits: list[SearchHit],
        *,
        top_k: int = _DEFAULT_TOP_K,
        db: AsyncSession | None = None,
    ) -> list[SearchHit]:
        if not hits:
            return []
        if not query or not query.strip():
            return hits[:top_k]
        # Single-item pool → nothing to reorder, save the LLM call.
        if len(hits) <= 1:
            return hits[:top_k]

        item_ids = [h.item.id for h in hits]
        cache_key = _build_rerank_cache_key(query, item_ids)

        # ── Cache lookup ───────────────────────────────────────────
        cached = await self._cache_get(db, cache_key) if db is not None else None
        if cached is not None:
            ordered = self._apply_scores(hits, cached)
            return ordered[:top_k]

        # ── LLM call ───────────────────────────────────────────────
        scores = await self._call_llm(query, hits)
        if scores is None:
            # Fail-quiet — keep input order
            logger.debug("rerank: LLM unusable, falling back to RRF order")
            return hits[:top_k]

        # Persist for cache hits within TTL
        if db is not None:
            await self._cache_put(db, cache_key, scores)

        ordered = self._apply_scores(hits, scores)
        return ordered[:top_k]

    # ── internals ──────────────────────────────────────────────────────

    async def _call_llm(
        self, query: str, hits: list[SearchHit]
    ) -> list[dict] | None:
        """Build the rerank prompt and parse the LLM JSON response.

        Returns a list of ``{"id": int, "relevance": float}`` dicts on
        success; ``None`` on any failure (LLM down, non-JSON, mismatched
        shape).
        """
        ai = self._ai_assist_override
        if ai is None:
            try:
                from services.ai_assist_client import ai_assist as _ai

                ai = _ai
            except Exception as e:  # pragma: no cover — defensive
                logger.warning("rerank: ai_assist client unavailable: %s", e)
                return None

        numbered = _format_numbered_docs(hits)
        prompt = _RERANK_PROMPT.format(query=query.strip(), numbered_docs=numbered)
        session_id = f"projecthub-rerank-{secrets.token_hex(6)}"
        try:
            result = await ai.agent_call(
                session_id=session_id,
                message=prompt,
                model=self._model,
                auto_detect=False,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("rerank: LLM call failed: %s", e)
            return None
        if not result or not isinstance(result, dict):
            return None
        raw = result.get("response") or ""
        parsed = _parse_scores(raw)
        if parsed is None:
            logger.debug("rerank: response not parseable, first 200 chars: %r", raw[:200])
            return None
        return parsed

    def _apply_scores(
        self, hits: list[SearchHit], scores: list[dict]
    ) -> list[SearchHit]:
        """Reorder ``hits`` by the LLM's per-id ``relevance`` scores.

        Items the LLM forgot get a default 0.0 score — they sink. Score
        ties break on the original RRF order (which is itself deterministic).
        """
        # Map id (1-indexed in prompt) → score
        by_id: dict[int, float] = {}
        for entry in scores:
            try:
                idx = int(entry.get("id"))
                rel = float(entry.get("relevance", 0.0))
            except (TypeError, ValueError):
                continue
            by_id[idx] = max(0.0, min(1.0, rel))

        scored: list[tuple[float, int, SearchHit]] = []
        for original_pos, hit in enumerate(hits, start=1):
            rel = by_id.get(original_pos, 0.0)
            # Negative tuple positions for descending score, ascending rrf-pos
            scored.append((-rel, original_pos, hit))
        scored.sort()
        out: list[SearchHit] = []
        for neg_rel, _orig, hit in scored:
            new_hit = SearchHit(
                item=hit.item,
                score=-neg_rel,  # back to 0..1
                source=hit.source | {"rerank"},
            )
            out.append(new_hit)
        return out

    # ── Cache plumbing ────────────────────────────────────────────────

    async def _cache_get(
        self, db: AsyncSession, cache_key: str
    ) -> list[dict] | None:
        try:
            res = await db.execute(
                select(OfflineCache).where(OfflineCache.cache_key == cache_key)
            )
            row = res.scalar_one_or_none()
        except Exception as e:  # pragma: no cover — defensive
            logger.debug("rerank: cache read failed: %s", e)
            return None
        if row is None:
            return None
        try:
            fetched = datetime.fromisoformat(row.fetched_at)
        except ValueError:
            return None
        if datetime.now(timezone.utc) - fetched > self._ttl:
            return None
        try:
            data = json.loads(row.data)
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(data, list):
            return None
        return data

    async def _cache_put(
        self, db: AsyncSession, cache_key: str, scores: list[dict]
    ) -> None:
        try:
            existing = (
                await db.execute(
                    select(OfflineCache).where(OfflineCache.cache_key == cache_key)
                )
            ).scalar_one_or_none()
            if existing is None:
                row = OfflineCache(
                    cache_key=cache_key,
                    cache_type="rerank",
                    data=json.dumps(scores, ensure_ascii=False),
                )
                db.add(row)
            else:
                existing.data = json.dumps(scores, ensure_ascii=False)
                existing.fetched_at = datetime.now(timezone.utc).isoformat()
            await db.commit()
        except IntegrityError:
            await db.rollback()
        except Exception as e:  # pragma: no cover — defensive
            logger.debug("rerank: cache write failed: %s", e)


# ── Helpers ─────────────────────────────────────────────────────────────


def _format_numbered_docs(hits: Iterable[SearchHit]) -> str:
    """Render the pool as ``[N] title — snippet`` lines."""
    lines: list[str] = []
    for i, h in enumerate(hits, start=1):
        item = h.item
        title = (item.title or "(no title)")[:_MAX_TITLE_CHARS]
        # Prefer context_summary as the snippet — it's the LLM-generated
        # framing line, denser than the raw body. Fall back to body-head.
        snippet = (item.context_summary or item.content_plain or "")[: _MAX_BODY_CHARS]
        snippet = " ".join(snippet.split())  # collapse whitespace
        lines.append(f"[{i}] {title} — {snippet}")
    return "\n".join(lines)


def _parse_scores(raw: str) -> list[dict] | None:
    """Tolerant JSON parser — same shape as the contextual / extractor parsers.

    Recognises ``{"scores": [...]}`` wrapped in ``` ``` ``` fences, prose, etc.
    """
    if not raw:
        return None
    text = raw.strip()
    # Strip code fences if present
    if text.startswith("```"):
        text = text.split("```", 2)[1] if text.count("```") >= 2 else text
        text = text.strip()
        if text.startswith("json\n"):
            text = text[5:]
    # Locate outermost JSON object
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    blob = text[start : end + 1]
    try:
        data = json.loads(blob)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    scores = data.get("scores")
    if not isinstance(scores, list):
        return None
    return [s for s in scores if isinstance(s, dict)]


# ── Default singleton ────────────────────────────────────────────────────


_default_reranker: Reranker | None = None


def get_default_reranker() -> Reranker | None:
    """Return the configured reranker — or ``None`` when reranking is off.

    Settings: ``brain_reranker_enabled`` gates the whole thing. When off,
    every caller (``hybrid_search``, ``/query``, ``_build_project_context``)
    treats ``None`` as "skip rerank stage" — identical to passing no
    reranker arg.
    """
    global _default_reranker
    try:
        from config import settings
    except Exception:  # pragma: no cover — defensive
        return None
    if not getattr(settings, "brain_reranker_enabled", False):
        return None
    if _default_reranker is None:
        _default_reranker = LLMJudgeReranker()
    return _default_reranker


def reset_default_reranker() -> None:
    global _default_reranker
    _default_reranker = None
