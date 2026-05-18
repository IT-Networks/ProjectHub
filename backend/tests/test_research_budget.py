"""Tests for the token-budget tracker (P3c).

Three groups:

    * Unit — Policy / reservation / commit / pressure-level / extension /
      snapshot / _extract_token_usage on every supported shape.
    * Property — pressure ladder is monotonic, snapshot is permutation-
      free, exempt categories don't shift the level.
    * Concurrency — 50 coroutines hammering ``reserve`` + ``commit`` in
      parallel must not produce over-allocation or stale snapshots.

No external dependencies — the tracker is pure Python.
"""
from __future__ import annotations

import asyncio
import os
import secrets
import tempfile

import pytest

# conftest pins PROJECTHUB_DB_PATH; tracker doesn't touch the DB but
# the test module is grouped with the others.
_TEST_DB_PATH = os.path.join(
    tempfile.gettempdir(),
    f"projecthub_pytest_budget_{secrets.token_hex(4)}.db",
)
os.environ["PROJECTHUB_DB_PATH"] = _TEST_DB_PATH


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _policy(
    *, soft=200_000, hard=400_000, profile="normal",
    per_cat: dict[str, int] | None = None,
):
    from services.research_budget import TokenBudgetPolicy

    return TokenBudgetPolicy(
        soft_cap_tokens=soft,
        hard_cap_tokens=hard,
        per_category_caps=per_cat or {},
        threshold_profile=profile,
    )


# ── Policy + tracker construction ─────────────────────────────────────────


def test_policy_defaults_exempt_rerank_and_embedding():
    from services.research_budget import (
        DEFAULT_EXEMPT, TokenBudgetPolicy,
    )

    p = TokenBudgetPolicy(soft_cap_tokens=10, hard_cap_tokens=100)
    assert set(p.exempt_categories) == set(DEFAULT_EXEMPT)
    assert "rerank" in p.exempt_categories
    assert "embedding" in p.exempt_categories


def test_tracker_starts_at_zero_and_ok():
    from services.research_budget import BudgetTracker

    t = BudgetTracker(_policy())
    assert t.total_non_exempt == 0
    assert t.pressure_level() == "ok"
    assert t.snapshot()["total"] == 0
    assert t.snapshot()["max_pressure_reached"] == "ok"


# ── reserve happy path ────────────────────────────────────────────────────


def test_reserve_under_caps_is_allowed():
    from services.research_budget import BudgetTracker

    t = BudgetTracker(_policy(per_cat={"planning": 50_000}))
    res = _run(t.reserve("planning", 5_000))
    assert res.allow is True
    assert res.pressure_before == "ok"
    assert res.reason == "approved"


def test_reserve_per_category_cap_denies_with_action():
    from services.research_budget import BudgetTracker

    t = BudgetTracker(_policy(per_cat={"critic": 10_000}))
    res = _run(t.reserve("critic", 11_000))
    assert res.allow is False
    assert res.suggested_action == "skip_critic"
    assert "per_category_cap" in (res.reason or "")


def test_reserve_hard_cap_denies_with_abort():
    from services.research_budget import BudgetTracker

    t = BudgetTracker(_policy(hard=10_000))
    res = _run(t.reserve("planning", 11_000))
    assert res.allow is False
    assert res.suggested_action == "abort_subquery"
    assert res.reason == "hard_cap_exceeded"


def test_reserve_exempt_category_always_allowed_even_above_cap():
    """rerank + embedding bypass the total cap by design."""
    from services.research_budget import BudgetTracker

    t = BudgetTracker(_policy(hard=10_000))
    # Pre-fill total with a non-exempt category up to near cap.
    _run(t.commit("planning", 9_500))
    # rerank should still be allowed even though we're at 95% usage.
    res = _run(t.reserve("rerank", 50_000))
    assert res.allow is True
    assert res.reason == "exempt_category"


# ── commit + pressure level ───────────────────────────────────────────────


