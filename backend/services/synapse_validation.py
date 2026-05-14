"""Validation — fourth stage of the synapse pipeline.

A draft ``Synapse`` (from Phase 3 synthesis) carries
``extra_data["draft_claims"]`` — atomic claims, each citing the source
items it was built from. This module decides whether to trust them, in a
two-tier design (cheapest first):

    Tier B — grounding ("LLM-as-NLI"): one cheap, narrowly-prompted call
             per claim checks it against its cited sources. A clearly
             SUPPORTED claim is trusted as-is; everything else escalates.
    Tier C — critic fan-out: escalated claims get N independent critic
             calls, ideally across heterogeneous models (config:
             ``synapse_verifier_models``) to avoid agreement collapse.
             Majority vote → relation; vote spread → agreement signal.

    Tier D — aggregation: per-claim relation + agreement + grounding
             score → composite synapse confidence, bucketed into a band.
    Tier E — verdict: high → persist · medium → persist_flagged ·
             low or any contradiction → human_review (review queue).

Spike note (2026-05-14): no local NLI model is available, so Tier B is
``LLMGroundingChecker``. The ``GroundingChecker`` protocol keeps a real
NLI model (MiniCheck-class) slot-in-able later without touching Tiers C–E.

Pure decision logic (``_aggregate_claim``, ``compute_confidence``,
``decide_verdict``, ``select_verifier_models``) is kept free of I/O so it
can be unit-tested directly.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Protocol, TYPE_CHECKING

from config import settings
from services.synapse_llm import call_json, merge_usage

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from models.synapse import Synapse

logger = logging.getLogger("projecthub.synapse")

# Claim relations, ordered worst → best for tie-breaking.
_VALID_RELATIONS = ("contradicted", "unsupported", "partial", "supported")

# How a relation maps to a 0–1 quality score for confidence aggregation.
_RELATION_SCORE = {
    "supported": 1.0,
    "partial": 0.6,
    "unsupported": 0.2,
    "contradicted": 0.0,
}

# Tier B → C escalation: a grounding result this strong skips the critic.
_GROUNDING_TRUST_THRESHOLD = 0.8

# A synapse with any contradicted claim is capped here, forcing human_review.
_CONTRADICTION_CONFIDENCE_CAP = 0.4

# Truncation budget for a source item inside a validation prompt.
_MAX_SOURCE_CHARS = 1100

# Bound concurrent LLM calls so the pipeline doesn't flood the AI-Assist proxy.
_LLM_SEMAPHORE = asyncio.Semaphore(max(1, settings.synapse_max_llm_concurrency))


# --- Data structures --------------------------------------------------------

@dataclass
class GroundingResult:
    """Outcome of a Tier-B grounding check for one claim."""

    relation: str            # supported | contradicted | unsupported | partial
    score: float             # 0–1 confidence in the relation
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


@dataclass
class SynapseValidation:
    """Aggregate validation result for one synapse."""

    confidence: float
    confidence_band: str
    verdict: str
    claims: list[ClaimVerdict]
    defects: list[str]
    usage: dict = field(default_factory=dict)


@dataclass
class ValidationStats:
    synapses_validated: int = 0
    persisted: int = 0       # verdict == persist
    flagged: int = 0         # verdict == persist_flagged
    review: int = 0          # verdict == human_review
    usage: dict = field(default_factory=dict)


# --- Source representation --------------------------------------------------

@dataclass
class SourceItem:
    item_id: str
    title: str
    text: str


# --- Grounding checker (Tier B) — pluggable --------------------------------

class GroundingChecker(Protocol):
    """A cheap claim↔sources entailment check.

    v1 implementation is ``LLMGroundingChecker``. A future
    ``MiniCheckGroundingChecker`` (real local NLI model) can be dropped in
    without changing the rest of the pipeline.
    """

    name: str

    async def check(
        self, claim_text: str, sources: list[SourceItem]
    ) -> tuple[GroundingResult, dict]:
        """Return ``(result, llm_usage)``. Never raises."""
        ...


_GROUNDING_PROMPT = """Du bist ein strenger Faktenprüfer. Prüfe, ob die QUELLEN die AUSSAGE stützen.

