"""End-to-end pipeline tests for Normal-Mode (P6).

Drives ``run_research`` through a throwaway SQLite with the full
planner + provider stack. The planner LLM is monkey-patched to return
a deterministic plan; one provider in the PROVIDERS registry is
patched to a deterministic fake so each sub-query emits exactly two
findings; everything else (SSE hub, BudgetTracker, finalisation,
KnowledgeItem persist) runs for real.

Covers:
    * Happy-path Normal run → status=ok, findings persisted, SSE order
    * Cancel mid-stream → status=cancelled, persisted findings survive
    * Planner fallback (LLM returns junk) → pipeline still runs with
      per-provider fallback plan
    * Project with no enabled providers → status=error early
    * Budget snapshot lands in ResearchRun.token_usage at finish
"""
from __future__ import annotations

import asyncio
import os
import secrets
import tempfile
from typing import AsyncIterator

import pytest

_TEST_DB_PATH = os.path.join(
    tempfile.gettempdir(),
    f"projecthub_pytest_pipeline_{secrets.token_hex(4)}.db",
)
os.environ["PROJECTHUB_DB_PATH"] = _TEST_DB_PATH


@pytest.fixture(scope="module")
def initdb():
    try:
        if os.path.exists(_TEST_DB_PATH):
            os.unlink(_TEST_DB_PATH)
    except OSError:
        pass
    import models  # noqa: F401 — register every Base subclass
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


# ── Fixtures / fakes ───────────────────────────────────────────────────────


def _new_project_id() -> str:
    from database import async_session
    from models.project import Project

    async def _go():
        async with async_session() as db:
            pid = secrets.token_hex(8)
            db.add(Project(id=pid, name=f"PipelineTest-{pid[:6]}"))
            await db.commit()
            return pid

    return _run(_go())