def test_commit_advances_pressure_through_levels():
    """Walk through every level by committing in chunks."""
    from services.research_budget import BudgetTracker

    t = BudgetTracker(_policy(soft=80, hard=100))
    # 0% → ok
    assert t.pressure_level() == "ok"
    _run(t.commit("planning", 50))   # 50% → still ok
    assert t.pressure_level() == "ok"
    _run(t.commit("planning", 30))   # 80% → warn
    assert t.pressure_level() == "warn"
    _run(t.commit("planning", 5))    # 85% → tight
    assert t.pressure_level() == "tight"
    _run(t.commit("planning", 5))    # 90% → critical
    assert t.pressure_level() == "critical"
    _run(t.commit("planning", 5))    # 95% → extreme
    assert t.pressure_level() == "extreme"
    _run(t.commit("planning", 5))    # 100% → exhausted
    assert t.pressure_level() == "exhausted"


def test_commit_never_raises_on_overshoot():
    """Reservation under cap but actual call returned more tokens —
    that's the LLM, not a bug; tracker just records it."""
    from services.research_budget import BudgetTracker

    t = BudgetTracker(_policy(hard=100))
    _run(t.commit("summary", 200))  # double the cap
    assert t.pressure_level() == "exhausted"
    assert t.total_non_exempt == 200


def test_max_pressure_reached_is_monotonic():
    """``max_pressure_reached`` only ever moves up the ladder."""
    from services.research_budget import BudgetTracker

    t = BudgetTracker(_policy(hard=100))
    _run(t.commit("planning", 90))  # 90% → critical
    assert t.snapshot()["max_pressure_reached"] == "critical"
    _run(t.commit("planning", 5))   # 95% → extreme (new peak)
    assert t.snapshot()["max_pressure_reached"] == "extreme"
    # A tiny commit that stays at the same level doesn't change the peak.
    _run(t.commit("planning", 1))   # 96% → still extreme
    assert t.snapshot()["max_pressure_reached"] == "extreme"


def test_tief_profile_thresholds_warn_earlier():
    """Tief profile warns at 70%, Normal at 80% — for the same usage
    Tief reports a higher pressure level."""
    from services.research_budget import BudgetTracker

    t_normal = BudgetTracker(_policy(hard=100, profile="normal"))
    t_tief = BudgetTracker(_policy(hard=100, profile="tief"))
    _run(t_normal.commit("planning", 75))
    _run(t_tief.commit("planning", 75))
    assert t_normal.pressure_level() == "ok"        # 75 < 80
    assert t_tief.pressure_level() == "warn"        # 75 ≥ 70


def test_exempt_categories_do_not_change_pressure():
    """rerank/embedding spending must NEVER push the pressure level up."""
    from services.research_budget import BudgetTracker

    t = BudgetTracker(_policy(hard=100))
    _run(t.commit("rerank", 10_000))     # huge but exempt
    _run(t.commit("embedding", 10_000))
    assert t.pressure_level() == "ok"
    assert t.total_non_exempt == 0
    assert t.total_exempt == 20_000


# ── SSE emit ──────────────────────────────────────────────────────────────


def test_sse_emit_fires_only_on_level_transition():
    """One emit per crossed boundary, not per commit."""
    from services.research_budget import BudgetTracker

    events: list[str] = []

    async def _emit(level: str, snap):
        events.append(level)

    t = BudgetTracker(_policy(hard=100), sse_emit=_emit)
    _run(t.commit("planning", 50))   # ok → ok       no emit
    _run(t.commit("planning", 30))   # ok → warn     emit
    _run(t.commit("planning", 1))    # warn → warn   no emit
    _run(t.commit("planning", 10))   # warn → critical (skipped tight)
    _run(t.commit("planning", 10))   # critical → exhausted (skipped extreme)
    assert events == ["warn", "critical", "exhausted"]


def test_sse_emit_failure_does_not_break_commit():
    """A broken SSE subscriber must not stall the pipeline."""
    from services.research_budget import BudgetTracker

    async def _broken(level, snap):
        raise RuntimeError("subscriber gone")

    t = BudgetTracker(_policy(hard=100), sse_emit=_broken)
    # If commit didn't swallow the exception, this would raise.
    _run(t.commit("planning", 80))
    assert t.pressure_level() == "warn"


