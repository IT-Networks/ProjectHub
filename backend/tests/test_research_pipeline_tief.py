"""End-to-end pipeline tests for Tief-Mode with lateral expansion (P7).

Exercises the Hop-Loop:
    initial PLAN (mocked) → SEARCH → LATERAL hop 1 → SEARCH → finalise

Mocks the planner + the lateral LLM stages so the test is deterministic
without an LLM proxy. Real components in play: Pipeline orchestrator,
DB writes, BudgetTracker, SSE hub, expand_hop wiring.

Covers:
    * Tief run with one lateral hop produces lateral sub-queries
      with hop=1, is_lateral=True, parent_finding_ids linked
    * Lateral hop emits research_lateral_planned SSE event
    * Budget-pressure 'critical' skips lateral hop (audit-trail label)
    * Normal-mode does NOT expand laterally (max_lateral_hops=0)
"""
from __future__ import annotations

import asyncio
import os
import secrets
import tempfile

import pytest

_TEST_DB_PATH = os.path.join(
    tempfile.gettempdir(),
    f"projecthub_pytest_tief_{secrets.token_hex(4)}.db",
)
os.environ["PROJECTHUB_DB_PATH"] = _TEST_DB_PATH


@pytest.fixture(scope="module")
def initdb():
    try:
        if os.path.exists(_TEST_DB_PATH):
            os.unlink(_TEST_DB_PATH)
    except OSError:
        pass
    import models  # noqa: F401
    from database import init_db

    asyncio.get_event_loop().run_until_complete(init_db())
    yield
    try:
        if os.path.exists(_TEST_DB_PATH):
            os.unlink(_TEST_DB_PATH)
    except OSError:
        pass


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── Fixtures ───────────────────────────────────────────────────────────────


def _new_project_id() -> str:
    from database import async_session
    from models.project import Project

    async def _go():
        async with async_session() as db:
            pid = secrets.token_hex(8)
            db.add(Project(id=pid, name="TiefTest"))
            await db.commit()
            return pid

    return _run(_go())


def _new_run(project_id: str, *, depth: str = "tief") -> str:
    from database import async_session
    from models.research import ResearchRun

    async def _go():
        async with async_session() as db:
            rid = secrets.token_hex(8)
            db.add(ResearchRun(
                id=rid, project_id=project_id, topic="PKCE",
                depth=depth, mode="auto", status="running", phase="planning",
            ))
            await db.commit()
            return rid

    return _run(_go())


def _enable_providers(project_id: str, providers: list[str]) -> None:
    from database import async_session
    from models.research import ProjectResearchSettings

    async def _go():
        async with async_session() as db:
            cfg = ProjectResearchSettings(
                project_id=project_id, default_depth="tief", routing_hints="",
            )
            cfg.enabled_providers_list = providers
            cfg.provider_settings_dict = {}
            db.add(cfg)
            await db.commit()

    _run(_go())


def _patch_provider_high_conf(monkeypatch, key: str, n_findings: int = 3):
    """Provider that emits findings with confidence ≥ 0.6 so they qualify
    for lateral expansion (which selects rows with score ≥ 0.6)."""
    from services.research_providers import PROVIDERS
    from services.research_providers.base import (
        Finding, ProviderHealth, SearchProgress,
    )

    class _HighConfProvider:
        def __init__(self):
            self.key = key
            self.description = "fake"
            self.typical_latency = "fast"
            self.side_effect = "read"
            self.default_enabled = True

        async def health(self):
            return ProviderHealth(ok=True, detail="connected", last_checked_at="now")

        async def stream(
            self, query, provider_settings, cancel, *, project_id,
        ):
            for i in range(n_findings):
                if cancel.is_set():
                    yield SearchProgress(kind="done", status_text="cancelled")
                    return
                yield SearchProgress(
                    kind="finding",
                    finding=Finding(
                        provider_key=self.key,
                        source_ref=f"{self.key}:{secrets.token_hex(4)}-{i}",
                        title=f"{self.key} finding {i} about service x and keycloak",
                        snippet=f"snippet mentioning {query} and keycloak",
                        full_content=f"long content for {query} entry {i} keycloak refresh",
                        score=0.8,  # ≥ 0.6 → qualifies for lateral expansion
                    ),
                )
            yield SearchProgress(kind="done", status_text="ok")

    monkeypatch.setitem(PROVIDERS, key, _HighConfProvider())


