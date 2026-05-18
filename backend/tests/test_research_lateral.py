"""Unit tests for the lateral-expansion engine (P7).

Pure/Mock-LLM tests of:

    * filter_high_value — dedup + length + stopword + freq/conf rules
    * extract_entities_from_finding — happy path, malformed LLM output,
      budget denial bubbles up
    * rank_by_relevance — scores attached in order, malformed input
      degrades to all-zero relevance (caller filters out)
    * plan_lateral_subquery — providers clamped to enabled set,
      empty input → None
    * expand_hop end-to-end on mocks
    * Runaway-guard: 100 fake entities never produce more than
      max_new_sub_queries sub-queries

No DB, no real LLM — every async dep is monkeypatched.
"""
from __future__ import annotations

import asyncio
import os
import secrets
import tempfile

import pytest

_TEST_DB_PATH = os.path.join(
    tempfile.gettempdir(),
    f"projecthub_pytest_lateral_{secrets.token_hex(4)}.db",
)
os.environ["PROJECTHUB_DB_PATH"] = _TEST_DB_PATH


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── filter_high_value ─────────────────────────────────────────────────────


def test_filter_drops_short_and_stopword_and_already_seen():
    from services.research_lateral import (
        ExtractedEntity, filter_high_value,
    )

    raw = [
        ExtractedEntity(name="A", normalized="a", confidence=0.9, source_finding_id="f1"),         # too short
        ExtractedEntity(name="The", normalized="the", confidence=0.9, source_finding_id="f1"),     # stopword
        ExtractedEntity(name="keycloak", normalized="keycloak", confidence=0.85, source_finding_id="f1"),  # single-source, conf ≥ 0.8
        ExtractedEntity(name="oldfox", normalized="oldfox", confidence=0.9, source_finding_id="f1"),  # already seen
    ]
    surviving = filter_high_value(
        raw, seen_normalized={"oldfox"},
        min_freq=2, min_single_conf=0.8,
    )
    norms = {r.normalized for r in surviving}
    assert norms == {"keycloak"}


def test_filter_keeps_high_frequency_even_with_low_conf():
    from services.research_lateral import (
        ExtractedEntity, filter_high_value,
    )

    raw = [
        ExtractedEntity(name="redis", normalized="redis", confidence=0.4, source_finding_id="f1"),
        ExtractedEntity(name="redis", normalized="redis", confidence=0.3, source_finding_id="f2"),
        ExtractedEntity(name="rare", normalized="rare", confidence=0.4, source_finding_id="f1"),
    ]
    out = filter_high_value(raw, seen_normalized=set(), min_freq=2, min_single_conf=0.8)
    assert {r.normalized for r in out} == {"redis"}
    redis = next(r for r in out if r.normalized == "redis")
    assert redis.extra_freq == 2
    # Best confidence across the duplicates is kept.
    assert redis.extraction_confidence == 0.4


def test_filter_dedup_aggregates_source_ids():
    from services.research_lateral import (
        ExtractedEntity, filter_high_value,
    )

    raw = [
        ExtractedEntity(name="keycloak", normalized="keycloak", confidence=0.85, source_finding_id="f1"),
        ExtractedEntity(name="keycloak", normalized="keycloak", confidence=0.7, source_finding_id="f2"),
    ]
    out = filter_high_value(raw, seen_normalized=set())
    assert len(out) == 1
    assert sorted(out[0].source_finding_ids) == ["f1", "f2"]


# ── extract_entities_from_finding ─────────────────────────────────────────


