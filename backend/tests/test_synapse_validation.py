"""Tests for the synapse validation pipeline's pure decision logic (Phase 3).

Covers the I/O-free core — verifier-model selection, claim aggregation,
confidence computation, verdict decision, and the small parsers. The
async tiers (grounding / critic fan-out / persistence) are covered by
integration tests later.
"""
import pytest

from services.synapse_validation import (
    ClaimVerdict, GroundingResult,
    select_verifier_models, compute_confidence, decide_verdict,
    _aggregate_claim, _sanitise_relation, _clamp01, _evidence_from_grounding,
    SourceItem,
)


def _claim(relation: str, *, agreement: float = 1.0, nli: float | None = 1.0,
           text: str = "c") -> ClaimVerdict:
    return ClaimVerdict(
        claim_text=text,
        source_item_ids=["i1"],
        relation=relation,
        evidence=[],
        nli_score=nli,
        verifier_agreement=agreement,
        verifier_votes={},
    )


# --- select_verifier_models -------------------------------------------------

def test_select_verifier_models_no_diversity_returns_single():
    """0 or 1 configured models → one default call (fan-out adds no value)."""
    assert select_verifier_models([], 3) == [None]
    assert select_verifier_models(["only-one"], 5) == [None]


def test_select_verifier_models_cycles_when_diverse():
    assert select_verifier_models(["a", "b"], 3) == ["a", "b", "a"]
    assert select_verifier_models(["a", "b", "c"], 3) == ["a", "b", "c"]
    assert select_verifier_models(["a", "b"], 1) == ["a"]


def test_select_verifier_models_clamps_samples():
    # samples <= 0 still yields at least one call
    assert select_verifier_models([], 0) == [None]
    assert len(select_verifier_models(["a", "b"], 0)) == 1


# --- _aggregate_claim -------------------------------------------------------

def test_aggregate_claim_majority_vote():
    g = GroundingResult(relation="unsupported", score=0.3)
    cv = _aggregate_claim("c", ["i1"], g, ["supported", "supported", "partial"])
    assert cv.relation == "supported"
    assert cv.verifier_agreement == pytest.approx(2 / 3, abs=1e-3)
    assert cv.verifier_votes == {"supported": 2, "partial": 1}


def test_aggregate_claim_tie_breaks_toward_severe():
    """A 1-1 split between supported and contradicted resolves to contradicted."""
    g = GroundingResult(relation="supported", score=0.9)
    cv = _aggregate_claim("c", ["i1"], g, ["supported", "contradicted"])
    assert cv.relation == "contradicted"
    assert cv.verifier_agreement == pytest.approx(0.5)


def test_aggregate_claim_falls_back_to_grounding_without_critic():
    g = GroundingResult(
        relation="supported", score=0.77,
        evidence=[{"item_id": "i1", "span": "x"}],
    )
    cv = _aggregate_claim("c", ["i1"], g, [])
    assert cv.relation == "supported"
    assert cv.verifier_agreement == pytest.approx(0.77)
    assert cv.verifier_votes == {}
    assert cv.evidence == [{"item_id": "i1", "span": "x"}]
    assert cv.nli_score == pytest.approx(0.77)


# --- compute_confidence -----------------------------------------------------

def test_compute_confidence_empty_is_low():
    assert compute_confidence([]) == (0.0, "low")


def test_compute_confidence_all_supported_is_high():
    conf, band = compute_confidence([_claim("supported"), _claim("supported")])
    assert conf == pytest.approx(1.0)
    assert band == "high"


def test_compute_confidence_contradiction_caps_low():
    """One contradicted claim drags the whole synapse below the review line."""
    claims = [_claim("supported"), _claim("supported"), _claim("contradicted", nli=0.9)]
    conf, band = compute_confidence(claims)
    assert conf <= 0.4
    assert band == "low"


def test_compute_confidence_low_agreement_damps_score():
    """Same relation, but split critic votes → lower confidence."""
    high = compute_confidence([_claim("supported", agreement=1.0)])[0]
    low = compute_confidence([_claim("supported", agreement=0.0)])[0]
    assert high == pytest.approx(1.0)
    assert low == pytest.approx(0.5)
    assert low < high


def test_compute_confidence_partial_lands_in_medium_band():
    conf, band = compute_confidence([_claim("partial", nli=0.6)])
    # base 0.6, nli 0.6 → raw 0.6, agreement 1.0 → 0.6
    assert conf == pytest.approx(0.6)
    assert band == "medium"


# --- decide_verdict ---------------------------------------------------------

def test_decide_verdict_high_band_persists():
    verdict, defects = decide_verdict(0.9, "high", [_claim("supported")])
    assert verdict == "persist"
    assert defects == []


def test_decide_verdict_medium_band_flags():
    verdict, _ = decide_verdict(0.6, "medium", [_claim("partial")])
    assert verdict == "persist_flagged"


def test_decide_verdict_low_band_goes_to_review():
    verdict, _ = decide_verdict(0.3, "low", [_claim("unsupported")])
    assert verdict == "human_review"


def test_decide_verdict_contradiction_forces_review_regardless_of_band():
    """Even if the band were 'high', a contradicted claim → human_review."""
    claims = [_claim("supported"), _claim("contradicted")]
    verdict, defects = decide_verdict(0.9, "high", claims)
    assert verdict == "human_review"
    assert any("widersprochen" in d for d in defects)


def test_decide_verdict_collects_defects_per_claim():
    claims = [_claim("supported"), _claim("unsupported"), _claim("partial")]
    _, defects = decide_verdict(0.6, "medium", claims)
    assert len(defects) == 2  # unsupported + partial, supported produces none
    assert any("keine Quelle belegt" in d for d in defects)
    assert any("stärker" in d for d in defects)


# --- small parsers ----------------------------------------------------------

def test_sanitise_relation():
    assert _sanitise_relation("SUPPORTED") == "supported"
    assert _sanitise_relation(" partial ") == "partial"
    assert _sanitise_relation("garbage") == "unsupported"
    assert _sanitise_relation(None) == "unsupported"
    assert _sanitise_relation(42) == "unsupported"


def test_clamp01():
    assert _clamp01(0.5, default=0.1) == 0.5
    assert _clamp01(1.7, default=0.1) == 1.0
    assert _clamp01(-3, default=0.1) == 0.0
    assert _clamp01("nope", default=0.25) == 0.25
    assert _clamp01(None, default=0.25) == 0.25


def test_evidence_from_grounding_maps_source_number_to_item_id():
    sources = [SourceItem("itemA", "A", "..."), SourceItem("itemB", "B", "...")]
    ev = _evidence_from_grounding(
        {"evidence_source": 2, "evidence_span": "the quote"}, sources
    )
    assert ev == [{"item_id": "itemB", "span": "the quote"}]


def test_evidence_from_grounding_handles_missing_or_bad_refs():
    sources = [SourceItem("itemA", "A", "...")]
    assert _evidence_from_grounding({"evidence_span": ""}, sources) == []
    assert _evidence_from_grounding(
        {"evidence_source": 9, "evidence_span": "x"}, sources
    ) == []
    assert _evidence_from_grounding(
        {"evidence_source": None, "evidence_span": "x"}, sources
    ) == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
