"""Per-finding validation pipeline (P8).

Replaces the pipeline's P6 stub (``_stub_validate_all`` → mark everything
"grounded") with a real two-tier validation per ``ResearchFinding``:

    Tier-B  one cheap LLM call asking "does the finding's body actually
            support its title, and is it relevant to the topic?"
            → relation in {supported, partial, unsupported, contradicted}
              + 0–1 score
    Tier-C  critic fan-out across the configured heterogeneous models
            (settings.synapse_verifier_models). Only fires for
            non-supported relations when ``enable_critic_fanout`` is on
            (Tief profile) or when the Tier-B result is contradicted.
            Majority vote → relation; vote spread → agreement signal.

The aggregation step reuses ``services/claim_aggregation`` verbatim
(extracted in P0) — same code path as Synapse-validation, so a future
change to the confidence weighting hits both pipelines.

Mapping verdict → finding.status (Spec §10):
    persist          → "grounded"  (pipeline's PERSIST promotes to KB)
    persist_flagged  → "grounded"  but ``finding.confidence_band="medium"``
                       so the KB item is written with low confidence
    human_review     → "flagged"   (no KnowledgeItem; user resolves)
    contradiction    → "rejected"  (no KnowledgeItem; finding row kept)

This module NEVER raises into the caller. Every LLM failure / parse
failure degrades the finding to ``status="flagged"`` with a defect
explanation in ``extra_data.validation``.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Iterable

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
from services.research_budget import (
    BudgetDegradation,
    BudgetTracker,
    _llm_call_with_budget,
)
from services.synapse_llm import call_json

logger = logging.getLogger("projecthub.research.validation")


# ── Tuning constants ──────────────────────────────────────────────────────

#: Tier-B → C escalation: skip the critic when Tier-B agrees with high score.
_GROUNDING_TRUST_THRESHOLD = 0.8

#: Confidence-bucket thresholds — Spec §5.7 "Normal" defaults; Tief
#: profile overrides via ``ConfidenceThresholds`` arg.
_DEFAULT_THRESHOLDS = ConfidenceThresholds(high=0.7, review=0.5)
_DEFAULT_THRESHOLDS_TIEF = ConfidenceThresholds(high=0.75, review=0.5)

#: Source/finding truncation budget for the validation prompt.
_MAX_TEXT_CHARS = 1500


# ── Value types ───────────────────────────────────────────────────────────


@dataclass
class FindingValidation:
    """Final aggregate validation outcome for one finding.

    ``claim_verdict`` comes straight from ``claim_aggregation.aggregate_claim``.
    ``new_status`` is what the caller should write to
    ``ResearchFinding.status``; ``new_confidence_band`` is the band that
    determines the KnowledgeItem.confidence value during PERSIST.
    """

    claim_verdict: ClaimVerdict
    confidence: float
    confidence_band: str
    verdict: str             # "persist" | "persist_flagged" | "human_review"
    new_status: str          # "grounded" | "flagged" | "rejected"
    defects: list[str]
    tier_b_score: float
    tier_b_relation: str
    critic_votes: list[str] = field(default_factory=list)
    usage: dict = field(default_factory=dict)


# ── Prompts ───────────────────────────────────────────────────────────────


_GROUNDING_PROMPT = """\
Du prüfst die Qualität eines Recherche-Findings vor der Übernahme in die
Wissens-Base. Beurteile streng anhand der Inhalte.

TOPIC: {topic}

FINDING-TITEL: {title}
FINDING-PROVIDER: {provider}
FINDING-INHALT:
{content}

Bewerte die Relation zwischen Titel und Inhalt UND die Relevanz zum Topic:
- supported:    Inhalt stützt den Titel klar UND ist zum Topic relevant
- partial:      Inhalt stützt den Titel teilweise oder Relevanz nur lose
- unsupported:  Inhalt stützt den Titel nicht (oder ist Off-Topic)
- contradicted: Inhalt widerspricht dem Titel direkt

Antworte AUSSCHLIESSLICH als valides JSON:
{{
  "relation": "supported|partial|unsupported|contradicted",
  "score": 0.0,
  "reason": "ein Satz Begründung"
}}
"""


_CRITIC_PROMPT = """\
Du bist ein skeptischer Faktenprüfer-Kritiker. Prüfe, ob der Finding-Inhalt
den Titel wirklich stützt und zum Topic gehört.

TOPIC: {topic}
FINDING-TITEL: {title}
FINDING-INHALT:
{content}

Denke Schritt für Schritt, antworte dann AUSSCHLIESSLICH als valides JSON:
{{
  "relation": "supported|partial|unsupported|contradicted",
  "reasoning": "ein Satz Begründung"
}}