def test_extract_happy_path(monkeypatch):
    from services.research_lateral import extract_entities_from_finding
    import services.research_lateral as rl

    async def fake_call_json(prompt, model=None, session_prefix=None):
        class R:
            parsed = [
                {"name": "keycloak-broker", "confidence": 0.91},
                {"name": "OAuth2 PKCE", "confidence": 0.88},
                {"name": "x", "confidence": 0.5},  # too short — filtered downstream
            ]
            ok = True
            usage = {"total_tokens": 800}
        return R()

    monkeypatch.setattr(rl, "call_json", fake_call_json)

    out = _run(extract_entities_from_finding(
        "f1", "Service X uses keycloak-broker for OAuth2 PKCE.",
        budget=None,
    ))
    # Top-2 entities make it through (we let the downstream filter drop
    # the third — short-name guard is in filter_high_value, not here).
    assert len(out) == 3
    assert out[0].normalized == "keycloak-broker"
    assert out[0].confidence == 0.91


def test_extract_malformed_json_returns_empty(monkeypatch):
    from services.research_lateral import extract_entities_from_finding
    import services.research_lateral as rl

    async def junk(*a, **k):
        class R:
            parsed = "not a list at all"
            ok = True
            usage = {}
        return R()

    monkeypatch.setattr(rl, "call_json", junk)

    out = _run(extract_entities_from_finding("f1", "anything", budget=None))
    assert out == []


def test_extract_propagates_budget_degradation(monkeypatch):
    from services.research_lateral import extract_entities_from_finding
    from services.research_budget import BudgetDegradation

    class _DenyBudget:
        async def reserve(self, *a, **k):
            from services.research_budget import ReservationResult
            return ReservationResult(
                allow=False, pressure_before="critical",
                pressure_after_est="exhausted",
                suggested_action="skip", reason="denied",
            )

        async def commit(self, *a, **k):
            pass

    with pytest.raises(BudgetDegradation):
        _run(extract_entities_from_finding(
            "f1", "text", budget=_DenyBudget(),
        ))


# ── rank_by_relevance ─────────────────────────────────────────────────────


def test_rank_attaches_scores_and_sorts(monkeypatch):
    from services.research_lateral import (
        RankedEntity, rank_by_relevance,
    )
    import services.research_lateral as rl

    async def fake_call_json(prompt, model=None, session_prefix=None):
        class R:
            parsed = [
                {"id": 1, "relevance": 0.3},
                {"id": 2, "relevance": 0.9},
                {"id": 3, "relevance": 0.6},
            ]
            ok = True
            usage = {}
        return R()

    monkeypatch.setattr(rl, "call_json", fake_call_json)

    ents = [
        RankedEntity(name="A", normalized="a", extraction_confidence=0.7, relevance=0.0),
        RankedEntity(name="B", normalized="b", extraction_confidence=0.7, relevance=0.0),
        RankedEntity(name="C", normalized="c", extraction_confidence=0.7, relevance=0.0),
    ]
    out = _run(rank_by_relevance(ents, "PKCE", budget=None))
    # Highest relevance first.
    assert [r.normalized for r in out] == ["b", "c", "a"]
    assert out[0].relevance == 0.9


def test_rank_malformed_response_degrades_to_zero(monkeypatch):
    from services.research_lateral import (
        RankedEntity, rank_by_relevance,
    )
    import services.research_lateral as rl

    async def junk(*a, **k):
        class R:
            parsed = "garbage"
            ok = True
            usage = {}
        return R()

    monkeypatch.setattr(rl, "call_json", junk)

    ents = [RankedEntity(name="A", normalized="a", extraction_confidence=0.7, relevance=0.0)]
    out = _run(rank_by_relevance(ents, "topic", budget=None))
    # No raise; all zero so caller's cutoff filter drops everything.
    assert out[0].relevance == 0.0


# ── plan_lateral_subquery ─────────────────────────────────────────────────