def _new_run(project_id: str, *, topic: str = "PKCE", depth: str = "normal") -> str:
    from database import async_session
    from models.research import ResearchRun

    async def _go():
        async with async_session() as db:
            rid = secrets.token_hex(8)
            db.add(ResearchRun(
                id=rid, project_id=project_id, topic=topic,
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
                project_id=project_id, default_depth="normal", routing_hints="",
            )
            cfg.enabled_providers_list = providers
            cfg.provider_settings_dict = {}
            db.add(cfg)
            await db.commit()

    _run(_go())


# A tiny provider that yields N deterministic findings regardless of query.
def _make_fake_provider(key: str, n_findings: int = 2):
    from services.research_providers.base import (
        Finding,
        ProviderHealth,
        SearchProgress,
    )

    class _FakeProvider:
        def __init__(self):
            self.key = key
            self.description = "fake"
            self.typical_latency = "fast"
            self.side_effect = "read"
            self.default_enabled = True

        async def health(self):
            return ProviderHealth(
                ok=True, detail="connected", last_checked_at="now"
            )

        async def stream(
            self, query: str, provider_settings: dict,
            cancel: asyncio.Event, *, project_id: str,
        ) -> AsyncIterator[SearchProgress]:
            if cancel.is_set():
                yield SearchProgress(kind="done", status_text="cancelled")
                return
            yield SearchProgress(kind="status", status_text=f"{self.key} starts")
            for i in range(n_findings):
                if cancel.is_set():
                    yield SearchProgress(kind="done", status_text="cancelled")
                    return
                yield SearchProgress(
                    kind="finding",
                    finding=Finding(
                        provider_key=self.key,
                        source_ref=f"{self.key}:f-{i}-{secrets.token_hex(4)}",
                        title=f"{self.key} finding {i}",
                        snippet=f"snippet for {query[:30]} #{i}",
                        full_content=f"long body about {query} entry {i}",
                        score=0.8 - i * 0.1,
                    ),
                )
            yield SearchProgress(kind="done", status_text="ok")

    return _FakeProvider()


def _patch_provider(monkeypatch, key: str, n_findings: int):
    """Replace a single key in the global PROVIDERS dict with a fake."""
    from services.research_providers import PROVIDERS

    fake = _make_fake_provider(key, n_findings)
    monkeypatch.setitem(PROVIDERS, key, fake)


def _patch_planner_with_concrete_plan(monkeypatch, sub_queries: list[dict]):
    """Replace ``plan_subqueries`` to return a deterministic plan."""
    from services.research_planner import PlanResult, SubQueryPlan
    import services.research_pipeline as pipe

    async def fake_plan(topic, **kw):
        return PlanResult(
            sub_queries=[SubQueryPlan(**sq) for sq in sub_queries],
            raw_response={"sub_queries": sub_queries},
        )

    monkeypatch.setattr(pipe, "plan_subqueries", fake_plan)


def _patch_validation_supported(monkeypatch):
    """Patch synapse_llm.call_json (used by research_validation) so
    every finding's Tier-B grounding returns ``relation="supported"``.

    Without this the pipeline would hit the real AI-Assist for each
    finding's validation, which doesn't exist in tests.
    """
    import services.research_validation as rv

    async def fake_call_json(prompt, model=None, session_prefix=None):
        class R:
            parsed = {"relation": "supported", "score": 0.9, "reason": "ok"}
            ok = True
            usage = {"total_tokens": 500}
        return R()

    monkeypatch.setattr(rv, "call_json", fake_call_json)


async def _collect_sse_until_complete(
    filter_types: set[str], timeout: float = 5.0,
):
    """Subscribe to sse_hub and grab events of interest until we see
    a ``research_complete`` (which always terminates the stream)."""
    from services.sse_hub import sse_hub

    received: list[dict] = []
    queue: asyncio.Queue = asyncio.Queue(maxsize=200)
    sse_hub._subscribers.append(queue)
    try:
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            try:
                event = await asyncio.wait_for(queue.get(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            if event["type"] in filter_types:
                received.append(event)
            if event["type"] == "research_complete":
                break
    finally:
        if queue in sse_hub._subscribers:
            sse_hub._subscribers.remove(queue)
    return received


# ── Happy path ─────────────────────────────────────────────────────────────


def test_normal_run_persists_findings_and_finalises_ok(monkeypatch, initdb):
    """Two sub-queries × one provider × two findings → 4 findings →
    4 KnowledgeItems written. Run row finalised with status=ok."""
    from services.research_pipeline import run_research
    from models.research import ResearchFinding, ResearchRun, ResearchSubQuery
    from database import async_session
    from sqlalchemy import select

    pid = _new_project_id()
    _enable_providers(pid, ["fake_a", "fake_b"])
    rid = _new_run(pid, topic="OAuth2 PKCE")

    _patch_provider(monkeypatch, "fake_a", n_findings=2)
    _patch_provider(monkeypatch, "fake_b", n_findings=2)
    _patch_planner_with_concrete_plan(monkeypatch, [
        {"id": secrets.token_hex(8), "question": "What is X?",
         "providers": ["fake_a"], "rationale": "test", "priority": 1},
        {"id": secrets.token_hex(8), "question": "How is X built?",
         "providers": ["fake_b"], "rationale": "test", "priority": 1},
    ])
    _patch_validation_supported(monkeypatch)

    _run(run_research(pid, rid))

    async def _read():
        async with async_session() as db:
            run = (await db.execute(
                select(ResearchRun).where(ResearchRun.id == rid)
            )).scalar_one()
            sqs = (await db.execute(
                select(ResearchSubQuery).where(ResearchSubQuery.run_id == rid)
            )).scalars().all()
            findings = (await db.execute(
                select(ResearchFinding).where(ResearchFinding.run_id == rid)
            )).scalars().all()
        return run, sqs, findings

    run, sqs, findings = _run(_read())
    assert run.status == "ok", f"expected ok, got {run.status} (err: {run.error_summary})"
    assert run.phase == "done"
    assert run.finding_count == 4
    assert run.persisted_count == 4
    assert run.validated_count == 4  # stub validates all candidates → grounded
    assert run.finished_at is not None
    assert len(sqs) == 2
    assert {sq.status for sq in sqs} == {"done"}
    assert len(findings) == 4
    statuses = {f.status for f in findings}
    assert statuses == {"persisted"}
    # Every finding has a KnowledgeItem link.
    assert all(f.knowledge_item_id for f in findings)


def test_normal_run_writes_budget_snapshot_to_token_usage(monkeypatch, initdb):
    """ResearchRun.token_usage_dict has the BudgetTracker snapshot shape."""
    from services.research_pipeline import run_research
    from models.research import ResearchRun
    from database import async_session
    from sqlalchemy import select

    pid = _new_project_id()
    _enable_providers(pid, ["fake_a"])
    rid = _new_run(pid)
    _patch_provider(monkeypatch, "fake_a", n_findings=1)
    _patch_planner_with_concrete_plan(monkeypatch, [
        {"id": secrets.token_hex(8), "question": "q", "providers": ["fake_a"],
         "rationale": "r", "priority": 1},
    ])
    _patch_validation_supported(monkeypatch)

    _run(run_research(pid, rid))

    async def _read():
        async with async_session() as db:
            run = (await db.execute(
                select(ResearchRun).where(ResearchRun.id == rid)
            )).scalar_one()
            return run.token_usage_dict

    snap = _run(_read())
    assert set(snap.keys()) >= {
        "by_category", "total", "soft_cap", "hard_cap",
        "max_pressure_reached", "degradations_triggered",
    }
    assert isinstance(snap["by_category"], dict)


# ── No enabled providers ──────────────────────────────────────────────────


def test_run_without_enabled_providers_errors_early(monkeypatch, initdb):
    """A project with empty enabled_providers → error before any LLM call."""
    from services.research_pipeline import run_research
    from models.research import ResearchRun
    from database import async_session
    from sqlalchemy import select

    pid = _new_project_id()
    _enable_providers(pid, [])  # explicitly empty
    rid = _new_run(pid)

    # Sentinel — must NOT fire: pipeline aborts before the planner.
    planner_called = {"hit": False}
    import services.research_pipeline as pipe

    async def must_not_call(*a, **k):
        planner_called["hit"] = True

    monkeypatch.setattr(pipe, "plan_subqueries", must_not_call)

    _run(run_research(pid, rid))
    assert planner_called["hit"] is False

    async def _read():
        async with async_session() as db:
            return (await db.execute(
                select(ResearchRun).where(ResearchRun.id == rid)
            )).scalar_one()

    run = _run(_read())
    assert run.status == "error"
    assert run.error_summary == "no_enabled_providers"


# ── Cancel mid-stream ─────────────────────────────────────────────────────


def test_run_cancel_mid_stream_finalises_as_cancelled(monkeypatch, initdb):
    """Cancel-event set before run start → status=cancelled, no persists."""
    from services.research_pipeline import run_research
    from models.research import ResearchFinding, ResearchRun
    from database import async_session
    from sqlalchemy import select

    pid = _new_project_id()
    _enable_providers(pid, ["fake_a"])
    rid = _new_run(pid)
    _patch_provider(monkeypatch, "fake_a", n_findings=3)
    _patch_planner_with_concrete_plan(monkeypatch, [
        {"id": secrets.token_hex(8), "question": "q", "providers": ["fake_a"],
         "rationale": "r", "priority": 1},
    ])

    cancel = asyncio.Event()
    cancel.set()
    _run(run_research(pid, rid, cancel=cancel))

    async def _read():
        async with async_session() as db:
            run = (await db.execute(
                select(ResearchRun).where(ResearchRun.id == rid)
            )).scalar_one()
            findings = (await db.execute(
                select(ResearchFinding).where(ResearchFinding.run_id == rid)
            )).scalars().all()
        return run, findings

    run, findings = _run(_read())
    assert run.status == "cancelled"
    # No findings persisted (cancel fired before provider stream emitted).
    assert all(f.status != "persisted" for f in findings)


# ── Planner fallback ──────────────────────────────────────────────────────


def test_planner_failure_uses_fallback_plan(monkeypatch, initdb):
    """When call_json returns garbage, the planner falls back to one
    sub-query per enabled provider — the pipeline still runs."""
    from services.research_pipeline import run_research
    from models.research import ResearchFinding, ResearchRun, ResearchSubQuery
    from database import async_session
    from sqlalchemy import select
    import services.research_planner as planner

    pid = _new_project_id()
    _enable_providers(pid, ["fake_a"])
    rid = _new_run(pid, topic="some topic")
    _patch_provider(monkeypatch, "fake_a", n_findings=2)

    # Make call_json return malformed LLMResult so the planner falls
    # back to its per-provider plan.
    async def fake_call_json(prompt, model=None, session_prefix=None):
        class R:
            parsed = "not a dict"
            ok = True
            usage = {"total_tokens": 100}
        return R()

    monkeypatch.setattr(planner, "call_json", fake_call_json)
    _patch_validation_supported(monkeypatch)

    _run(run_research(pid, rid))

    async def _read():
        async with async_session() as db:
            run = (await db.execute(
                select(ResearchRun).where(ResearchRun.id == rid)
            )).scalar_one()
            findings = (await db.execute(
                select(ResearchFinding).where(ResearchFinding.run_id == rid)
            )).scalars().all()
        return run, findings

    run, findings = _run(_read())
    assert run.status == "ok"
    # Fallback question becomes the topic itself (no question text).
    assert run.finding_count == 2  # 1 provider × 2 findings
    assert len(findings) == 2
    assert all(f.status == "persisted" for f in findings)


# ── SSE event sequence ────────────────────────────────────────────────────


def test_sse_emits_expected_event_sequence(monkeypatch, initdb):
    """Run emits progress + subquery_started + finding + complete in order."""
    from services.research_pipeline import run_research

    pid = _new_project_id()
    _enable_providers(pid, ["fake_a"])
    rid = _new_run(pid)
    _patch_provider(monkeypatch, "fake_a", n_findings=1)
    _patch_planner_with_concrete_plan(monkeypatch, [
        {"id": secrets.token_hex(8), "question": "q", "providers": ["fake_a"],
         "rationale": "r", "priority": 1},
    ])
    _patch_validation_supported(monkeypatch)

    async def _go():
        # Collect events in parallel with the pipeline run.
        collect_task = asyncio.create_task(_collect_sse_until_complete(
            filter_types={
                "research_progress", "research_subquery_started",
                "research_finding", "research_complete",
            },
            timeout=3.0,
        ))
        # Tiny yield to let the collector register before the pipeline starts.
        await asyncio.sleep(0.01)
        await run_research(pid, rid)
        return await collect_task

    events = _run(_go())
    types = [e["type"] for e in events]
    assert "research_progress" in types
    assert "research_subquery_started" in types
    assert "research_finding" in types
    assert "research_complete" in types
    # Complete must always be the last one we observe.
    assert types[-1] == "research_complete"
    complete = events[-1]
    assert complete["data"]["status"] == "ok"
    assert "token_usage" in complete["data"]