# ── Adaptive extension ────────────────────────────────────────────────────


def test_extension_grants_up_to_max_fraction():
    """Default 30% of hard cap; larger requests get clamped to that."""
    from services.research_budget import BudgetTracker

    t = BudgetTracker(_policy(hard=1000))
    granted = _run(t.request_extension(1_000_000))  # way too much
    assert granted == 300  # 30% of 1000
    assert t.hard_cap_effective == 1300


def test_extension_second_request_denied():
    """Only one extension per run."""
    from services.research_budget import BudgetTracker

    t = BudgetTracker(_policy(hard=1000))
    _run(t.request_extension(100))
    second = _run(t.request_extension(100))
    assert second == 0
    snap = t.snapshot()
    assert any("extension_denied" in d for d in snap["degradations_triggered"])


def test_extension_with_zero_amount_is_no_op():
    from services.research_budget import BudgetTracker

    t = BudgetTracker(_policy(hard=1000))
    assert _run(t.request_extension(0)) == 0
    assert _run(t.request_extension(-5)) == 0
    assert t._extension_amount == 0


def test_extension_audit_trail_in_snapshot():
    from services.research_budget import BudgetTracker

    t = BudgetTracker(_policy(hard=1000))
    _run(t.request_extension(100))
    snap = t.snapshot()
    assert snap["extensions_used"] == 1
    assert snap["extension_amount"] == 100
    assert any("extension_granted:100" in d for d in snap["degradations_triggered"])


# ── _llm_call_with_budget ─────────────────────────────────────────────────


def test_llm_call_with_budget_happy_path():
    from services.research_budget import BudgetTracker, _llm_call_with_budget

    t = BudgetTracker(_policy(hard=100_000))

    async def _fake_call():
        return {"usage": {"total_tokens": 2_500}}

    result = _run(_llm_call_with_budget(t, "planning", 2_000, 500, _fake_call))
    assert result == {"usage": {"total_tokens": 2_500}}
    # Actual tokens from the response committed, not the estimate.
    assert t.by_category()["planning"] == 2_500


def test_llm_call_with_budget_denial_raises_degradation():
    from services.research_budget import (
        BudgetDegradation,
        BudgetTracker,
        _llm_call_with_budget,
    )

    t = BudgetTracker(_policy(hard=100, per_cat={"critic": 50}))

    async def _fake_call():
        raise AssertionError("must not be called")

    with pytest.raises(BudgetDegradation) as ei:
        _run(_llm_call_with_budget(t, "critic", 60, 10, _fake_call))
    assert ei.value.suggested_action == "skip_critic"


def test_llm_call_with_budget_no_tracker_bypasses():
    """budget=None → call runs, no tracking."""
    from services.research_budget import _llm_call_with_budget

    async def _fake_call():
        return {"usage": {"total_tokens": 999}}

    result = _run(_llm_call_with_budget(None, "planning", 100, 10, _fake_call))
    assert result["usage"]["total_tokens"] == 999


def test_llm_call_with_budget_falls_back_to_estimate_when_no_usage():
    """If the response lacks .usage.total_tokens, use the estimate."""
    from services.research_budget import BudgetTracker, _llm_call_with_budget

    t = BudgetTracker(_policy(hard=100_000))

    async def _fake_call():
        return "raw string response, no usage"

    _run(_llm_call_with_budget(t, "planning", 1_500, 500, _fake_call))
    assert t.by_category()["planning"] == 2_000  # est_in + est_out


# ── _extract_token_usage parametrised ─────────────────────────────────────


@pytest.mark.parametrize("result,expected", [
    (None, 100),
    ({"usage": {"total_tokens": 42}}, 42),
    ({"usage": {"total_tokens": 0}}, 0),
    ({"usage": {}}, 100),
    ({"usage": "junk"}, 100),
    ({"no_usage": "key"}, 100),
    ("plain string", 100),
])
def test_extract_token_usage_recognises_common_shapes(result, expected):
    from services.research_budget import _extract_token_usage

    assert _extract_token_usage(result, fallback=100) == expected


