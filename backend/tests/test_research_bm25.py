"""Tests for the pure BM25 scorer used by the rerank adapter (P3b).

Pure-Python, no DB, no LLM — covers tokenisation, edge cases, score
ordering, length-normalisation, and DE+EN stopwords. The scorer is
small (<100 LOC) but lives on the hot path of every Auto-Mode rerank,
so a typo here would surface as confusing ordering downstream.
"""
import pytest

from services.research_providers._bm25 import (
    ScoredChunk,
    score_chunks,
    tokenize,
)


# ── Tokeniser ──────────────────────────────────────────────────────────────


def test_tokenize_lowercases_and_drops_punctuation():
    assert tokenize("OAuth2 PKCE — Service X!") == ["oauth2", "pkce", "service"]


def test_tokenize_preserves_german_umlauts():
    """Common German words must survive tokenisation."""
    out = tokenize("Über das Häuschen mit Möhren und süßem Käse")
    # stopwords "über"/"das"/"mit"/"und" go, content words stay (incl. ß-form).
    assert "häuschen" in out
    assert "möhren" in out
    assert "süßem" in out
    assert "käse" in out


def test_tokenize_drops_stopwords_de_and_en():
    out = tokenize("the auth and der token")
    assert out == ["auth", "token"]


def test_tokenize_drops_single_char_and_returns_empty_on_pure_punctuation():
    assert tokenize("!.?,") == []
    assert tokenize("a x b") == []  # all single-char → all dropped


def test_tokenize_empty_string_returns_empty_list():
    assert tokenize("") == []


# ── score_chunks: basic ordering ───────────────────────────────────────────


def _triple(cid, text, *, payload=None):
    return (cid, text, payload if payload is not None else {"id": cid})


def test_score_chunks_orders_relevant_first():
    """Both PKCE chunks beat the weather chunk; exact ordering between
    the two relevant chunks depends on doc-length normalisation."""
    chunks = [
        _triple("c1", "Service X uses OAuth2 PKCE for client auth"),
        _triple("c2", "weather report sunny tomorrow"),
        _triple("c3", "OAuth2 PKCE deep-dive: flow, refresh tokens, ..."),
    ]
    scored = score_chunks("OAuth2 PKCE", chunks)
    top_two = {s.chunk_id for s in scored[:2]}
    assert top_two == {"c1", "c3"}
    # Last entry is the irrelevant one with score 0.
    assert scored[-1].chunk_id == "c2"
    assert scored[-1].score == 0.0


def test_score_chunks_empty_query_returns_all_with_zero():
    """Defensive: empty query → all chunks survive with score 0."""
    chunks = [_triple("c1", "anything"), _triple("c2", "more")]
    out = score_chunks("", chunks)
    assert {s.chunk_id for s in out} == {"c1", "c2"}
    assert all(s.score == 0.0 for s in out)


def test_score_chunks_empty_input_returns_empty():
    assert score_chunks("auth", []) == []


def test_score_chunks_passthrough_is_preserved():
    payload = {"finding_id": "fid_42", "extra": "metadata"}
    chunks = [_triple("c1", "PKCE auth flow", payload=payload)]
    out = score_chunks("PKCE", chunks)
    assert out[0].chunk is payload


def test_score_chunks_stopword_only_query_zero_scores():
    """A query that's pure stopwords tokenises to [] → empty-query path."""
    out = score_chunks("the and or", [_triple("c1", "auth")])
    assert all(s.score == 0.0 for s in out)


# ── BM25 specifics ─────────────────────────────────────────────────────────


def test_score_chunks_idf_rewards_rare_terms():
    """A term appearing in ALL chunks contributes (almost) zero IDF;
    rare terms differentiate."""
    common_term_chunks = [
        _triple(f"c{i}", "common token everywhere") for i in range(5)
    ]
    rare_term_chunks = [
        _triple("rare", "rare unicorn token nowhere else"),
    ]
    out = score_chunks("unicorn common", common_term_chunks + rare_term_chunks)
    # The chunk with the unicorn must come first (rare = high IDF).
    assert out[0].chunk_id == "rare"


def test_score_chunks_term_frequency_saturates():
    """Repeating a query term should help but not 100× — BM25 saturates."""
    one = [_triple("once", "auth")]
    many = [_triple("many", "auth auth auth auth auth auth auth auth")]
    scored = score_chunks("auth", one + many)
    # ``many`` does score higher, but not 8× higher — saturation works.
    one_score = next(s.score for s in scored if s.chunk_id == "once")
    many_score = next(s.score for s in scored if s.chunk_id == "many")
    assert many_score > one_score
    assert many_score < 8 * one_score


def test_score_chunks_length_normalisation_helps_short_docs():
    """A precise short doc should beat a long sprawling one for the same hit count."""
    short = _triple("short", "PKCE flow")
    long_doc = _triple(
        "long",
        "PKCE flow " + " ".join(f"filler{i}" for i in range(200)),
    )
    out = score_chunks("PKCE flow", [short, long_doc])
    assert out[0].chunk_id == "short"


def test_score_chunks_result_is_permutation_of_input():
    """No chunk is dropped, ever — caller can rely on len equality."""
    chunks = [_triple(f"c{i}", f"text {i}") for i in range(10)]
    out = score_chunks("auth", chunks)
    assert len(out) == len(chunks)
    assert {s.chunk_id for s in out} == {c[0] for c in chunks}


def test_score_chunks_ties_break_deterministically_by_chunk_id():
    """Two chunks with identical content tie on score; secondary sort
    by chunk_id keeps the ordering reproducible across runs."""
    chunks = [
        _triple("zebra", "auth token"),
        _triple("alpha", "auth token"),
        _triple("mango", "auth token"),
    ]
    out = score_chunks("auth token", chunks)
    assert [s.chunk_id for s in out] == ["alpha", "mango", "zebra"]