def _patch_planner(monkeypatch, sub_queries: list[dict]):
    from services.research_planner import PlanResult, SubQueryPlan
    import services.research_pipeline as pipe

    async def fake_plan(topic, **kw):
        return PlanResult(
            sub_queries=[SubQueryPlan(**sq) for sq in sub_queries],
            raw_response={"sub_queries": sub_queries},
        )

    monkeypatch.setattr(pipe, "plan_subqueries", fake_plan)


def _patch_lateral_stages(monkeypatch, *, ranked_relevance: float = 0.9):
    """Stub the three lateral LLM stages with deterministic responses."""
    import services.research_lateral as rl

    call_idx = {"n": 0}

    async def fake(*a, **k):
        i = call_idx["n"]; call_idx["n"] += 1
        # Cycle through: extract, extract, ..., rank, plan, plan, ...
        # For tests we always emit at least one entity per finding.
        prompt = a[0] if a else ""
        if "Extrahiere" in prompt:
            class R:
                parsed = [{"name": f"entity-{call_idx['n']}", "confidence": 0.9}]
                ok = True
                usage = {}
            return R()
        if "Bewerte" in prompt:  # rank prompt
            class R2:
                # Match however many entities the rank prompt contains.
                parsed = [
                    {"id": 1, "relevance": ranked_relevance},
                    {"id": 2, "relevance": ranked_relevance * 0.9},
                    {"id": 3, "relevance": ranked_relevance * 0.8},
                ]
                ok = True
                usage = {}
            return R2()
        # plan prompt
        class P:
            parsed = {
                "question": "Wie wirkt sich die Entität auf das Topic aus?",
                "providers": ["fake_a"],
                "rationale": "lateral",
            }
            ok = True
            usage = {}
        return P()

    monkeypatch.setattr(rl, "call_json", fake)


def _patch_validation_supported(monkeypatch):
    """Patch validation's call_json so Tier-B always returns 'supported'.

    Required for Tief-Mode tests since the validation phase now runs
    for real (P8 replaced the stub). Without this every test would hit
    the unreachable AI-Assist endpoint.
    """
    import services.research_validation as rv

    async def fake(*a, **k):
        class R:
            parsed = {"relation": "supported", "score": 0.9, "reason": "ok"}
            ok = True
            usage = {"total_tokens": 500}
        return R()

    monkeypatch.setattr(rv, "call_json", fake)


# ── Happy path: tief run produces lateral sub-queries ─────────────────────


def test_tief_run_produces_lateral_sub_queries(monkeypatch, initdb):
    from services.research_pipeline import run_research
    from models.research import ResearchSubQuery
    from database import async_session
    from sqlalchemy import select

    pid = _new_project_id()
    _enable_providers(pid, ["fake_a"])
    rid = _new_run(pid, depth="tief")

    _patch_provider_high_conf(monkeypatch, "fake_a", n_findings=3)
    _patch_planner(monkeypatch, [
        {"id": secrets.token_hex(8), "question": "What about PKCE?",
         "providers": ["fake_a"], "rationale": "initial", "priority": 1},
    ])
    _patch_lateral_stages(monkeypatch, ranked_relevance=0.9)
    _patch_validation_supported(monkeypatch)

    _run(run_research(pid, rid))

    async def _read():
        async with async_session() as db:
            sqs = (await db.execute(
                select(ResearchSubQuery).where(ResearchSubQuery.run_id == rid)
            )).scalars().all()
        return sqs

    sqs = _run(_read())
    hops = {sq.hop for sq in sqs}
    lateral_sqs = [sq for sq in sqs if sq.is_lateral]
    assert 0 in hops  # initial
    assert 1 in hops  # lateral hop 1
    assert lateral_sqs, "expected ≥1 lateral sub-query"
    for lsq in lateral_sqs:
        assert lsq.hop >= 1
        assert lsq.is_lateral is True
        assert lsq.entity_focus  # set from expand_hop
        assert lsq.relevance_score is not None
        assert lsq.parent_finding_ids_list  # lineage wired up


# ── Normal mode does NOT expand laterally ─────────────────────────────────


def test_normal_run_does_not_expand_laterally(monkeypatch, initdb):
    from services.research_pipeline import run_research
    from models.research import ResearchSubQuery
    from database import async_session
    from sqlalchemy import select

    pid = _new_project_id()
    _enable_providers(pid, ["fake_a"])
    rid = _new_run(pid, depth="normal")

    _patch_provider_high_conf(monkeypatch, "fake_a")
    _patch_planner(monkeypatch, [
        {"id": secrets.token_hex(8), "question": "q",
         "providers": ["fake_a"], "rationale": "r", "priority": 1},
    ])
    # Lateral stages must NOT fire — sentinel raise.
    import services.research_lateral as rl

    async def must_not_call(*a, **k):
        raise AssertionError("Normal-mode triggered lateral expansion!")

    monkeypatch.setattr(rl, "call_json", must_not_call)
    # Validation still runs (Normal-Mode validates too) — stub it.
    _patch_validation_supported(monkeypatch)

    _run(run_research(pid, rid))

    async def _read():
        async with async_session() as db:
            sqs = (await db.execute(
                select(ResearchSubQuery).where(ResearchSubQuery.run_id == rid)
            )).scalars().all()
        return sqs

    sqs = _run(_read())
    assert all(sq.hop == 0 for sq in sqs)
    assert not any(sq.is_lateral for sq in sqs)