def test_extract_token_usage_object_attribute():
    """call_json returns LLMResult with .usage dict — object access path."""
    from services.research_budget import _extract_token_usage

    class _R:
        usage = {"total_tokens": 77}

    assert _extract_token_usage(_R(), fallback=100) == 77


# ── Snapshot ──────────────────────────────────────────────────────────────


def test_snapshot_contains_required_keys_and_types():
    from services.research_budget import BudgetTracker

    t = BudgetTracker(_policy())
    _run(t.commit("planning", 1234))
    _run(t.commit("rerank", 4321))
    snap = t.snapshot()
    assert set(snap.keys()) == {
        "by_category", "total", "total_exempt", "soft_cap", "hard_cap",
        "max_pressure_reached", "degradations_triggered",
        "extensions_used", "extension_amount",
    }
    assert snap["by_category"]["planning"] == 1234
    assert snap["by_category"]["rerank"] == 4321
    assert snap["total"] == 1234  # rerank excluded
    assert snap["total_exempt"] == 4321
    # JSON-friendly: every value is a primitive / list / dict.
    import json
    json.dumps(snap)


# ── Concurrency ───────────────────────────────────────────────────────────


def test_concurrent_reserve_and_commit_no_over_allocation():
    """50 coroutines each reserving+committing 100 tokens against a
    cap of 10_000 must end at exactly 5_000 used (no race conditions).
    """
    from services.research_budget import BudgetTracker

    t = BudgetTracker(_policy(hard=10_000))

    async def _worker():
        res = await t.reserve("planning", 100)
        assert res.allow is True
        await t.commit("planning", 100)

    async def _go():
        await asyncio.gather(*[_worker() for _ in range(50)])

    _run(_go())
    assert t.by_category()["planning"] == 5_000
    assert t.total_non_exempt == 5_000
    assert t.pressure_level() == "ok"


def test_concurrent_reserve_respects_hard_cap():
    """100 coroutines fighting for a cap of 50 reservations — only
    50 should win their reservation."""
    from services.research_budget import BudgetTracker

    t = BudgetTracker(_policy(hard=5_000))

    grants = 0
    denials = 0

    async def _worker():
        nonlocal grants, denials
        res = await t.reserve("planning", 100)
        if res.allow:
            grants += 1
            await t.commit("planning", 100)
        else:
            denials += 1

    async def _go():
        await asyncio.gather(*[_worker() for _ in range(100)])

    _run(_go())
    # Note: reserve doesn't subtract from the budget — it just checks
    # if the *estimated* commit would fit. So early-bird reservers
    # see "still room" until commits add up. Result: up to 50 commits
    # actually land; later reservers see a fuller bucket and start
    # denying. The exact count varies; assert sensible bounds.
    assert grants <= 50
    assert grants + denials == 100
    assert t.total_non_exempt <= 5_000


def test_concurrent_commits_match_individual_sum():
    """Even under concurrency, ``sum(commits) == final by_category``."""
    from services.research_budget import BudgetTracker

    t = BudgetTracker(_policy(hard=1_000_000))
    amounts = [13, 17, 19, 23, 29, 31, 37, 41, 43, 47] * 10  # 100 distinct commits

    async def _commit(amt):
        await t.commit("planning", amt)

    async def _go():
        await asyncio.gather(*[_commit(a) for a in amounts])

    _run(_go())
    assert t.by_category()["planning"] == sum(amounts)


# ── Audit-trail helper ────────────────────────────────────────────────────


def test_record_degradation_appears_in_snapshot():
    from services.research_budget import BudgetTracker

    t = BudgetTracker(_policy())
    t.record_degradation("user_cancelled_mid_hop_1")
    snap = t.snapshot()
    assert "user_cancelled_mid_hop_1" in snap["degradations_triggered"]
