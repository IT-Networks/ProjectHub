"""Research planner — one LLM call to decompose a topic into sub-queries.

Phase 6 of the Research-Auto-Mode workflow. Sits at the front of the
pipeline: takes the user's topic + the project's enabled-provider list
+ routing hints and returns a structured plan that the orchestrator
then fans out across providers.

Single LLM call by design — the planner doesn't need to chat, it
emits JSON once. ``synapse_llm.call_json`` handles the JSON extraction
+ retry on parse failure already, so this module just builds the
prompt and shapes the response.

Defensive parsing: any malformed or missing field falls back to a
sensible default (one sub-query per enabled provider, equal priority).
The orchestrator must NEVER refuse to run because the planner had a
bad day — at worst we run a slightly less curated plan.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Literal

from services.research_budget import (
    BudgetDegradation,
    BudgetTracker,
    _llm_call_with_budget,
)
from services.synapse_llm import call_json, gen_id

logger = logging.getLogger("projecthub.research.planner")


# ── Value types ────────────────────────────────────────────────────────────


@dataclass
class SubQueryPlan:
    """One planned sub-question + its routing assignment."""

    id: str
    question: str
    providers: list[str]
    rationale: str
    priority: int = 1
    expected_cost: Literal["light", "medium", "heavy"] = "light"
    budget_request: int | None = None


@dataclass
class PlanResult:
    """Aggregate output of one ``plan_subqueries`` call."""

    sub_queries: list[SubQueryPlan]
    raw_response: dict | None = None
    fallback_used: bool = False
    fallback_reason: str | None = None
    usage: dict = field(default_factory=dict)


# ── Routing rubric (embedded in the prompt) ────────────────────────────────

#: One-line hints by topic-type. The planner prompt embeds this verbatim
#: so the LLM has consistent guidance across runs. Edit here = consistent
#: behavior change.
_ROUTING_RUBRIC = """\
- Architektur/Design       → kb_fts, confluence, code_graph, project_documents
- Code/Implementierung     → code_graph, github, kb_fts
- Incidents/Bugs           → jira, log_servers, iq
- Kommunikation/Entscheid. → webex, email, chat_history, project_notes
- Prozess/Policy           → handbook, confluence
- Build/Deploy             → jenkins, github, log_servers
- Compliance/Lizenzen      → iq, confluence
- Allgemeines Projektwissen→ kb_fts, project_documents, project_notes
"""


_PROMPT_TEMPLATE = """\
Du bist ein Recherche-Planer. Zerlege das Thema in atomare Sub-Fragen und
weise jeder Sub-Frage die geeigneten Datenquellen zu.

TOPIC: {topic}

AKTIVE QUELLEN für dieses Projekt (nur diese darfst du wählen):
{providers_list}

ROUTING-HILFE (Faustregeln pro Thementyp):
{rubric}{routing_hints_block}

EXISTIERENDES PROJEKTWISSEN (Top-Einträge — zur Vermeidung doppelter Suche):
{kb_context_block}

CONSTRAINTS:
- Maximal {max_sub_queries} Sub-Fragen
- Pro Sub-Frage maximal {max_providers_per_sq} Quellen
- "expected_cost" = "light" (eine Quelle, schnell) | "medium" (mehrere
  Quellen oder eine teure) | "heavy" (Confluence-Tiefenrecherche,
  großer Code-Graph-Scan o.ä.)