# ── Lateral SSE event emitted ─────────────────────────────────────────────


def test_tief_run_emits_lateral_planned_sse(monkeypatch, initdb):
    from services.research_pipeline import run_research
    from services.sse_hub import sse_hub

    pid = _new_project_id()
    _enable_providers(pid, ["fake_a"])
    rid = _new_run(pid, depth="tief")

    _patch_provider_high_conf(monkeypatch, "fake_a", n_findings=2)
    _patch_planner(monkeypatch, [
        {"id": secrets.token_hex(8), "question": "q",
         "providers": ["fake_a"], "rationale": "r", "priority": 1},
    ])
    _patch_lateral_stages(monkeypatch, ranked_relevance=0.9)
    _patch_validation_supported(monkeypatch)

    received: list[dict] = []

    async def _collect_and_run():
        queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        sse_hub._subscribers.append(queue)
        try:
            run_task = asyncio.create_task(run_research(pid, rid))
            while not run_task.done() or not queue.empty():
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.5)
                    received.append(event)
                except asyncio.TimeoutError:
                    if run_task.done():
                        break
            await run_task
        finally:
            if queue in sse_hub._subscribers:
                sse_hub._subscribers.remove(queue)

    _run(_collect_and_run())

    lateral_events = [e for e in received if e["type"] == "research_lateral_planned"]
    assert lateral_events, "no research_lateral_planned event emitted"
    event = lateral_events[0]
    assert event["data"]["hop"] == 1
    assert "entities" in event["data"]
    assert "new_sub_queries" in event["data"]


# ── Budget pressure skips lateral hop ─────────────────────────────────────


def test_tief_run_skips_lateral_at_critical_pressure(monkeypatch, initdb):
    """When the BudgetTracker is critical/extreme, lateral hops are
    skipped with an audit-trail label in token_usage.degradations_triggered.
    """
    from services.research_pipeline import run_research
    from models.research import ResearchRun, ResearchSubQuery
    from database import async_session
    from sqlalchemy import select

    pid = _new_project_id()
    _enable_providers(pid, ["fake_a"])
    rid = _new_run(pid, depth="tief")

    _patch_provider_high_conf(monkeypatch, "fake_a")
    _patch_planner(monkeypatch, [
        {"id": secrets.token_hex(8), "question": "q",
         "providers": ["fake_a"], "rationale": "r", "priority": 1},
    ])
    # Lateral LLM stages must NOT fire — budget pressure blocks the hop.
    import services.research_lateral as rl

    async def must_not_call(*a, **k):
        raise AssertionError("budget pressure should have blocked the hop")

    monkeypatch.setattr(rl, "call_json", must_not_call)
    # Validation phase still needs a mock (it runs even at critical pressure
    # until the per-finding budget guard fires).
    _patch_validation_supported(monkeypatch)

    # Monkeypatch the tracker's pressure_level to report critical so the
    # pipeline's budget guard short-circuits. Patching at class level so
    # the tracker the pipeline creates picks it up.
    from services.research_budget import BudgetTracker

    real_pressure = BudgetTracker.pressure_level

    def fake_pressure(self):
        return "critical"

    monkeypatch.setattr(BudgetTracker, "pressure_level", fake_pressure)

    try:
        _run(run_research(pid, rid))
    finally:
        monkeypatch.setattr(BudgetTracker, "pressure_level", real_pressure)

    async def _read():
        async with async_session() as db:
            run = (await db.execute(
                select(ResearchRun).where(ResearchRun.id == rid)
            )).scalar_one()
            sqs = (await db.execute(
                select(ResearchSubQuery).where(ResearchSubQuery.run_id == rid)
            )).scalars().all()
        return run, sqs

    run, sqs = _run(_read())
    # No lateral sub-queries were spawned.
    assert not any(sq.is_lateral for sq in sqs)
    # Audit trail records the skip.
    usage = run.token_usage_dict
    assert any(
        "lateral_hop" in label
        for label in usage.get("degradations_triggered", [])
    )
