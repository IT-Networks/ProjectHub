"""Token budget tracker with auto-degradation for Auto-Mode (P3c).

Replaces the naive ``max_llm_calls: int`` cap with a *category-based,
token-counted* budget that degrades gracefully under pressure instead
of crashing. Two design points worth keeping in mind:

    1. **Categories matter** — a 7k-token critic call costs an order
       of magnitude more than a 200-token rerank batch; one global
       counter would hide where the budget is going. The tracker
       keeps a per-category counter and surfaces it both in the
       run-final ``token_usage`` JSON and in the live
       ``research_budget`` SSE event.

    2. **Rerank + embedding are exempt** — they're structural cost
       (essential for everything downstream) and have their own
       self-limit baked into the rerank adapter (``max_batches *
       batch_size``). Counting them against the total cap would force
       the tracker to deny calls that prevent *bigger* downstream
       costs.

Pressure ladder (spec §5.7) maps ``total_non_exempt / hard_cap`` to a
level; transitions emit one ``research_budget`` SSE event each so the
UI can show the budget-bar going red. The pipeline orchestrator (P6)
reads ``suggested_action`` from a denied reservation and reacts —
skip_critic, skip_lateral, skip_synthesis, shorter_summary — exactly
the auto-degradation ladder spelt out in the design doc.

Adaptive extension: the planner can request **once per run** to bump
the hard cap by up to ``max_extension_fraction`` (default 30%). This
exists so a sub-query that legitimately needs a huge source (a
Confluence-Deep run hitting a 50-page space) doesn't have to fight a
budget that was set conservatively for the average case.

The tracker NEVER raises on commit — commits always succeed. ``reserve``
returns ``allow=False`` with a hint when over budget; ``_llm_call_with_budget``
is the helper that turns that hint into a ``BudgetDegradation`` exception
for the caller to handle.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger("projecthub.research.budget")


# ── Vocabulary ──────────────────────────────────────────────────────────────

#: All categories the tracker recognises. Anything not in this set is
#: still counted under its own name (the tracker is permissive about
#: new categories), but the auto-degradation ladder only maps these.
CATEGORIES: tuple[str, ...] = (
    "planning",
    "embedding",
    "rerank",
    "summary",
    "entity_extract",
    "lateral_plan",
    "grounding",
    "critic",
    "synthesis",
)

#: Categories that bypass the total cap. Their growth is bounded by the
#: rerank adapter's own ``max_batches * batch_size`` cap upstream, so
#: counting them here would penalise structurally-necessary work.
DEFAULT_EXEMPT: tuple[str, ...] = ("rerank", "embedding")

#: Ordered pressure levels — index() gives a comparable rank.
PRESSURE_OK = "ok"
PRESSURE_WARN = "warn"
PRESSURE_TIGHT = "tight"
PRESSURE_CRITICAL = "critical"
PRESSURE_EXTREME = "extreme"
PRESSURE_EXHAUSTED = "exhausted"
PRESSURE_LEVELS: tuple[str, ...] = (
    PRESSURE_OK,
    PRESSURE_WARN,
    PRESSURE_TIGHT,
    PRESSURE_CRITICAL,
    PRESSURE_EXTREME,
    PRESSURE_EXHAUSTED,
)


def _level_rank(level: str) -> int:
    """Index a level string for ordering comparisons."""
    try:
        return PRESSURE_LEVELS.index(level)
    except ValueError:
        return 0


# ── Threshold profiles ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class PressureThresholds:
    """Where each level kicks in, as ratio of ``total_non_exempt / hard_cap``.

    The mapping is monotonic: warn ≤ tight ≤ critical ≤ extreme. We
    don't bake in ``exhausted`` because that's always "≥ 1.0" — when
    the user has burned 100% of the cap, no further reservations are
    granted regardless of category.
    """

    warn: float
    tight: float
    critical: float
    extreme: float


#: Default profiles (spec §5.7). The Tief profile starts warning earlier
#: because it has further to fall — there are more downstream stages
#: that can still be degraded usefully.
DEFAULT_THRESHOLD_PROFILES: dict[str, PressureThresholds] = {
    "normal": PressureThresholds(warn=0.80, tight=0.85, critical=0.90, extreme=0.95),
    "tief": PressureThresholds(warn=0.70, tight=0.80, critical=0.85, extreme=0.95),
}


# ── Policy + result types ──────────────────────────────────────────────────


@dataclass
class TokenBudgetPolicy:
    """Per-run budget configuration.

    Constructed once at run-start from the project's depth-profile
    settings; immutable for the lifetime of the run (the tracker
    instance owns the mutable state).
    """

    soft_cap_tokens: int
    hard_cap_tokens: int
    per_category_caps: dict[str, int] = field(default_factory=dict)
    exempt_categories: tuple[str, ...] = DEFAULT_EXEMPT
    #: Which threshold profile to use ("normal" or "tief"). Custom
    #: profiles can be registered via ``BudgetTracker(..., thresholds=...)``.
    threshold_profile: str = "normal"
    #: Adaptive extension allowance (spec §5.7).
    max_adaptive_extensions: int = 1
    max_extension_fraction: float = 0.30


@dataclass
class ReservationResult:
    """What ``BudgetTracker.reserve`` tells the caller.

    On ``allow=False`` the orchestrator reads ``suggested_action`` and
    applies the corresponding degradation (skip_critic, skip_lateral,
    shorter_summary, etc.) — see ``_action_for_category``.
    """

    allow: bool
    pressure_before: str
    pressure_after_est: str
    suggested_action: str | None = None
    reason: str | None = None


@dataclass
class BudgetSnapshot:
    """Immutable point-in-time view of the tracker state.

    Returned by ``BudgetTracker.snapshot_obj()`` for in-process use;
    serialised via ``BudgetTracker.snapshot()`` for JSON persistence
    into ``ResearchRun.token_usage``.
    """

    by_category: dict[str, int]
    total: int  # sum of non-exempt categories
    total_exempt: int  # sum of exempt categories (informational)
    soft_cap: int
    hard_cap: int
    max_pressure_reached: str
    degradations_triggered: list[str]
    extensions_used: int
    extension_amount: int


# ── Exceptions ─────────────────────────────────────────────────────────────


class BudgetDegradation(RuntimeError):
    """Raised by ``_llm_call_with_budget`` when a reservation is denied.

    ``suggested_action`` is what the caller should do — e.g.
    ``"skip_critic"``, ``"shorter_summary"``, ``"skip_lateral"``. The
    pipeline orchestrator handles these by stepping down a level on
    the auto-degradation ladder, never by aborting the run.
    """

    def __init__(self, suggested_action: str, reason: str = ""):
        self.suggested_action = suggested_action
        self.reason = reason
        super().__init__(f"{suggested_action}: {reason}" if reason else suggested_action)


class BudgetExhausted(RuntimeError):
    """Raised when even degradation can't continue (final-stage stop).

    Used by callers who can't keep going on any reduced strategy —
    e.g. when the per-category cap blocks the only allowed call type
    for that stage. The orchestrator catches this and finalises the
    run with ``status="partial"`` instead of ``"error"``.
    """


# ── The tracker ────────────────────────────────────────────────────────────


#: Mapping category → suggested degradation action. Read by
#: ``_action_for_category`` when a reservation gets denied for a
#: specific category cap.
_CATEGORY_ACTIONS: dict[str, str] = {
    "critic": "skip_critic",
    "synthesis": "skip_synthesis",
    "lateral_plan": "skip_lateral",
    "entity_extract": "skip_lateral",
    "summary": "shorter_summary",
    "grounding": "skip_grounding",
    "planning": "abort_subquery",
}


SSEEmitFn = Callable[[str, BudgetSnapshot], Awaitable[None]]


class BudgetTracker:
    """Async-safe per-run token budget bookkeeping.

    Lifecycle: one instance per ``ResearchRun``. The orchestrator
    constructs it from ``settings.research.profiles[depth].budget``,
    plumbs it down to every Stage that costs tokens, and at run-end
    calls ``snapshot()`` to persist into ``ResearchRun.token_usage``.

    Thread/coroutine safety: a single ``asyncio.Lock`` guards every
    state mutation. SSE emission happens AFTER the lock releases so
    a slow subscriber can't stall the pipeline.
    """

    def __init__(
        self,
        policy: TokenBudgetPolicy,
        *,
        sse_emit: SSEEmitFn | None = None,
        thresholds: PressureThresholds | None = None,
    ):
        self.policy = policy
        self._lock = asyncio.Lock()
        self._by_cat: dict[str, int] = {c: 0 for c in CATEGORIES}
        self._max_pressure_reached: str = PRESSURE_OK
        self._degradations_triggered: list[str] = []
        self._sse_emit = sse_emit
        self._extensions_used = 0
        self._extension_amount = 0
        self._exempt = frozenset(policy.exempt_categories)
        self._thresholds = thresholds or DEFAULT_THRESHOLD_PROFILES.get(
            policy.threshold_profile, DEFAULT_THRESHOLD_PROFILES["normal"]
        )

    # ── Read-only views ────────────────────────────────────────────────

    @property
    def total_non_exempt(self) -> int:
        return sum(v for c, v in self._by_cat.items() if c not in self._exempt)

    @property
    def total_exempt(self) -> int:
        return sum(v for c, v in self._by_cat.items() if c in self._exempt)

    @property
    def hard_cap_effective(self) -> int:
        """Hard cap including any adaptive extension applied."""
        return self.policy.hard_cap_tokens + self._extension_amount

    @property
    def soft_cap_effective(self) -> int:
        """Soft cap scaled proportionally to the extension amount."""
        base = self.policy.hard_cap_tokens
        if self._extension_amount > 0 and base > 0:
            ratio = self.policy.soft_cap_tokens / base
            return int(self.hard_cap_effective * ratio)
        return self.policy.soft_cap_tokens

    def by_category(self) -> dict[str, int]:
        """Return a defensive copy of the per-category counters."""
        return dict(self._by_cat)

    def pressure_level(self) -> str:
        """Bucket the current usage ratio into a pressure level."""
        return self._level_for_used(self.total_non_exempt)

    def _level_for_used(self, used: int) -> str:
        """Pure mapping: token count → pressure level."""
        cap = self.hard_cap_effective
        if cap <= 0:
            return PRESSURE_OK
        ratio = used / cap
        if ratio >= 1.0:
            return PRESSURE_EXHAUSTED
        t = self._thresholds
        if ratio >= t.extreme:
            return PRESSURE_EXTREME
        if ratio >= t.critical:
            return PRESSURE_CRITICAL
        if ratio >= t.tight:
            return PRESSURE_TIGHT
        if ratio >= t.warn:
            return PRESSURE_WARN
        return PRESSURE_OK

    # ── Reservations ───────────────────────────────────────────────────

    async def reserve(
        self, category: str, est_tokens: int
    ) -> ReservationResult:
        """Ask "can I spend ``est_tokens`` on a ``category`` call?".

        On allow=True the caller proceeds and then calls ``commit``
        with the *actual* tokens (from the LLM response). On
        allow=False the caller reads ``suggested_action`` and degrades.

        Exempt categories (``rerank``, ``embedding``) are always
        allowed; their growth is constrained upstream by the rerank
        adapter's ``max_batches * batch_size`` cap. We still count
        them so the snapshot reports the real spend.
        """
        async with self._lock:
            pressure_before = self.pressure_level()

            # Exempt categories — always allowed, exit early.
            if category in self._exempt:
                return ReservationResult(
                    allow=True,
                    pressure_before=pressure_before,
                    pressure_after_est=pressure_before,
                    reason="exempt_category",
                )

            est_tokens = max(0, int(est_tokens))
            cat_cap = self.policy.per_category_caps.get(category)
            cat_after = self._by_cat.get(category, 0) + est_tokens
            est_total = self.total_non_exempt + est_tokens
            est_pressure = self._level_for_used(est_total)

            # Per-category cap (0 means "disabled for this category").
            if cat_cap is not None and cat_cap > 0 and cat_after > cat_cap:
                action = _action_for_category(category)
                return ReservationResult(
                    allow=False,
                    pressure_before=pressure_before,
                    pressure_after_est=est_pressure,
                    suggested_action=action,
                    reason=f"per_category_cap[{category}]={cat_cap}",
                )

            # Hard cap — even an extension-bumped cap can't be crossed.
            if est_total > self.hard_cap_effective:
                return ReservationResult(
                    allow=False,
                    pressure_before=pressure_before,
                    pressure_after_est=PRESSURE_EXHAUSTED,
                    suggested_action="abort_subquery",
                    reason="hard_cap_exceeded",
                )

            return ReservationResult(
                allow=True,
                pressure_before=pressure_before,
                pressure_after_est=est_pressure,
                reason="approved",
            )

    async def commit(self, category: str, actual_tokens: int) -> None:
        """Record actual token spend after the call returned.

        Commits NEVER raise — overshoot is permitted (the reservation
        only used an estimate; the LLM might return more tokens than
        we predicted) but the resulting pressure level is what drives
        the next reserve decision.

        Emits a ``research_budget`` SSE event ONLY when the pressure
        level transitions to a new bucket — every commit otherwise
        would flood the channel.
        """
        actual_tokens = max(0, int(actual_tokens))
        new_snapshot: BudgetSnapshot | None = None
        new_level: str | None = None
        async with self._lock:
            old_level = self.pressure_level()
            self._by_cat[category] = self._by_cat.get(category, 0) + actual_tokens
            new_level = self.pressure_level()
            if _level_rank(new_level) > _level_rank(self._max_pressure_reached):
                self._max_pressure_reached = new_level
            if new_level != old_level:
                new_snapshot = self.snapshot_obj_locked()

        if new_snapshot is not None and self._sse_emit is not None:
            try:
                await self._sse_emit(new_level, new_snapshot)
            except Exception:  # noqa: BLE001 — SSE failure must not stall pipeline
                logger.warning(
                    "budget SSE emit failed at level=%s — continuing", new_level
                )

    # ── Adaptive extension ─────────────────────────────────────────────

    async def request_extension(self, amount: int) -> int:
        """Bump the hard cap (and proportionally the soft cap) once per run.

        Returns the actual amount granted (capped by
        ``max_extension_fraction``); 0 if the call is denied (already
        used the allowance, or amount ≤ 0). Logs every grant + denial
        because this is something the user should be able to see in
        ``ResearchRun.token_usage.degradations_triggered``.
        """
        if amount <= 0:
            return 0
        async with self._lock:
            if self._extensions_used >= self.policy.max_adaptive_extensions:
                self._degradations_triggered.append(
                    f"extension_denied:already_used:{self._extensions_used}"
                )
                return 0
            max_allowed = int(
                self.policy.hard_cap_tokens * self.policy.max_extension_fraction
            )
            granted = min(amount, max_allowed)
            if granted <= 0:
                self._degradations_triggered.append("extension_denied:zero_after_cap")
                return 0
            self._extension_amount += granted
            self._extensions_used += 1
            self._degradations_triggered.append(
                f"extension_granted:{granted}:total_cap={self.hard_cap_effective}"
            )
            return granted

    # ── Snapshot ───────────────────────────────────────────────────────

    def snapshot_obj_locked(self) -> BudgetSnapshot:
        """Build a snapshot while the lock is already held.

        Use ``snapshot()`` (public) when calling from outside the
        tracker; this variant exists so ``commit`` can build a snapshot
        for the SSE-emit without re-entering the lock.
        """
        return BudgetSnapshot(
            by_category=dict(self._by_cat),
            total=sum(v for c, v in self._by_cat.items() if c not in self._exempt),
            total_exempt=sum(v for c, v in self._by_cat.items() if c in self._exempt),
            soft_cap=self.soft_cap_effective,
            hard_cap=self.hard_cap_effective,
            max_pressure_reached=self._max_pressure_reached,
            degradations_triggered=list(self._degradations_triggered),
            extensions_used=self._extensions_used,
            extension_amount=self._extension_amount,
        )

    async def snapshot_obj(self) -> BudgetSnapshot:
        async with self._lock:
            return self.snapshot_obj_locked()

    def snapshot(self) -> dict:
        """Return a JSON-serialisable snapshot for ``ResearchRun.token_usage``.

        Read-only — uses the current ``_by_cat`` without locking
        because dict reads are atomic in CPython and the snapshot is
        descriptive ("what was true a moment ago"), not authoritative.
        """
        s = self.snapshot_obj_locked()
        return {
            "by_category": s.by_category,
            "total": s.total,
            "total_exempt": s.total_exempt,
            "soft_cap": s.soft_cap,
            "hard_cap": s.hard_cap,
            "max_pressure_reached": s.max_pressure_reached,
            "degradations_triggered": list(s.degradations_triggered),
            "extensions_used": s.extensions_used,
            "extension_amount": s.extension_amount,
        }

    def record_degradation(self, label: str) -> None:
        """Append a free-text label to the audit trail.

        Called by the orchestrator when it applies a degradation that
        wasn't tracker-initiated (e.g. user-cancelled mid-run). Not
        locked because list-append is atomic in CPython.
        """
        self._degradations_triggered.append(label)


# ── Helper: budget-gated LLM call ──────────────────────────────────────────


def _action_for_category(category: str) -> str:
    """Suggest a degradation action for a denied reservation."""
    return _CATEGORY_ACTIONS.get(category, "skip")


async def _llm_call_with_budget(
    budget: BudgetTracker | None,
    category: str,
    est_in: int,
    est_out: int,
    call_fn: Callable[..., Awaitable[Any]],
    *args,
    **kwargs,
) -> Any:
    """Wrap a coroutine call with budget reserve + commit.

    Args:
        budget: tracker instance or ``None`` (None bypasses every
            check — useful for unit-tests and pre-P3c callers).
        category: which budget bucket this call belongs to.
        est_in / est_out: pre-call token-count estimate (input prompt
            + expected output). Sum is the reservation amount.
        call_fn / args / kwargs: the actual coroutine to await.

    Returns the call's result on success.

    Raises:
        BudgetDegradation: when the reservation is denied. The caller
            inspects ``e.suggested_action`` to know how to degrade.
    """
    if budget is None:
        return await call_fn(*args, **kwargs)

    est_total = max(0, int(est_in)) + max(0, int(est_out))
    reservation = await budget.reserve(category, est_total)
    if not reservation.allow:
        raise BudgetDegradation(
            suggested_action=reservation.suggested_action or "skip",
            reason=reservation.reason or "denied",
        )

    result = await call_fn(*args, **kwargs)
    actual = _extract_token_usage(result, fallback=est_total)
    await budget.commit(category, actual)
    return result


def _extract_token_usage(result: Any, *, fallback: int) -> int:
    """Best-effort pull of ``usage.total_tokens`` from the result.

    Recognises both dict-shaped ({"usage": {...}}) and object-shaped
    (.usage attribute) results — AI-Assist v2 stream consumers use the
    former, ``synapse_llm.call_json`` returns the latter. Anything we
    can't parse falls back to the estimate so a malformed response
    still counts toward the cap (defensive: better over-count than
    under-count when in doubt).
    """
    if result is None:
        return fallback
    usage: Any = None
    if isinstance(result, dict):
        usage = result.get("usage")
    else:
        usage = getattr(result, "usage", None)
    if isinstance(usage, dict):
        t = usage.get("total_tokens")
        if isinstance(t, (int, float)) and t >= 0:
            return int(t)
    return fallback