ANTWORTE AUSSCHLIESSLICH als valides JSON in dieser Form:
{{
  "sub_queries": [
    {{
      "id": "sq1",
      "question": "Konkrete Sub-Frage",
      "providers": ["kb_fts", "confluence"],
      "rationale": "Kurzbegründung warum diese Quellen",
      "priority": 1,
      "expected_cost": "medium"
    }}
  ]
}}
Keine Erklärungen außerhalb des JSON. Keine Markdown-Codeblöcke.
"""


# ── Public API ────────────────────────────────────────────────────────────


async def plan_subqueries(
    topic: str,
    *,
    enabled_providers: list[str],
    max_sub_queries: int,
    max_providers_per_sub_query: int,
    routing_hints: str | None = None,
    kb_context: list[str] | None = None,
    budget: BudgetTracker | None = None,
    model: str | None = None,
) -> PlanResult:
    """Plan a topic into structured sub-queries (one LLM call).

    Args:
        topic: user's free-text research topic.
        enabled_providers: subset of the global registry the project
            has turned on. The planner is constrained to these.
        max_sub_queries / max_providers_per_sub_query: from the depth
            profile. The prompt mentions them; we also enforce
            server-side after parsing.
        routing_hints: free-text from ``ProjectResearchSettings.routing_hints``.
            Appended to the rubric block if non-empty.
        kb_context: pre-fetched Top-K KB-titles or one-line summaries
            (caller gets them from the kb_fts provider or a direct query).
            ``None``/empty → omitted from the prompt.
        budget: optional ``BudgetTracker``. When given, the planner call
            is reserved/committed under the ``"planning"`` category.
        model: forwarded to ``call_json`` — typically ``None`` so the
            LLM proxy picks its configured default.

    Returns:
        ``PlanResult`` with the parsed sub-queries (clamped to the
        profile limits + filtered to enabled providers). On any planner
        failure we fall back to a one-sub-query-per-enabled-provider
        plan so the pipeline can still run.

    Raises:
        BudgetDegradation: when the budget tracker denies the planner
            reservation. The orchestrator decides whether to abort the
            run or proceed with the fallback plan.
    """
    if not enabled_providers:
        # No providers → no plan can run. Return empty and let the
        # orchestrator surface this as a config error.
        return PlanResult(
            sub_queries=[],
            fallback_used=True,
            fallback_reason="no_enabled_providers",
        )

    prompt = _build_prompt(
        topic=topic,
        enabled_providers=enabled_providers,
        max_sub_queries=max_sub_queries,
        max_providers_per_sub_query=max_providers_per_sub_query,
        routing_hints=routing_hints,
        kb_context=kb_context or [],
    )

    # Conservative estimate: ~1.5k prompt + 500 output.
    est_in, est_out = 1500, 500

    try:
        result = await _llm_call_with_budget(
            budget,
            "planning",
            est_in,
            est_out,
            call_json,
            prompt,
            model=model,
            session_prefix="research-planner",
        )
    except BudgetDegradation:
        # Re-raise — orchestrator decides whether to abort or fall back.
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning("planner LLM call raised: %s", e)
        return _fallback_plan(
            enabled_providers,
            max_sub_queries=max_sub_queries,
            max_providers_per_sq=max_providers_per_sub_query,
            reason=f"llm_call_failed:{type(e).__name__}",
        )

    # call_json returns LLMResult with .parsed (dict or None) + .usage.
    parsed = getattr(result, "parsed", None)
    usage = getattr(result, "usage", {}) or {}
    if not isinstance(parsed, dict) or not isinstance(parsed.get("sub_queries"), list):
        logger.warning("planner returned malformed JSON; using fallback plan")
        return _fallback_plan(
            enabled_providers,
            max_sub_queries=max_sub_queries,
            max_providers_per_sq=max_providers_per_sub_query,
            reason="malformed_planner_json",
            raw_response=parsed if isinstance(parsed, dict) else None,
            usage=usage,
        )

    sub_queries = _normalise_sub_queries(
        parsed["sub_queries"],
        enabled_providers=set(enabled_providers),
        max_sub_queries=max_sub_queries,
        max_providers_per_sq=max_providers_per_sub_query,
    )
    if not sub_queries:
        return _fallback_plan(
            enabled_providers,
            max_sub_queries=max_sub_queries,
            max_providers_per_sq=max_providers_per_sub_query,
            reason="empty_after_filter",
            raw_response=parsed,
            usage=usage,
        )

    return PlanResult(
        sub_queries=sub_queries,
        raw_response=parsed,
        usage=usage,
    )


# ── Prompt construction ───────────────────────────────────────────────────


def _build_prompt(
    *,
    topic: str,
    enabled_providers: list[str],
    max_sub_queries: int,
    max_providers_per_sub_query: int,
    routing_hints: str | None,
    kb_context: list[str],
) -> str:
    providers_list = "\n".join(f"  - {p}" for p in enabled_providers)

    routing_hints_block = ""
    if routing_hints:
        routing_hints_block = (
            f"\n\nPROJEKT-SPEZIFISCHE HINTS:\n  {routing_hints.strip()}"
        )

    if kb_context:
        kb_lines = "\n".join(f"  - {ln[:200]}" for ln in kb_context[:5])
    else:
        kb_lines = "  (keine vorherigen Einträge)"

    return _PROMPT_TEMPLATE.format(
        topic=topic.strip()[:1000],
        providers_list=providers_list,
        rubric=_ROUTING_RUBRIC,
        routing_hints_block=routing_hints_block,
        kb_context_block=kb_lines,
        max_sub_queries=max_sub_queries,
        max_providers_per_sq=max_providers_per_sub_query,
    )


# ── Defensive normalisation ───────────────────────────────────────────────


_VALID_COSTS = ("light", "medium", "heavy")


def _normalise_sub_queries(
    raw: list,
    *,
    enabled_providers: set[str],
    max_sub_queries: int,
    max_providers_per_sq: int,
) -> list[SubQueryPlan]:
    """Clamp + filter LLM output to what the orchestrator can actually run.

    * Drops sub-queries with no question text
    * Filters provider list to ``enabled_providers``; drops sub-queries
      with zero remaining providers
    * Caps providers_per_sq + total count
    * Normalises ``expected_cost`` to one of {light, medium, heavy}
    """
    out: list[SubQueryPlan] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        question = str(entry.get("question") or "").strip()
        if not question:
            continue

        providers_in = entry.get("providers")
        if not isinstance(providers_in, list):
            continue
        providers = [
            p for p in providers_in
            if isinstance(p, str) and p in enabled_providers
        ][:max_providers_per_sq]
        if not providers:
            continue

        try:
            priority = int(entry.get("priority", 1))
        except (TypeError, ValueError):
            priority = 1
        priority = max(1, min(priority, 10))

        cost = str(entry.get("expected_cost", "light")).lower()
        if cost not in _VALID_COSTS:
            cost = "light"

        budget_request = entry.get("budget_request")
        try:
            budget_request_int = int(budget_request) if budget_request else None
        except (TypeError, ValueError):
            budget_request_int = None
        # Sanity: positive only; budget tracker enforces the upper cap.
        if budget_request_int is not None and budget_request_int <= 0:
            budget_request_int = None

        sq_id = str(entry.get("id") or "").strip() or f"sq-{gen_id()[:8]}"

        out.append(SubQueryPlan(
            id=sq_id,
            question=question[:500],
            providers=providers,
            rationale=str(entry.get("rationale") or "")[:300],
            priority=priority,
            expected_cost=cost,
            budget_request=budget_request_int,
        ))
        if len(out) >= max_sub_queries:
            break

    return out


# ── Fallback plan ─────────────────────────────────────────────────────────


def _fallback_plan(
    enabled_providers: list[str],
    *,
    max_sub_queries: int,
    max_providers_per_sq: int,
    reason: str,
    raw_response: dict | None = None,
    usage: dict | None = None,
) -> PlanResult:
    """One-sub-query-per-provider fallback when the LLM plan is unusable.

    The pipeline can still run on this — every enabled provider is
    queried with the original topic. Quality is lower (no semantic
    routing), but the run completes instead of failing.
    """
    sub_queries: list[SubQueryPlan] = []
    for p in enabled_providers[:max_sub_queries]:
        sub_queries.append(SubQueryPlan(
            id=f"fallback-{p}",
            question="",  # filled by orchestrator with the original topic
            providers=[p],
            rationale=f"fallback: planner unusable ({reason})",
            priority=1,
            expected_cost="light",
        ))
    return PlanResult(
        sub_queries=sub_queries,
        raw_response=raw_response,
        fallback_used=True,
        fallback_reason=reason,
        usage=usage or {},
    )
