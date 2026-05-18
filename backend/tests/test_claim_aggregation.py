"""Tests for the pure claim-aggregation primitives.

These tests cover ``services/claim_aggregation.py`` in isolation — no DB,
no settings, no LLM. They complement the existing
``test_synapse_validation.py`` (which calls the same functions via the
backward-compat re-exports) and lock in behaviour for the planned
Research-Auto-Mode validator that will reuse this module.
"""
import pytest

from services.claim_aggregation import (
    ClaimVerdict,
    ConfidenceThresholds,
    GroundingResult,
    aggregate_claim,
    band_from_confidence,
    compute_confidence,
    decide_verdict,
    select_verifier_models,
)


# Two reusable thresholds: matches the Synapse defaults (0.7 / 0.5) but
# kept literal so a settings change can never invalidate the tests.
T = ConfidenceThresholds(high=0.7, review=0.5)


def _verdict(
    relation: str,
    *,
    agreement: float = 1.0,
    nli: float | None = 1.0,
    text: str = "c",
) -> ClaimVerdict:
    return ClaimVerdict(
        claim_text=text,
        source_item_ids=["i1"],
        relation=relation,
        evidence=[],
        nli_score=nli,
        verifier_agreement=agreement,
        verifier_votes={},
    )


# --- select_verifier_models ------------------------------------------------

def test_select_verifier_models_returns_default_for_single_model():
    """One configured model = no diversity = single default-engine call."""
    assert select_verifier_models(["only-one"], 5) == [None]


def test_select_verifier_models_cycles_through_when_multiple():
    """Two+ configured = cycle so each sample picks a different model."""
    out = select_verifier_models(["a", "b", "c"], 5)
    assert out == ["a", "b", "c", "a", "b"]


def test_select_verifier_models_clamps_samples_to_at_least_one():
    assert select_verifier_models(["a", "b"], 0) == ["a"]


# --- aggregate_claim -------------------------------------------------------

def test_aggregate_claim_no_critic_falls_back_to_grounding():
    """No critic signal → relation+score come from the grounding result."""
    g = GroundingResult(relation="supported", score=0.85, evidence=[{"item_id": "i1", "span": "x"}])
    out = aggregate_claim("c", ["i1"], g, [])
    assert out.relation == "supported"
    assert out.verifier_agreement == 0.85
    assert out.verifier_votes == {}
    assert out.evidence == [{"item_id": "i1", "span": "x"}]


def test_aggregate_claim_majority_vote_wins():
    """3 votes 'supported', 1 'partial' → supported, agreement 0.75."""
    g = GroundingResult(relation="unsupported", score=0.4)
    out = aggregate_claim(
        "c", ["i1"], g, ["supported", "supported", "supported", "partial"]
    )
    assert out.relation == "supported"
    assert out.verifier_agreement == 0.75
    assert out.verifier_votes == {"supported": 3, "partial": 1}


def test_aggregate_claim_tie_breaks_to_more_severe():
    """Tie 2-2 between contradicted and supported → contradicted wins."""
    g = GroundingResult(relation="supported", score=0.9)
    out = aggregate_claim("c", ["i1"], g, ["supported", "supported", "contradicted", "contradicted"])
    assert out.relation == "contradicted"
    assert out.verifier_agreement == 0.5


# --- band_from_confidence --------------------------------------------------

@pytest.mark.parametrize("score,expected", [
    (0.91, "high"),
    (0.70, "high"),       # exactly the threshold
    (0.69, "medium"),
    (0.50, "medium"),     # exactly the review threshold
    (0.49, "low"),
    (0.0, "low"),
])
def test_band_from_confidence_bucketing(score, expected):
    assert band_from_confidence(score, T) == expected


# --- compute_confidence ----------------------------------------------------

def test_compute_confidence_empty_returns_low():
    """No claims = nothing to validate = low confidence."""
    assert compute_confidence([], T) == (0.0, "low")


def test_compute_confidence_single_supported_high_band():
    """1.0·(0.7 + 0.3) · agreement-bonus → exactly 1.0 → high."""
    score, band = compute_confidence([_verdict("supported")], T)
    assert score == 1.0
    assert band == "high"


def test_compute_confidence_contradiction_caps_below_review():
    """Any contradicted claim caps the synapse-level score at 0.4 → low."""
    claims = [_verdict("supported"), _verdict("contradicted", nli=0.0)]
    score, band = compute_confidence(claims, T)
    assert score <= 0.4
    assert band == "low"


def test_compute_confidence_low_agreement_dampens_score():
    """Verifier agreement of 0 still leaves half the base in (0.5+0.5·0)=0.5."""
    score, _ = compute_confidence([_verdict("supported", agreement=0.0)], T)
    # base = 1.0 · (0.5 + 0.5·0) = 0.5
    assert score == 0.5


# --- decide_verdict --------------------------------------------------------

def test_decide_verdict_high_persists():
    out, defects = decide_verdict(0.9, "high", [_verdict("supported")])
    assert out == "persist"
    assert defects == []


def test_decide_verdict_medium_persists_flagged():
    """Medium band + no contradiction → persist with flag."""
    claims = [_verdict("partial", nli=0.6)]
    out, defects = decide_verdict(0.6, "medium", claims)
    assert out == "persist_flagged"
    # 'partial' produces a "stärker formuliert" defect entry
    assert any("stärker" in d for d in defects)


def test_decide_verdict_any_contradiction_forces_review():
    """One contradicted claim → human_review even if band were high."""
    claims = [_verdict("supported"), _verdict("contradicted", nli=0.1)]
    out, defects = decide_verdict(0.8, "high", claims)
    assert out == "human_review"
    assert any("widersprochen" in d for d in defects)


def test_decide_verdict_low_band_forces_review():
    """Low confidence (no contradiction needed) → human_review."""
    out, _ = decide_verdict(0.3, "low", [_verdict("unsupported")])
    assert out == "human_review"


def test_decide_verdict_unsupported_listed_as_defect():
    """An unsupported claim shows up in the German defect list."""
    _, defects = decide_verdict(0.6, "medium", [_verdict("unsupported")])
    assert any("kein" in d.lower() and "belegt" in d for d in defects)


# --- Integration of the full pipeline (still pure) -------------------------

def test_full_pipeline_supported_to_persist():
    """End-to-end on the pure path: aggregate → confidence → verdict."""
    g = GroundingResult(relation="supported", score=0.9)
    cv = aggregate_claim("c", ["i1"], g, ["supported", "supported"])
    conf, band = compute_confidence([cv], T)
    verdict, defects = decide_verdict(conf, band, [cv])
    assert verdict == "persist"
    assert band == "high"
    assert defects == []


def test_full_pipeline_contradicted_to_review():
    """End-to-end: a single contradiction sinks the whole synapse."""
    g = GroundingResult(relation="contradicted", score=0.8)
    cv = aggregate_claim("c", ["i1"], g, ["contradicted", "contradicted"])
    conf, band = compute_confidence([cv], T)
    verdict, _ = decide_verdict(conf, band, [cv])
    assert verdict == "human_review"
    assert conf <= 0.4  # contradiction cap
