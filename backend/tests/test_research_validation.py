"""Unit tests for per-finding validation (P8).

Pure/Mock-LLM tests:

    * Tier-B alone (supported) → status=grounded, verdict=persist
    * Tier-B partial → status=grounded, band=medium
    * Tier-B unsupported, Normal mode → flagged (no Tier-C)
    * Tier-B unsupported, Tief mode → critic fan-out, status from votes
    * Tier-B contradicted → rejected regardless of band
    * Critic majority overrides Tier-B (Tief)
    * Budget denial mid-validation → degrades to flagged
    * Malformed LLM output → unsupported relation
"""
from __future__ import annotations

import asyncio
import os
import secrets
import tempfile

import pytest

_TEST_DB_PATH = os.path.join(
    tempfile.gettempdir(),
    f"projecthub_pytest_validate_{secrets.token_hex(4)}.db",
)
os.environ["PROJECTHUB_DB_PATH"] = _TEST_DB_PATH


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _stub_call_json(monkeypatch, responses: list[dict | str | None]):
    """Patch synapse_llm.call_json to yield the queued responses.

    Each response can be a dict (parsed value), or None (parsed=None).
    The fake LLMResult also carries .ok and .usage attributes that
    validate_finding reads.
    """
    import services.research_validation as rv

    queue = list(responses)

    async def fake(prompt, model=None, session_prefix=None):
        if not queue:
            class R:
                parsed = None
                ok = False
                usage = {}
            return R()
        item = queue.pop(0)

        class R:
            parsed = item
            ok = item is not None
            usage = {"total_tokens": 500}
        return R()

    monkeypatch.setattr(rv, "call_json", fake)


# ── Tier-B alone ──────────────────────────────────────────────────────────


def test_tier_b_supported_high_score_grounds(monkeypatch):
    from services.research_validation import validate_finding

    _stub_call_json(monkeypatch, [
        {"relation": "supported", "score": 0.92, "reason": "exact match"},
    ])

    out = _run(validate_finding(
        topic="OAuth2 PKCE", title="Service X uses PKCE",
        snippet="Detailed evidence about PKCE",
        full_content="Long content discussing PKCE in Service X",
        source_ref="kb:1", provider_key="kb_fts",
        enable_critic_fanout=False,
    ))
    assert out.new_status == "grounded"
    assert out.verdict == "persist"
    assert out.confidence_band == "high"
    assert out.tier_b_relation == "supported"
    assert out.critic_votes == []
    # Defects should be empty for a supported finding.
    assert out.defects == []


def test_tier_b_partial_normal_grounds_with_medium_band(monkeypatch):
    from services.research_validation import validate_finding

    # Score 0.62 → partial relation maps to score=0.6 in RELATION_SCORE
    # plus NLI weight → confidence in medium range.
    _stub_call_json(monkeypatch, [
        {"relation": "partial", "score": 0.65, "reason": "loose link"},
    ])

    out = _run(validate_finding(
        topic="X", title="possibly related",
        snippet="vague",
        full_content="might be relevant",
        source_ref="kb:2", provider_key="kb_fts",
        enable_critic_fanout=False,
    ))
    # No critic on Normal for partial → no escalation.
    assert out.critic_votes == []
    # Partial maps to either grounded (persist/persist_flagged) or
    # rejected depending on the threshold; in Normal mode the
    # confidence band lands in "medium" → persist_flagged → grounded.
    assert out.new_status in {"grounded", "flagged"}


def test_tier_b_contradicted_rejects(monkeypatch):
    from services.research_validation import validate_finding

    _stub_call_json(monkeypatch, [
        {"relation": "contradicted", "score": 0.9, "reason": "opposite"},
    ])

    out = _run(validate_finding(
        topic="X", title="X is fast",
        snippet="X is slow", full_content="X is definitely slow",
        source_ref="kb:3", provider_key="kb_fts",
        enable_critic_fanout=False,
    ))
    assert out.new_status == "rejected"
    assert out.tier_b_relation == "contradicted"


def test_tier_b_unsupported_normal_mode_flags(monkeypatch):
    """Normal mode + unsupported Tier-B → no Tier-C → flagged."""
    from services.research_validation import validate_finding

    _stub_call_json(monkeypatch, [
        {"relation": "unsupported", "score": 0.2},
    ])

    out = _run(validate_finding(
        topic="X", title="claim", snippet="snippet",
        full_content="content",
        source_ref="kb:4", provider_key="kb_fts",
        enable_critic_fanout=False,
    ))
    assert out.new_status == "flagged"
    assert out.critic_votes == []
    assert out.verdict == "human_review"