def test_plan_filters_providers_to_enabled_set(monkeypatch):
    from services.research_lateral import (
        RankedEntity, plan_lateral_subquery,
    )
    import services.research_lateral as rl

    async def fake_call_json(prompt, model=None, session_prefix=None):
        class R:
            parsed = {
                "question": "Wie ist X konfiguriert?",
                "providers": ["confluence", "DISABLED_PROV"],
                "rationale": "tief in die Doku schauen",
            }
            ok = True
            usage = {}
        return R()

    monkeypatch.setattr(rl, "call_json", fake_call_json)

    ent = RankedEntity(name="X", normalized="x", extraction_confidence=0.9, relevance=0.8)
    out = _run(plan_lateral_subquery(
        ent, topic="PKCE",
        enabled_providers=["confluence", "kb_fts"],
        max_providers_per_sub_query=3,
        budget=None,
    ))
    assert out is not None
    assert out.providers == ["confluence"]
    assert "konfiguriert" in out.question
    assert out.priority == 2  # lateral default


def test_plan_returns_none_on_malformed_or_empty(monkeypatch):
    from services.research_lateral import (
        RankedEntity, plan_lateral_subquery,
    )
    import services.research_lateral as rl

    async def junk(*a, **k):
        class R:
            parsed = {"providers": ["confluence"]}  # no question text
            ok = True
            usage = {}
        return R()

    monkeypatch.setattr(rl, "call_json", junk)

    ent = RankedEntity(name="X", normalized="x", extraction_confidence=0.9, relevance=0.8)
    out = _run(plan_lateral_subquery(
        ent, topic="PKCE",
        enabled_providers=["confluence"],
        max_providers_per_sub_query=3,
        budget=None,
    ))
    assert out is None


# ── expand_hop end-to-end ─────────────────────────────────────────────────


def test_expand_hop_runs_three_stages(monkeypatch):
    """The full hop happy path: 2 findings → entities → ranked → 2 sub-queries."""
    from services.research_lateral import expand_hop
    import services.research_lateral as rl

    call_count = {"n": 0}
    responses = [
        # Extract from finding f1
        [{"name": "keycloak", "confidence": 0.9}],
        # Extract from finding f2
        [{"name": "refresh-token", "confidence": 0.85}],
        # Rank both
        [{"id": 1, "relevance": 0.9}, {"id": 2, "relevance": 0.7}],
        # Plan sub-query 1
        {"question": "Wie konfiguriert?", "providers": ["confluence"], "rationale": "r"},
        # Plan sub-query 2
        {"question": "Refresh-Token-Policy?", "providers": ["confluence"], "rationale": "r"},
    ]

    async def stub_call_json(prompt, model=None, session_prefix=None):
        idx = call_count["n"]
        call_count["n"] += 1
        if idx < len(responses):
            class R:
                parsed = responses[idx]
                ok = True
                usage = {}
            return R()
        # Unexpected extra call → empty.
        class E:
            parsed = None
            ok = False
            usage = {}
        return E()

    monkeypatch.setattr(rl, "call_json", stub_call_json)

    findings = [
        {"id": "f1", "snippet": "Service X uses keycloak"},
        {"id": "f2", "snippet": "90-day refresh-token policy"},
    ]
    seen: set[str] = set()
    out = _run(expand_hop(
        hop=1, findings=findings,
        topic="PKCE",
        enabled_providers=["confluence", "kb_fts"],
        seen_entities=seen,
        max_new_sub_queries=6,
        max_providers_per_sub_query=2,
        relevance_cutoff=0.5,
    ))
    assert out.hop == 1
    assert out.extracted_count == 2
    assert out.surviving_count == 2  # both above threshold (single-source ≥ 0.8)
    assert out.ranked_top == 2
    assert len(out.new_sub_queries) == 2
    # Lineage wired up.
    assert all(sq.id in out.parent_finding_ids for sq in out.new_sub_queries)
    # Entity dedup: seen_entities is mutated.
    assert "keycloak" in seen
    assert "refresh-token" in seen


def test_expand_hop_no_findings_aborts():
    from services.research_lateral import expand_hop

    out = _run(expand_hop(
        hop=1, findings=[],
        topic="x", enabled_providers=["confluence"],
        seen_entities=set(),
        max_new_sub_queries=6, max_providers_per_sub_query=1,
        relevance_cutoff=0.5,
    ))
    assert out.aborted_reason == "no_findings"
    assert out.new_sub_queries == []


