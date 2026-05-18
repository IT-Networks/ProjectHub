"""Pure-Python BM25 scorer for Auto-Mode Stage-1 rerank.

Phase 3b of the Research-Auto-Mode workflow. Lexical relevance scoring
over a small in-memory chunk set — usually 30–100 provider hits that
need ordering before we hand the survivors to Stage 2 (embedding-cosine
or LLM-judge rerank). The classical Okapi BM25 formula with the
standard ``k1=1.2`` / ``b=0.75`` defaults — those numbers have been
re-validated against TREC, MS MARCO, BEIR repeatedly; project-specific
tuning gives <5% lift and isn't worth a new knob.

Why hand-rolled instead of ``rank-bm25``:

    * Zero new pip dep (the backend already runs without any ML stack).
    * The library wraps numpy; we don't want to drag numpy into the
      research pipeline just for this.
    * <100 LOC of clear code we own — tunable when an actual benchmark
      ever shows a deficiency.

Tokenisation matches the existing helpers in ``research_providers/
project_notes.py`` so the planner's lexical signal lines up with the
notes provider's substring match: unicode-word split, lowercased,
DE+EN stopwords dropped, single-char tokens dropped.

This module is pure — no DB, no settings, no I/O. The Auto-Mode
``RerankAdapter`` is the only caller in v1.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Iterable


# ── Tuning constants ────────────────────────────────────────────────────────

#: BM25 term-frequency saturation. Higher = less saturation = a doc with
#: many repetitions still scores up. 1.2 is the standard Lucene default.
_K1 = 1.2

#: BM25 length normalisation. 0 = ignore doc length; 1 = full normalise.
#: 0.75 is the standard.
_B = 0.75

#: Tokeniser stopwords — DE + EN, same set as the project_notes provider
#: so the lexical channel is consistent.
_STOPWORDS: frozenset[str] = frozenset({
    # DE
    "der", "die", "das", "und", "oder", "in", "mit", "für", "auf", "von",
    "ist", "im", "zu", "den", "ein", "eine", "wie", "was", "wer", "wann",
    "auch", "aus", "bei", "über", "nach", "vor", "durch", "ohne", "um",
    "noch", "nur", "schon", "sehr", "kann", "soll", "muss", "wird",
    # EN
    "the", "and", "or", "in", "with", "for", "on", "of", "is", "to", "a",
    "an", "how", "what", "who", "when", "why", "are", "be", "was", "were",
    "this", "that", "those", "these", "it", "as", "at", "by", "from",
})


# ── Value types ────────────────────────────────────────────────────────────


@dataclass
class ScoredChunk:
    """Output of ``score_chunks`` — one chunk + its BM25 score.

    ``chunk`` is whatever the caller passed in (a Finding, a dict, …);
    BM25 doesn't care, the scorer just needs the text. Passing it
    through means the caller doesn't have to keep a side-table to
    re-attach scores after sorting.
    """

    chunk_id: str
    score: float
    chunk: Any  # passthrough, opaque to the scorer


# ── Tokeniser ──────────────────────────────────────────────────────────────


def tokenize(text: str) -> list[str]:
    """Split + lowercase + stopword-filter ``text`` into BM25 terms.

    Returns ``[]`` when the input collapses to nothing. Single-character
    tokens are dropped (they almost never help and cause score noise).
    Umlauts and ß are preserved — important for the German corpus.
    """
    if not text:
        return []
    raw = re.findall(r"[\wäöüÄÖÜß]+", text.lower())
    return [t for t in raw if len(t) >= 2 and t not in _STOPWORDS]


# ── Scorer ─────────────────────────────────────────────────────────────────


def score_chunks(
    query: str,
    chunks: Iterable[tuple[str, str, Any]],
    *,
    k1: float = _K1,
    b: float = _B,
) -> list[ScoredChunk]:
    """BM25-score ``chunks`` against ``query``; return sorted high→low.

    Args:
        query: free-text query — same tokenisation as the chunks.
        chunks: iterable of ``(chunk_id, text, passthrough)`` triples.
            ``passthrough`` is yielded verbatim back to the caller in
            the result; ``text`` is what's actually scored.
        k1, b: BM25 parameters. Defaults are Lucene's; override only with
            evidence.

    Returns:
        ``list[ScoredChunk]`` ordered by descending score. Chunks with
        zero score (no overlapping terms with the query) are still
        included so a noisy filter doesn't surprise the caller — they
        sort to the bottom. An empty query returns chunks in input
        order with score 0.0 (defensive: callers can still rely on the
        return list being a permutation of the input).
    """
    materialised = list(chunks)
    if not materialised:
        return []

    q_terms = tokenize(query)
    if not q_terms:
        return [
            ScoredChunk(chunk_id=cid, score=0.0, chunk=passthrough)
            for cid, _, passthrough in materialised
        ]

    # Pass 1 — pre-tokenise every chunk, collect doc lengths + doc-frequencies.
    chunk_tokens: list[list[str]] = [tokenize(text or "") for _, text, _ in materialised]
    doc_lens = [len(toks) for toks in chunk_tokens]
    n_docs = len(materialised)
    avg_dl = (sum(doc_lens) / n_docs) if n_docs else 0.0

    # Document-frequency: in how many chunks does each query term appear?
    # We only care about query terms (no need for a global DF table).
    df: dict[str, int] = {}
    for term in set(q_terms):
        df[term] = sum(1 for toks in chunk_tokens if term in toks)

    # Inverse-document-frequency, BM25's "+1 smoothing" variant.
    # Guarantees IDF > 0 even when a term hits every chunk.
    idf = {
        term: math.log(((n_docs - df[term] + 0.5) / (df[term] + 0.5)) + 1.0)
        for term in df
    }

    # Pass 2 — score.
    out: list[ScoredChunk] = []
    for (cid, _, passthrough), toks, dl in zip(materialised, chunk_tokens, doc_lens):
        if not toks:
            out.append(ScoredChunk(chunk_id=cid, score=0.0, chunk=passthrough))
            continue
        # Term-frequency in this chunk, but only for query terms.
        tf: dict[str, int] = {}
        for t in toks:
            if t in idf:
                tf[t] = tf.get(t, 0) + 1
        if not tf:
            out.append(ScoredChunk(chunk_id=cid, score=0.0, chunk=passthrough))
            continue

        # BM25 saturated term-frequency × IDF, accumulated over q terms.
        score = 0.0
        norm = 1.0 - b + b * (dl / avg_dl if avg_dl else 1.0)
        for term, f in tf.items():
            saturated = f * (k1 + 1.0) / (f + k1 * norm)
            score += idf[term] * saturated
        out.append(ScoredChunk(chunk_id=cid, score=score, chunk=passthrough))

    out.sort(key=lambda sc: (-sc.score, sc.chunk_id))
    return out