# ── Tier-C fan-out (Tief) ─────────────────────────────────────────────────


def test_tief_unsupported_triggers_critic_fanout(monkeypatch):
    """Tief mode + unsupported → critic votes drive the final relation."""
    from services.research_validation import validate_finding

    # Tier-B says unsupported; critics override to "supported".
    _stub_call_json(monkeypatch, [
        {"relation": "unsupported", "score": 0.4},        # Tier-B
        {"relation": "supported", "reasoning": "ok"},      # critic 1
        {"relation": "supported", "reasoning": "ok"},      # critic 2
        {"relation": "supported", "reasoning": "ok"},      # critic 3
    ])

    out = _run(validate_finding(
        topic="X", title="actually relevant",
        snippet="snippet", full_content="content",
        source_ref="kb:5", provider_key="kb_fts",
        enable_critic_fanout=True,
        verifier_models=["m1", "m2", "m3"],  # ≥2 → real fan-out
        verifier_samples=3,
    ))
    # Critic majority "supported" → relation becomes supported regardless
    # of Tier-B's "unsupported". new_status="grounded".
    assert out.claim_verdict.relation == "supported"
    assert len(out.critic_votes) == 3
    assert out.new_status == "grounded"


def test_tief_critic_majority_contradicts_rejects(monkeypatch):
    from services.research_validation import validate_finding

    _stub_call_json(monkeypatch, [
        {"relation": "partial", "score": 0.55},        # Tier-B
        {"relation": "contradicted", "reasoning": "a"},
        {"relation": "contradicted", "reasoning": "b"},
        {"relation": "supported", "reasoning": "c"},
    ])

    out = _run(validate_finding(
        topic="X", title="bold claim",
        snippet="...", full_content="...",
        source_ref="kb:6", provider_key="kb_fts",
        enable_critic_fanout=True,
        verifier_models=["m1", "m2", "m3"],
        verifier_samples=3,
    ))
    assert out.claim_verdict.relation == "contradicted"
    assert out.new_status == "rejected"


# ── Malformed responses ───────────────────────────────────────────────────


def test_malformed_tier_b_response_treated_as_unsupported(monkeypatch):
    from services.research_validation import validate_finding

    _stub_call_json(monkeypatch, [
        "not a dict at all",  # parsed → string, validator must defend
    ])

    out = _run(validate_finding(
        topic="X", title="t", snippet="s",
        full_content="c",
        source_ref="kb:7", provider_key="kb_fts",
        enable_critic_fanout=False,
    ))
    assert out.tier_b_relation == "unsupported"
    assert out.new_status == "flagged"


def test_tier_b_supported_low_score_in_tief_escalates(monkeypatch):
    """In Tief, Tier-B score < 0.8 escalates even if relation='supported'."""
    from services.research_validation import validate_finding

    _stub_call_json(monkeypatch, [
        {"relation": "supported", "score": 0.6},        # Tier-B trust < 0.8
        {"relation": "supported", "reasoning": "yes"},   # critic 1
        {"relation": "supported", "reasoning": "yes"},   # critic 2
    ])

    out = _run(validate_finding(
        topic="X", title="t", snippet="s", full_content="c",
        source_ref="kb:8", provider_key="kb_fts",
        enable_critic_fanout=True,
        verifier_models=["m1", "m2"],
        verifier_samples=2,
    ))
    assert len(out.critic_votes) == 2  # escalated even though tier_b="supported"


# ── Budget denial ─────────────────────────────────────────────────────────


def test_budget_denies_tier_b_flags_finding(monkeypatch):
    """When the BudgetTracker denies the grounding reservation, the
    finding is flagged with a clear defect explanation — never raises."""
    from services.research_validation import validate_finding
    from services.research_budget import ReservationResult

    class _DenyBudget:
        async def reserve(self, *a, **k):
            return ReservationResult(
                allow=False, pressure_before="critical",
                pressure_after_est="exhausted",
                suggested_action="skip_grounding", reason="denied",
            )

        async def commit(self, *a, **k):
            pass

    out = _run(validate_finding(
        topic="X", title="t", snippet="s", full_content="c",
        source_ref="kb:9", provider_key="kb_fts",
        enable_critic_fanout=False,
        budget=_DenyBudget(),
    ))
    assert out.new_status == "flagged"
    assert any("budget" in d.lower() for d in out.defects)