Sei skeptisch bei Superlativen, Mengen, kausalen Behauptungen.
"""


# ── Tier-B grounding ──────────────────────────────────────────────────────


async def _grounding_call(
    *, topic: str, title: str, provider_key: str, content: str,
    budget: BudgetTracker | None, model: str | None,
) -> tuple[GroundingResult, dict]:
    """Single Tier-B LLM call. Returns ``(GroundingResult, usage_dict)``.

    On any failure → ``relation="unsupported"`` with score 0.0; caller
    will escalate to Tier-C (when enabled) or flag the finding outright.
    """
    prompt = _GROUNDING_PROMPT.format(
        topic=topic[:300],
        title=title[:300],
        provider=provider_key,
        content=content[:_MAX_TEXT_CHARS] or "(leer)",
    )
    try:
        result = await _llm_call_with_budget(
            budget, "grounding", 1000, 200,
            call_json, prompt, model=model, session_prefix="research-grounding",
        )
    except BudgetDegradation:
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning("grounding call failed for %s: %s", provider_key, e)
        return GroundingResult(relation="unsupported", score=0.0, evidence=[]), {}

    parsed = getattr(result, "parsed", None)
    usage = getattr(result, "usage", {}) or {}
    if not isinstance(parsed, dict):
        return GroundingResult(relation="unsupported", score=0.0, evidence=[]), usage

    relation = _sanitise_relation(parsed.get("relation"))
    score = _clamp01(parsed.get("score"), default=0.5)
    reason = str(parsed.get("reason") or "")[:300]
    return (
        GroundingResult(
            relation=relation,
            score=score,
            evidence=[{"reason": reason}] if reason else [],
        ),
        usage,
    )


# ── Tier-C critic fan-out ─────────────────────────────────────────────────


async def _critic_call(
    *, topic: str, title: str, content: str,
    model: str | None,
    budget: BudgetTracker | None,
) -> str:
    """Single critic call returning just the relation vote.

    Failures return ``""`` (skipped in aggregation).
    """
    prompt = _CRITIC_PROMPT.format(
        topic=topic[:300], title=title[:300],
        content=content[:_MAX_TEXT_CHARS] or "(leer)",
    )
    try:
        result = await _llm_call_with_budget(
            budget, "critic", 2200, 300,
            call_json, prompt, model=model, session_prefix="research-critic",
        )
    except BudgetDegradation:
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning("critic call failed: %s", e)
        return ""
    parsed = getattr(result, "parsed", None)
    if not isinstance(parsed, dict):
        return ""
    return _sanitise_relation(parsed.get("relation"))


async def _critic_fanout(
    *, topic: str, title: str, content: str,
    budget: BudgetTracker | None,
    verifier_models: list[str],
    verifier_samples: int,
) -> list[str]:
    """Run the critic across heterogeneous models; return relation votes.

    Failures drop silently — an empty list means "no critic signal" and
    the aggregator falls back to the Tier-B grounding result.
    Budget denial in the middle of the fan-out → returns what we have.
    """
    models = select_verifier_models(verifier_models, verifier_samples)
    votes_raw = await asyncio.gather(*[
        _critic_call(
            topic=topic, title=title, content=content,
            model=m, budget=budget,
        )
        for m in models
    ], return_exceptions=True)
    out: list[str] = []
    for v in votes_raw:
        if isinstance(v, BudgetDegradation):
            # Critic budget hit mid-fanout — stop calling, keep what we have.
            break
        if isinstance(v, Exception):
            continue
        if v:
            out.append(v)
    return out


# ── Public API ────────────────────────────────────────────────────────────


async def validate_finding(
    *,
    topic: str,
    title: str,
    snippet: str,
    full_content: str | None,
    source_ref: str,
    provider_key: str,
    enable_critic_fanout: bool,
    thresholds: ConfidenceThresholds | None = None,
    verifier_models: list[str] | None = None,
    verifier_samples: int = 3,
    budget: BudgetTracker | None = None,
    model: str | None = None,
) -> FindingValidation:
    """Validate one finding; never raises.

    Args:
        topic: original research topic — for Tier-B relevance judging.
        title / snippet / full_content / source_ref / provider_key:
            denormalised from the ``ResearchFinding`` row so this
            function stays DB-free (testable, reusable).
        enable_critic_fanout: True for Tief; False for Normal (which
            only fans out on Tier-B = contradicted).
        thresholds: confidence bucketing. ``None`` → Normal defaults
            (high=0.7, review=0.5); Tief callers should pass the
            stricter ``ConfidenceThresholds(high=0.75, review=0.5)``.
        verifier_models / verifier_samples: passed through to
            ``select_verifier_models``. ``None``/empty → single critic
            call with engine default.
        budget: optional BudgetTracker. Both grounding + critic calls
            are budget-gated.
        model: forwarded to call_json — typically None so the proxy
            picks its configured default.

    Returns:
        ``FindingValidation`` with status + confidence + verdict + defects.
    """
    thresholds = thresholds or _DEFAULT_THRESHOLDS
    content = full_content or snippet or ""
    usage_acc: dict = {}

    # ─ Tier B: grounding ─
    try:
        grounding, g_usage = await _grounding_call(
            topic=topic, title=title, provider_key=provider_key,
            content=content, budget=budget, model=model,
        )
    except BudgetDegradation as e:
        # Budget denied even the cheap Tier-B call → flag the finding
        # and move on. The pipeline keeps running on other findings.
        logger.info("validate_finding: budget denied Tier-B for %s: %s", source_ref, e)
        return _flagged_for_budget(source_ref, str(e))
    _merge_usage(usage_acc, g_usage)

    # Decide whether to escalate. Spec §12.2:
    #   Normal: only contradicted escalates
    #   Tief:   everything non-supported (or low Tier-B score) escalates
    needs_critic = False
    if grounding.relation == "contradicted":
        needs_critic = True
    elif enable_critic_fanout and grounding.relation != "supported":
        needs_critic = True
    elif enable_critic_fanout and grounding.score < _GROUNDING_TRUST_THRESHOLD:
        needs_critic = True

    # ─ Tier C: critic fan-out (when escalated) ─
    critic_votes: list[str] = []
    if needs_critic:
        try:
            critic_votes = await _critic_fanout(
                topic=topic, title=title, content=content,
                budget=budget,
                verifier_models=verifier_models or [],
                verifier_samples=verifier_samples,
            )
        except BudgetDegradation as e:
            # Critic denied — use what we have so far (possibly empty).
            logger.info("validate_finding: budget denied Tier-C: %s", e)

    # ─ Aggregate ─
    claim_text = title or snippet[:200] or "(ohne Titel)"
    claim_verdict = aggregate_claim(
        claim_text=claim_text,
        source_item_ids=[source_ref],  # one finding = its own source
        grounding=grounding,
        critic_votes=critic_votes,
    )
    confidence, band = compute_confidence([claim_verdict], thresholds)
    verdict, defects = decide_verdict(confidence, band, [claim_verdict])
    new_status = _verdict_to_status(verdict, claim_verdict.relation)

    return FindingValidation(
        claim_verdict=claim_verdict,
        confidence=confidence,
        confidence_band=band,
        verdict=verdict,
        new_status=new_status,
        defects=defects,
        tier_b_score=grounding.score,
        tier_b_relation=grounding.relation,
        critic_votes=critic_votes,
        usage=usage_acc,
    )


# ── Internal helpers ──────────────────────────────────────────────────────


def _verdict_to_status(verdict: str, relation: str) -> str:
    """Map (verdict, relation) → ResearchFinding.status."""
    # A contradicted relation is rejection-worthy regardless of verdict bucket
    if relation == "contradicted":
        return "rejected"
    if verdict == "persist":
        return "grounded"
    if verdict == "persist_flagged":
        return "grounded"  # still gets persisted, but with low confidence band
    # human_review → flagged finding the user can resolve in the UI
    return "flagged"


def _flagged_for_budget(source_ref: str, reason: str) -> FindingValidation:
    """Construct a synthetic FindingValidation when budget denied the call."""
    grounding = GroundingResult(
        relation="unsupported", score=0.0,
        evidence=[{"reason": f"budget denied: {reason}"[:200]}],
    )
    claim_verdict = aggregate_claim(
        claim_text="(budget denied)",
        source_item_ids=[source_ref],
        grounding=grounding,
        critic_votes=[],
    )
    return FindingValidation(
        claim_verdict=claim_verdict,
        confidence=0.0,
        confidence_band="low",
        verdict="human_review",
        new_status="flagged",
        defects=["Budget verweigerte Tier-B-Validierung — manuell prüfen"],
        tier_b_score=0.0,
        tier_b_relation="unsupported",
        critic_votes=[],
        usage={},
    )


def _sanitise_relation(value: object) -> str:
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("supported", "partial", "unsupported", "contradicted"):
            return v
    return "unsupported"


def _clamp01(value: object, *, default: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _merge_usage(acc: dict, new: dict) -> None:
    """In-place merge token-usage counts."""
    if not isinstance(new, dict):
        return
    for key, val in new.items():
        if isinstance(val, (int, float)):
            acc[key] = acc.get(key, 0) + val