AUSSAGE: {claim}

QUELLEN:
{sources}

Antworte AUSSCHLIESSLICH als valides JSON:
{{
  "relation": "supported|contradicted|unsupported|partial",
  "score": 0.0,
  "evidence_source": null,
  "evidence_span": ""
}}

Bedeutung:
- supported:    eine Quelle belegt die Aussage eindeutig
- contradicted: eine Quelle sagt das Gegenteil
- unsupported:  keine Quelle behandelt die Aussage
- partial:      die Aussage ist breiter/stärker als die Quellen hergeben
"score" = deine Konfidenz in die Relation (0.0–1.0).
"evidence_source" = Quell-Nummer mit dem Beleg (oder null).
"evidence_span" = wörtliches Zitat aus der Quelle (oder "")."""


class LLMGroundingChecker:
    """Tier-B grounding via one cheap, narrowly-prompted LLM call."""

    name = "llm-as-nli"

    async def check(
        self, claim_text: str, sources: list[SourceItem]
    ) -> tuple[GroundingResult, dict]:
        if not sources:
            return GroundingResult(relation="unsupported", score=0.5), {}

        numbered = _number_sources(sources)
        prompt = _GROUNDING_PROMPT.format(claim=claim_text, sources=numbered)
        res = await _guarded_call_json(prompt, session_prefix="grounding")
        if not res.ok or not isinstance(res.parsed, dict):
            # Unverifiable → treat as unsupported, low score → will escalate.
            return GroundingResult(relation="unsupported", score=0.0), res.usage

        relation = _sanitise_relation(res.parsed.get("relation"))
        score = _clamp01(res.parsed.get("score"), default=0.5)
        evidence = _evidence_from_grounding(res.parsed, sources)
        return GroundingResult(relation=relation, score=score, evidence=evidence), res.usage


# --- Critic fan-out (Tier C) ------------------------------------------------

_CRITIC_PROMPT = """Du bist ein skeptischer Faktenprüfer-Kritiker. Prüfe, ob die AUSSAGE durch die QUELLEN gedeckt ist.

AUSSAGE: {claim}

QUELLEN:
{sources}

Denke Schritt für Schritt, antworte dann AUSSCHLIESSLICH als valides JSON:
{{
  "claim_quote": "wörtlicher Ausschnitt aus der Aussage",
  "evidence_quote": "wörtliches Zitat aus einer Quelle, oder 'KEIN BELEG'",
  "relation": "supported|contradicted|unsupported|partial",
  "reasoning": "ein Satz Begründung"
}}