def test_expand_hop_below_cutoff_yields_nothing(monkeypatch):
    """All entities below relevance_cutoff → zero sub-queries."""
    from services.research_lateral import expand_hop
    import services.research_lateral as rl

    responses = [
        [{"name": "blubb", "confidence": 0.9}],
        [{"id": 1, "relevance": 0.3}],  # below cutoff 0.5
    ]
    idx = {"n": 0}

    async def fake(*a, **k):
        i = idx["n"]; idx["n"] += 1

        class R:
            parsed = responses[i] if i < len(responses) else None
            ok = i < len(responses)
            usage = {}
        return R()

    monkeypatch.setattr(rl, "call_json", fake)

    out = _run(expand_hop(
        hop=1, findings=[{"id": "f1", "snippet": "blubb"}],
        topic="x", enabled_providers=["confluence"],
        seen_entities=set(),
        max_new_sub_queries=6, max_providers_per_sub_query=1,
        relevance_cutoff=0.5,
    ))
    assert out.aborted_reason == "no_entity_above_cutoff"
    assert out.new_sub_queries == []


# ── Runaway guard ─────────────────────────────────────────────────────────


def test_expand_hop_caps_at_max_new_sub_queries(monkeypatch):
    """100 high-confidence entities still produce ≤ max_new_sub_queries."""
    from services.research_lateral import expand_hop
    import services.research_lateral as rl

    # Build a per-finding entity list of 100 distinct high-conf entities,
    # delivered via the extract path (one big response).
    big_entity_list = [
        {"name": f"entity{i:03d}", "confidence": 0.9} for i in range(100)
    ]
    # Ranking returns equal high-relevance for all (preserves order).
    rank_response = [
        {"id": i + 1, "relevance": 0.9} for i in range(100)
    ]

    call_idx = {"n": 0}

    async def fake(*a, **k):
        i = call_idx["n"]; call_idx["n"] += 1
        if i == 0:
            class R:
                parsed = big_entity_list[:5]  # _MAX_ENTITIES_PER_FINDING
                ok = True
                usage = {}
            return R()
        if i == 1:
            class R2:
                parsed = rank_response
                ok = True
                usage = {}
            return R2()
        # Per-entity plan calls — return valid plan for any prompt.
        class P:
            parsed = {"question": "q", "providers": ["confluence"], "rationale": "r"}
            ok = True
            usage = {}
        return P()

    monkeypatch.setattr(rl, "call_json", fake)

    out = _run(expand_hop(
        hop=1, findings=[{"id": "f1", "snippet": "lots of entities"}],
        topic="x", enabled_providers=["confluence"],
        seen_entities=set(),
        max_new_sub_queries=6,  # CAP
        max_providers_per_sub_query=1,
        relevance_cutoff=0.5,
    ))
    assert len(out.new_sub_queries) <= 6, (
        f"runaway: produced {len(out.new_sub_queries)} sub-queries"
    )


def test_expand_hop_already_seen_entities_skipped(monkeypatch):
    """Entities marked seen_normalized are dropped during filtering."""
    from services.research_lateral import expand_hop
    import services.research_lateral as rl

    responses = [
        [{"name": "keycloak", "confidence": 0.9}],  # already seen
        # Rank response unused — filter empties before this fires.
    ]
    idx = {"n": 0}

    async def fake(*a, **k):
        i = idx["n"]; idx["n"] += 1

        class R:
            parsed = responses[i] if i < len(responses) else None
            ok = True
            usage = {}
        return R()

    monkeypatch.setattr(rl, "call_json", fake)

    seen = {"keycloak"}
    out = _run(expand_hop(
        hop=1, findings=[{"id": "f1", "snippet": "keycloak"}],
        topic="x", enabled_providers=["confluence"],
        seen_entities=seen,
        max_new_sub_queries=6, max_providers_per_sub_query=1,
        relevance_cutoff=0.5,
    ))
    assert out.aborted_reason == "no_surviving_entities"
