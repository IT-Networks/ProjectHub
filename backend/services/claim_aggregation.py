"""Pure claim-validation aggregation primitives.

Shared between two pipelines that both validate atomic factual claims
against cited sources:

    1. Synapse validation (existing, ``services/synapse_validation.py``)
       — runs over post-synthesis Synapse drafts.
    2. Research Auto-Mode validation (planned, ``services/research_validation.py``)
       — runs over freshly extracted Findings during Auto-Mode.

The functions below are kept **I/O-free** so unit tests don't need a DB,
a config, or an LLM. Settings-bound thresholds are passed in as a
``ConfidenceThresholds`` value object — callers that want the Synapse
defaults call :func:`get_synapse_thresholds`.

Design note: this module is the extract from ``synapse_validation.py``'s
``_aggregate_claim`` / ``compute_confidence`` / ``decide_verdict`` /
``select_verifier_models`` block (Phase 0 of the Research-Auto-Mode plan,
see ``claudedocs/design_research_auto_mode_20260516.md`` §12). Behaviour
is preserved bit-exact so the existing 28/28 synapse tests stay green
and the new Auto-Mode validator gets a tested foundation for free.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

# ── Constants ───────────────────────────────────────────────────────────────

#: Claim relations, ordered worst → best so ``min(..., key=index)`` tie-breaks
#: toward the more severe verdict.
VALID_RELATIONS: tuple[str, ...] = (
    "contradicted",
    "unsupported",
    "partial",
    "supported",
)

#: How a relation maps to a 0–1 quality score for confidence aggregation.
RELATION_SCORE: dict[str, float] = {
    "supported": 1.0,
    "partial": 0.6,
    "unsupported": 0.2,
    "contradicted": 0.0,
}

#: A synapse with any contradicted claim is capped here, forcing human_review.
CONTRADICTION_CONFIDENCE_CAP: float = 0.4


# ── Value objects ───────────────────────────────────────────────────────────

@dataclass
class GroundingResult:
    """Outcome of a Tier-B grounding check for one claim."""

    relation: str  # supported | contradicted | unsupported | partial
    score: float  # 0–1 confidence in the relation
    evidence: list[dict] = field(default_factory=list)
    # [{"item_id": "...", "span": "..."}]


@dataclass
class ClaimVerdict:
    """Final per-claim outcome after grounding + (optional) critic fan-out."""

    claim_text: str
    source_item_ids: list[str]
    relation: str
    evidence: list[dict]
    nli_score: float | None
    verifier_agreement: float
    verifier_votes: dict


@dataclass(frozen=True)
class ConfidenceThresholds:
    """Bucketing thresholds for ``band_from_confidence`` / ``compute_confidence``.

    ``high``  — confidences ≥ this map to band ``"high"``.
    ``review``— confidences ≥ this (but < high) map to ``"medium"``.
                Below this they map to ``"low"`` and trigger human_review.
    """

    high: float
    review: float


# ── Pure functions ──────────────────────────────────────────────────────────

def select_verifier_models(
    configured: list[str], samples: int
) -> list[str | None]:
    """Decide which models the critic fan-out runs on.

    With ≥2 configured models, cycle through them for ``samples`` calls —
    real answer diversity. With 0 or 1 configured model, return a single
    ``None`` (engine default): repeating identical calls adds cost without
    diversity, so there is no point fanning out.
    """
    samples = max(1, samples)
    if len(configured) >= 2:
        return [configured[i % len(configured)] for i in range(samples)]
    return [None]


def aggregate_claim(
    claim_text: str,
    source_item_ids: list[str],
    grounding: GroundingResult,
    critic_votes: list[str],
) -> ClaimVerdict:
    """Combine the grounding result and critic votes into one ClaimVerdict.

    When critic votes exist, the majority vote wins; ties break toward the
    more severe relation. Agreement is ``top_votes / total_votes``.
    No critic signal → trust the grounding tier as-is.
    """
    if critic_votes:
        tally = Counter(critic_votes)
        top = max(tally.values())
        relation = min(
            (r for r, c in tally.items() if c == top),
            key=VALID_RELATIONS.index,
        )
        agreement = top / len(critic_votes)
        votes = dict(tally)
    else:
        relation = grounding.relation
        agreement = grounding.score
        votes = {}

    return ClaimVerdict(
        claim_text=claim_text,
        source_item_ids=source_item_ids,
        relation=relation,
        evidence=grounding.evidence,
        nli_score=grounding.score,
        verifier_agreement=round(agreement, 3),
        verifier_votes=votes,
    )


def band_from_confidence(
    confidence: float, thresholds: ConfidenceThresholds
) -> str:
    """Bucket a 0–1 confidence into ``"high" | "medium" | "low"``."""
    if confidence >= thresholds.high:
        return "high"
    if confidence >= thresholds.review:
        return "medium"
    return "low"


def compute_confidence(
    claims: list[ClaimVerdict], thresholds: ConfidenceThresholds
) -> tuple[float, str]:
    """Composite 0–1 confidence + its band, over a list of claim verdicts.

    Each claim contributes ``(0.7·relation_score + 0.3·nli_score)`` scaled
    by verifier agreement. Any contradicted claim caps the whole result
    low — a single refuted claim should never read as trustworthy.
    Empty input returns ``(0.0, "low")``.
    """
    if not claims:
        return 0.0, "low"

    total = 0.0
    for c in claims:
        base = RELATION_SCORE.get(c.relation, 0.0)
        nli = c.nli_score if c.nli_score is not None else base
        raw = 0.7 * base + 0.3 * nli
        # Low agreement = uncertain → damp toward 0, but never fully erase.
        total += raw * (0.5 + 0.5 * c.verifier_agreement)
    confidence = total / len(claims)

    if any(c.relation == "contradicted" for c in claims):
        confidence = min(confidence, CONTRADICTION_CONFIDENCE_CAP)

    return round(confidence, 3), band_from_confidence(confidence, thresholds)


def decide_verdict(
    confidence: float, band: str, claims: list[ClaimVerdict]
) -> tuple[str, list[str]]:
    """Map (confidence, band, claims) → verdict + human-readable defects.

    Verdicts:
      * ``"persist"``         — band == high, no contradictions
      * ``"persist_flagged"`` — band == medium, no contradictions
      * ``"human_review"``    — any contradiction, or band == low

    Defects list is German-language and intended for UI display next to
    the persisted item, e.g. in the review queue.
    """
    defects: list[str] = []
    for i, c in enumerate(claims, start=1):
        if c.relation == "contradicted":
            defects.append(f"Aussage {i} wird von den Quellen widersprochen")
        elif c.relation == "unsupported":
            defects.append(f"Aussage {i} ist durch keine Quelle belegt")
        elif c.relation == "partial":
            defects.append(f"Aussage {i} ist stärker formuliert als belegt")

    has_contradiction = any(c.relation == "contradicted" for c in claims)
    if has_contradiction or band == "low":
        return "human_review", defects
    if band == "high":
        return "persist", defects
    return "persist_flagged", defects


# ── Convenience: Synapse-bound thresholds ──────────────────────────────────

def get_synapse_thresholds() -> ConfidenceThresholds:
    """Pull confidence thresholds from the Synapse-specific settings.

    Kept as a separate helper so unit tests of the pure logic don't have
    to import or stub the global ``settings`` object. Auto-Mode validation
    (Research) will get its own helper once those settings exist.
    """
    from config import settings

    return ConfidenceThresholds(
        high=settings.synapse_confidence_high,
        review=settings.synapse_confidence_review,
    )