Regeln:
- Wörtlich zitieren, nicht umschreiben.
- Eine Aussage als "unsupported" zu markieren ist korrekt und erwünscht — erfinde keine Belege.
- Sei skeptisch bei Mengenangaben, Superlativen, Kausalbehauptungen."""


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


async def _critic_fanout(
    claim_text: str, sources: list[SourceItem], usage_acc: dict
) -> list[str]:
    """Run the critic across the configured models; return relation votes.

    Failed calls are dropped — an empty list means "no critic signal",
    and the aggregator falls back to the grounding result.
    """
    models = select_verifier_models(
        settings.synapse_verifier_models, settings.synapse_verifier_samples
    )
    numbered = _number_sources(sources)
    prompt = _CRITIC_PROMPT.format(claim=claim_text, sources=numbered)

    results = await asyncio.gather(*[
        _guarded_call_json(prompt, model=model, session_prefix="critic")
        for model in models
    ])

    votes: list[str] = []
    for res in results:
        merge_usage(usage_acc, res.usage)
        if res.ok and isinstance(res.parsed, dict):
            votes.append(_sanitise_relation(res.parsed.get("relation")))
    return votes


# --- Aggregation (Tier D) — pure --------------------------------------------

def _aggregate_claim(
    claim_text: str,
    source_item_ids: list[str],
    grounding: GroundingResult,
    critic_votes: list[str],
) -> ClaimVerdict:
    """Combine the grounding result and critic votes into one ClaimVerdict."""
    if critic_votes:
        tally = Counter(critic_votes)
        # Tie-break toward the more severe relation (lower index in _VALID_RELATIONS).
        top = max(tally.values())
        relation = min(
            (r for r, c in tally.items() if c == top),
            key=_VALID_RELATIONS.index,
        )
        agreement = top / len(critic_votes)
        votes = dict(tally)
    else:
        # No critic signal — trust the grounding tier.
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


def compute_confidence(claims: list[ClaimVerdict]) -> tuple[float, str]:
    """Composite 0–1 synapse confidence + its band.

    Each claim contributes ``(0.7·relation_score + 0.3·nli_score)`` scaled
    by verifier agreement. Any contradicted claim caps the whole synapse
    low — a single refuted claim should never read as trustworthy.
    """
    if not claims:
        return 0.0, "low"

    total = 0.0
    for c in claims:
        base = _RELATION_SCORE.get(c.relation, 0.0)
        nli = c.nli_score if c.nli_score is not None else base
        raw = 0.7 * base + 0.3 * nli
        # Low agreement = uncertain → damp toward 0, but never fully erase.
        total += raw * (0.5 + 0.5 * c.verifier_agreement)
    confidence = total / len(claims)

    if any(c.relation == "contradicted" for c in claims):
        confidence = min(confidence, _CONTRADICTION_CONFIDENCE_CAP)

    if confidence >= settings.synapse_confidence_high:
        band = "high"
    elif confidence >= settings.synapse_confidence_review:
        band = "medium"
    else:
        band = "low"
    return round(confidence, 3), band


def decide_verdict(
    confidence: float, band: str, claims: list[ClaimVerdict]
) -> tuple[str, list[str]]:
    """Map (confidence, band, claims) → verdict + human-readable defects."""
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


# --- Orchestrator (Tier E: persist) -----------------------------------------

async def validate_synapse(
    db: "AsyncSession",
    synapse: "Synapse",
    *,
    stats: ValidationStats | None = None,
    grounding_checker: GroundingChecker | None = None,
) -> SynapseValidation:
    """Validate one draft synapse, persist the result, return the summary.

    Reads ``synapse.extra_data["draft_claims"]``, runs Tiers B–D, writes
    ``SynapseClaim`` rows, updates the synapse's confidence/band/verdict/
    status, and enqueues a review row when the verdict is human_review.
    The caller owns the DB session; this commits.
    """
    from sqlalchemy import select
    from models.knowledge import KnowledgeItem
    from models.synapse import SynapseClaim, KnowledgeReviewQueue
    from services.synapse_llm import gen_id

    stats = stats or ValidationStats()
    checker = grounding_checker or LLMGroundingChecker()
    usage: dict = {}

    draft_claims = synapse.extra_data_dict.get("draft_claims") or []

    # Load every source item the synapse references, once.
    source_map: dict[str, SourceItem] = {}
    item_ids = synapse.source_item_ids_list
    if item_ids:
        rows = await db.execute(
            select(KnowledgeItem).where(KnowledgeItem.id.in_(item_ids))
        )
        for item in rows.scalars().all():
            source_map[item.id] = SourceItem(
                item_id=item.id,
                title=item.title or "",
                text=(item.content_plain or "")[:_MAX_SOURCE_CHARS],
            )

    # Tier B: ground every claim concurrently (cheap calls, bounded by semaphore).
    grounding_pairs = await asyncio.gather(*[
        checker.check(
            claim.get("text", ""),
            [source_map[sid] for sid in (claim.get("source_item_ids") or [])
             if sid in source_map],
        )
        for claim in draft_claims
    ]) if draft_claims else []

    # Tier C + D: escalate hard claims, aggregate each into a ClaimVerdict.
    claim_verdicts: list[ClaimVerdict] = []
    for claim, (grounding, g_usage) in zip(draft_claims, grounding_pairs):
        merge_usage(usage, g_usage)
        claim_text = claim.get("text", "")
        source_item_ids = [
            sid for sid in (claim.get("source_item_ids") or []) if sid in source_map
        ]
        sources = [source_map[sid] for sid in source_item_ids]

        trusted = (
            grounding.relation == "supported"
            and grounding.score >= _GROUNDING_TRUST_THRESHOLD
        )
        critic_votes: list[str] = []
        if not trusted:
            critic_votes = await _critic_fanout(claim_text, sources, usage)

        claim_verdicts.append(
            _aggregate_claim(claim_text, source_item_ids, grounding, critic_votes)
        )

    # Tier D: composite confidence; Tier E: verdict.
    confidence, band = compute_confidence(claim_verdicts)
    verdict, defects = decide_verdict(confidence, band, claim_verdicts)

    # --- Persist ---
    for cv in claim_verdicts:
        claim_row = SynapseClaim(
            id=gen_id(),
            synapse_id=synapse.id,
            claim_text=cv.claim_text,
            relation=cv.relation,
            nli_score=cv.nli_score,
            verifier_agreement=cv.verifier_agreement,
        )
        claim_row.evidence_list = cv.evidence
        claim_row.verifier_votes_dict = cv.verifier_votes
        db.add(claim_row)

    synapse.confidence = confidence
    synapse.confidence_band = band
    synapse.verdict = verdict
    synapse.status = "validated"
    extra = synapse.extra_data_dict
    extra["validation"] = {
        "defects": defects,
        "usage": usage,
        "grounding_checker": checker.name,
    }
    synapse.extra_data_dict = extra

    if verdict == "human_review":
        reason = defects[0] if defects else f"Konfidenz {confidence} unter Schwelle"
        db.add(KnowledgeReviewQueue(
            id=gen_id(),
            project_id=synapse.project_id,
            synapse_id=synapse.id,
            reason=reason[:300],
        ))

    await db.commit()

    # --- Stats ---
    stats.synapses_validated += 1
    if verdict == "persist":
        stats.persisted += 1
    elif verdict == "persist_flagged":
        stats.flagged += 1
    else:
        stats.review += 1
    merge_usage(stats.usage, {"total_tokens": usage.get("total_tokens", 0)})
    stats.usage["calls"] = stats.usage.get("calls", 0) + usage.get("calls", 0)

    return SynapseValidation(
        confidence=confidence,
        confidence_band=band,
        verdict=verdict,
        claims=claim_verdicts,
        defects=defects,
        usage=usage,
    )


# --- Internal helpers -------------------------------------------------------

async def _guarded_call_json(prompt: str, *, model: str | None = None,
                             session_prefix: str = "synapse"):
    """``call_json`` wrapped in the concurrency semaphore."""
    async with _LLM_SEMAPHORE:
        return await call_json(prompt, model=model, session_prefix=session_prefix)


def _number_sources(sources: list[SourceItem]) -> str:
    if not sources:
        return "(keine Quellen)"
    return "\n\n".join(
        f"[{i}] {s.title}\n{s.text}" for i, s in enumerate(sources, start=1)
    )


def _sanitise_relation(value: object) -> str:
    if isinstance(value, str) and value.strip().lower() in _VALID_RELATIONS:
        return value.strip().lower()
    return "unsupported"


def _clamp01(value: object, *, default: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _evidence_from_grounding(
    parsed: dict, sources: list[SourceItem]
) -> list[dict]:
    """Map a grounding response's ``evidence_source`` number → item id + span."""
    span = str(parsed.get("evidence_span") or "").strip()
    if not span:
        return []
    raw_num = parsed.get("evidence_source")
    try:
        idx = int(raw_num) - 1
    except (TypeError, ValueError):
        return []
    if not (0 <= idx < len(sources)):
        return []
    return [{"item_id": sources[idx].item_id, "span": span[:500]}]
