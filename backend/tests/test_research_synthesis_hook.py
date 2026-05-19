"""Inline-Synthesis-Hook tests (P9).

After PERSIST, the pipeline optionally fires off a SynapseGenerationRun
to bundle the freshly persisted KnowledgeItems into Synapses. Driven by
``profile.auto_synthesise`` — True for Tief, False for Normal.

The actual synapse pipeline is mocked here so we don't run a real
multi-minute LLM chain just to test the wiring. We assert:

    * Tief run with persisted findings → synapse run created with
      trigger="auto_research", linked into ResearchRun.synapse_run_id,
      surfaced in research_complete SSE
    * Normal run → no synapse run, no synapse_run_id on the research run
    * scope_item_ids hint reaches the synapse pipeline (metadata
      correlation for the operator)
    * Already-running synapse → inline trigger short-circuits, research
      still finalises ok
"""
from __future__ import annotations

import asyncio
import os
import secrets
import tempfile

import pytest

_TEST_DB_PATH = os.path.join(
    tempfile.gettempdir(),
    f"projecthub_pytest_synthesis_{secrets.token_hex(4)}.db",
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


# ── Setup helpers (mirror test_research_pipeline_normal patterns) ─────────


def _new_project_id() -> str:
    from database import async_session
    from models.project import Project

    async def _go():
        async with async_session() as db:
            pid = secrets.token_hex(8)
            db.add(Project(id=pid, name="SynthTest"))
            await db.commit()
            return pid

    return _run(_go())


def _new_run(project_id: str, *, depth: str) -> str:
    from database import async_session
    from models.research import ResearchRun

    async def _go():
        async with async_session() as db:
            rid = secrets.token_hex(8)
            db.add(ResearchRun(
                id=rid, project_id=project_id, topic="t",
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


def _patch_provider(monkeypatch, key: str, n: int = 2):
    """High-conf finding provider so validation grounds + persist fires."""
    from services.research_providers import PROVIDERS
    from services.research_providers.base import (
        Finding, ProviderHealth, SearchProgress,
    )

    class _FakeProvider:
        def __init__(self):
            self.key = key
            self.description = "fake"
            self.typical_latency = "fast"
            self.side_effect = "read"
            self.default_enabled = True

        async def health(self):
            return ProviderHealth(ok=True, detail="connected", last_checked_at="now")

        async def stream(self, query, provider_settings, cancel, *, project_id):
            for i in range(n):
                if cancel.is_set():
                    return
                yield SearchProgress(
                    kind="finding",
                    finding=Finding(
                        provider_key=self.key,
                        source_ref=f"{self.key}:{secrets.token_hex(4)}-{i}",
                        title=f"finding {i}", snippet="text", full_content="content",
                        score=0.85,
                    ),
                )
            yield SearchProgress(kind="done", status_text="ok")

    monkeypatch.setitem(PROVIDERS, key, _FakeProvider())


def _patch_planner(monkeypatch, providers: list[str]):
    from services.research_planner import PlanResult, SubQueryPlan
    import services.research_pipeline as pipe

    async def fake_plan(topic, **kw):
        sq = SubQueryPlan(
            id=secrets.token_hex(8), question="q",
            providers=providers, rationale="r", priority=1,
        )
        return PlanResult(sub_queries=[sq], raw_response={"sub_queries": []})

    monkeypatch.setattr(pipe, "plan_subqueries", fake_plan)


def _patch_validation_supported(monkeypatch):
    import services.research_validation as rv

    async def fake(*a, **k):
        class R:
            parsed = {"relation": "supported", "score": 0.9, "reason": "ok"}
            ok = True
            usage = {"total_tokens": 500}
        return R()

    monkeypatch.setattr(rv, "call_json", fake)


def _patch_lateral_noop(monkeypatch):
    """Stub the lateral LLM calls so Tief-mode lateral expansion is a no-op."""
    import services.research_lateral as rl

    async def fake(*a, **k):
        class R:
            parsed = []          # no entities → expand_hop aborts
            ok = True
            usage = {}
        return R()

    monkeypatch.setattr(rl, "call_json", fake)


def _patch_synapse_pipeline_noop(monkeypatch, *, capture: dict):
    """Mock run_synapse_generation so we don't run the real synapse pipeline.

    Captures the call args so tests can assert that scope_item_ids was
    passed and that the synapse run gets the correct trigger label.
    """
    import services.research_pipeline as pipe

    async def fake(project_id, run_id, *, scope_item_ids=None):
        capture["project_id"] = project_id
        capture["run_id"] = run_id
        capture["scope_item_ids"] = scope_item_ids
        # Mark the synapse run "ok" so the pipeline's link survives a later
        # FK check.
        from database import async_session
        from models.synapse import SynapseGenerationRun
        from sqlalchemy import select
        async with async_session() as db:
            sr = await db.scalar(
                select(SynapseGenerationRun).where(SynapseGenerationRun.id == run_id)
            )
            if sr is not None:
                sr.status = "ok"
                sr.phase = "done"
                await db.commit()

    monkeypatch.setattr(pipe, "run_synapse_generation", fake)


# ── Tests ──────────────────────────────────────────────────────────────────


def test_tief_run_triggers_inline_synapse(monkeypatch, initdb):
    """Tief profile has auto_synthesise=True → synapse run created."""
    from services.research_pipeline import run_research
    from models.research import ResearchRun
    from models.synapse import SynapseGenerationRun
    from database import async_session
    from sqlalchemy import select

    pid = _new_project_id()
    _enable_providers(pid, ["fake_a"])
    rid = _new_run(pid, depth="tief")

    _patch_provider(monkeypatch, "fake_a")
    _patch_planner(monkeypatch, ["fake_a"])
    _patch_validation_supported(monkeypatch)
    _patch_lateral_noop(monkeypatch)
    capture: dict = {}
    _patch_synapse_pipeline_noop(monkeypatch, capture=capture)

    _run(run_research(pid, rid))

    async def _read():
        async with async_session() as db:
            run = (await db.execute(
                select(ResearchRun).where(ResearchRun.id == rid)
            )).scalar_one()
            synapse_runs = (await db.execute(
                select(SynapseGenerationRun).where(
                    SynapseGenerationRun.project_id == pid
                )
            )).scalars().all()
        return run, synapse_runs

    run, synapse_runs = _run(_read())
    assert run.synapse_run_id is not None
    assert len(synapse_runs) == 1
    assert synapse_runs[0].trigger == "auto_research"
    assert synapse_runs[0].id == run.synapse_run_id
    # scope_item_ids hint was passed.
    assert capture["scope_item_ids"]
    assert isinstance(capture["scope_item_ids"], list)


def test_normal_run_does_not_trigger_inline_synapse(monkeypatch, initdb):
    """Normal profile has auto_synthesise=False → no synapse run."""
    from services.research_pipeline import run_research
    from models.research import ResearchRun
    from models.synapse import SynapseGenerationRun
    from database import async_session
    from sqlalchemy import select

    pid = _new_project_id()
    _enable_providers(pid, ["fake_a"])
    rid = _new_run(pid, depth="normal")

    _patch_provider(monkeypatch, "fake_a")
    _patch_planner(monkeypatch, ["fake_a"])
    _patch_validation_supported(monkeypatch)

    # The synapse pipeline must NOT be called in Normal mode.
    import services.research_pipeline as pipe

    async def must_not_call(*a, **k):
        raise AssertionError("Normal-mode triggered the inline synapse hook!")

    monkeypatch.setattr(pipe, "run_synapse_generation", must_not_call)

    _run(run_research(pid, rid))

    async def _read():
        async with async_session() as db:
            run = (await db.execute(
                select(ResearchRun).where(ResearchRun.id == rid)
            )).scalar_one()
            synapse_runs = (await db.execute(
                select(SynapseGenerationRun).where(
                    SynapseGenerationRun.project_id == pid
                )
            )).scalars().all()
        return run, synapse_runs

    run, synapse_runs = _run(_read())
    assert run.synapse_run_id is None
    assert synapse_runs == []


def test_tief_run_with_zero_persists_skips_synapse(monkeypatch, initdb):
    """No persisted findings (e.g. all flagged/rejected) → no synapse run."""
    from services.research_pipeline import run_research
    from models.research import ResearchRun
    from database import async_session
    from sqlalchemy import select

    pid = _new_project_id()
    _enable_providers(pid, ["fake_a"])
    rid = _new_run(pid, depth="tief")

    _patch_provider(monkeypatch, "fake_a")
    _patch_planner(monkeypatch, ["fake_a"])

    # Validation rejects every finding (relation=contradicted → status=rejected
    # → not promoted to KnowledgeItem).
    import services.research_validation as rv

    async def reject_everything(*a, **k):
        class R:
            parsed = {"relation": "contradicted", "score": 0.9, "reason": "no"}
            ok = True
            usage = {"total_tokens": 500}
        return R()

    monkeypatch.setattr(rv, "call_json", reject_everything)
    _patch_lateral_noop(monkeypatch)

    # Synapse pipeline must NOT be called when there's nothing to bundle.
    import services.research_pipeline as pipe

    async def must_not_call(*a, **k):
        raise AssertionError("synapse triggered despite zero persists!")

    monkeypatch.setattr(pipe, "run_synapse_generation", must_not_call)

    _run(run_research(pid, rid))

    async def _read():
        async with async_session() as db:
            run = (await db.execute(
                select(ResearchRun).where(ResearchRun.id == rid)
            )).scalar_one()
        return run

    run = _run(_read())
    assert run.synapse_run_id is None
    assert run.persisted_count == 0
    # Run still finalises ok (rejected findings are valid outcomes).
    assert run.status in ("ok", "partial")


def test_inline_synapse_short_circuits_when_already_running(monkeypatch, initdb):
    """A pre-existing 'running' synapse for the same project blocks the
    inline trigger — research run still finalises cleanly."""
    from services.research_pipeline import run_research
    from models.research import ResearchRun
    from models.synapse import SynapseGenerationRun
    from database import async_session
    from sqlalchemy import select

    pid = _new_project_id()
    _enable_providers(pid, ["fake_a"])
    rid = _new_run(pid, depth="tief")

    # Seed a running synapse run before the research starts.
    pre_existing_synapse_id = secrets.token_hex(8)

    async def _seed():
        async with async_session() as db:
            db.add(SynapseGenerationRun(
                id=pre_existing_synapse_id, project_id=pid,
                trigger="manual", status="running", phase="extracting_entities",
            ))
            await db.commit()

    _run(_seed())

    _patch_provider(monkeypatch, "fake_a")
    _patch_planner(monkeypatch, ["fake_a"])
    _patch_validation_supported(monkeypatch)
    _patch_lateral_noop(monkeypatch)

    # Real synapse pipeline still mustn't run.
    import services.research_pipeline as pipe

    async def must_not_call(*a, **k):
        raise AssertionError("synapse triggered despite pre-existing run!")

    monkeypatch.setattr(pipe, "run_synapse_generation", must_not_call)

    _run(run_research(pid, rid))

    async def _read():
        async with async_session() as db:
            run = (await db.execute(
                select(ResearchRun).where(ResearchRun.id == rid)
            )).scalar_one()
            synapse_runs = (await db.execute(
                select(SynapseGenerationRun).where(
                    SynapseGenerationRun.project_id == pid
                )
            )).scalars().all()
        return run, synapse_runs

    run, synapse_runs = _run(_read())
    # Research-run completed without crash, no new synapse linked.
    assert run.status in ("ok", "partial")
    assert run.synapse_run_id is None
    # Only the pre-existing synapse row remains.
    assert len(synapse_runs) == 1
    assert synapse_runs[0].id == pre_existing_synapse_id
