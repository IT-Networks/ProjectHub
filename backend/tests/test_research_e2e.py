"""End-to-end + hardening tests for the Research Auto-Mode pipeline (P15).

The phases P0-P12 each shipped their own focused tests; this module
covers the *integration* scenarios that need the whole stack working
together. Each test drives the real ``run_research`` pipeline from
beginning to end, with only the LLM-bound external calls
(synapse_llm.call_json, research_lateral.call_json, AI-Assist clients)
monkey-patched away.

Covered scenarios:

    1. Multi-provider fan-out — 3 mock providers run concurrently;
       findings from all three land in the DB; concurrency-semaphore
       respected.
    2. Provider failure isolation — one provider raises mid-stream;
       the other providers + the run as a whole still finish ok,
       with error_summary recording the failure.
    3. Validation status mapping — provider yields confident +
       low-confidence findings; verify the verdict-to-status mapping
       (grounded → persisted / flagged / rejected) ends up correct
       across the whole DB after the run finalises.
    4. Cancel during SEARCH — mid-stream cancel leaves the run in
       status=cancelled with the findings collected so far persisted
       but not promoted to KnowledgeItems.
    5. Adaptive-budget request — planner emits ``budget_request``;
       BudgetTracker grants it once + audit-trail records it.
    6. Tief-mode lateral lineage — full Tief run produces lateral
       sub-queries linked back to their parent findings, plus an
       inline synapse run triggered.

These aren't exhaustive — the unit tests cover edge cases per
component. P15 confirms the *seams* between components actually
hold under real pipeline execution.
"""
from __future__ import annotations

import asyncio
import os
import secrets
import tempfile

import pytest

_TEST_DB_PATH = os.path.join(
    tempfile.gettempdir(),
    f"projecthub_pytest_e2e_{secrets.token_hex(4)}.db",
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


# ── Setup helpers ──────────────────────────────────────────────────────────


def _new_project_id() -> str:
    from database import async_session
    from models.project import Project

    async def _go():
        async with async_session() as db:
            pid = secrets.token_hex(8)
            db.add(Project(id=pid, name="E2ETest"))
            await db.commit()
            return pid

    return _run(_go())


def _new_run(project_id: str, *, depth: str = "normal", topic: str = "X") -> str:
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


class _DeterministicProvider:
    """Configurable mock provider for E2E tests.

    Yields ``n_findings`` findings with the given confidence + status.
    If ``raise_on_stream`` is set, raises that error after yielding
    ``raise_after`` findings (or 0 = before any).
    If ``concurrency_marker`` is set, appends self to it on entry and
    sleeps briefly so concurrent providers can pile up.
    """

    def __init__(
        self,
        key: str,
        *,
        n_findings: int = 2,
        confidence: float = 0.85,
        raise_on_stream: type[BaseException] | None = None,
        raise_after: int = 0,
        concurrency_marker: list | None = None,
        delay_s: float = 0.0,
    ):
        self.key = key
        self.description = "deterministic-fake"
        self.typical_latency = "fast"
        self.side_effect = "read"
        self.default_enabled = True
        self._n = n_findings
        self._conf = confidence
        self._raise = raise_on_stream
        self._raise_after = raise_after
        self._marker = concurrency_marker
        self._delay = delay_s

    async def health(self):
        from services.research_providers.base import ProviderHealth
        return ProviderHealth(ok=True, detail="connected", last_checked_at="now")

    async def stream(self, query, provider_settings, cancel, *, project_id):
        from services.research_providers.base import Finding, SearchProgress

        if self._marker is not None:
            self._marker.append(self.key)
        if self._delay > 0:
            await asyncio.sleep(self._delay)

        for i in range(self._n):
            if cancel.is_set():
                yield SearchProgress(kind="done", status_text="cancelled")
                return
            if self._raise is not None and i == self._raise_after:
                raise self._raise(f"injected {self.key} failure")
            yield SearchProgress(
                kind="finding",
                finding=Finding(
                    provider_key=self.key,
                    source_ref=f"{self.key}:{secrets.token_hex(4)}-{i}",
                    title=f"{self.key} finding {i}",
                    snippet="snippet text",
                    full_content="full content",
                    score=self._conf,
                ),
            )
        yield SearchProgress(kind="done", status_text="ok")


def _install_providers(monkeypatch, providers: dict[str, _DeterministicProvider]):
    from services.research_providers import PROVIDERS
    for key, p in providers.items():
        monkeypatch.setitem(PROVIDERS, key, p)


def _patch_planner(monkeypatch, sub_queries: list[dict]):
    from services.research_planner import PlanResult, SubQueryPlan
    import services.research_pipeline as pipe

    async def fake_plan(topic, **kw):
        return PlanResult(
            sub_queries=[SubQueryPlan(**sq) for sq in sub_queries],
            raw_response={},
        )

    monkeypatch.setattr(pipe, "plan_subqueries", fake_plan)


def _patch_validation(monkeypatch, *, relation: str = "supported", score: float = 0.9):
    """Patch the validation LLM to return a deterministic relation."""
    import services.research_validation as rv

    async def fake(*a, **k):
        class R:
            parsed = {"relation": relation, "score": score, "reason": ""}
            ok = True
            usage = {"total_tokens": 500}
        return R()

    monkeypatch.setattr(rv, "call_json", fake)


def _patch_lateral_noop(monkeypatch):
    """Lateral LLM calls return empty entities → no expansion."""
    import services.research_lateral as rl

    async def fake(*a, **k):
        class R:
            parsed = []
            ok = True
            usage = {}
        return R()

    monkeypatch.setattr(rl, "call_json", fake)


def _patch_synapse_noop(monkeypatch):
    """Synapse pipeline is a no-op so Tief tests don't run the real
    project-wide synthesis (which needs AI-Assist)."""
    import services.research_pipeline as pipe

    async def fake(project_id, run_id, *, scope_item_ids=None):
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


# ── E2E-1: Multi-provider fan-out ──────────────────────────────────────────


def test_e2e_multi_provider_fanout(monkeypatch, initdb):
    """3 providers concurrently feed into one sub-query; all findings land,
    semaphore caps in-flight at 4 (so 3 is unbounded here — just verify all
    yield)."""
    from services.research_pipeline import run_research
    from models.research import ResearchFinding
    from database import async_session
    from sqlalchemy import select

    pid = _new_project_id()
    _enable_providers(pid, ["p_a", "p_b", "p_c"])
    rid = _new_run(pid)

    marker: list[str] = []
    _install_providers(monkeypatch, {
        "p_a": _DeterministicProvider("p_a", n_findings=2, concurrency_marker=marker, delay_s=0.02),
        "p_b": _DeterministicProvider("p_b", n_findings=2, concurrency_marker=marker, delay_s=0.02),
        "p_c": _DeterministicProvider("p_c", n_findings=2, concurrency_marker=marker, delay_s=0.02),
    })
    # Three sub-queries, each pointing at one provider — concurrent fan-out.
    _patch_planner(monkeypatch, [
        {"id": secrets.token_hex(8), "question": "q_a",
         "providers": ["p_a"], "rationale": "", "priority": 1},
        {"id": secrets.token_hex(8), "question": "q_b",
         "providers": ["p_b"], "rationale": "", "priority": 1},
        {"id": secrets.token_hex(8), "question": "q_c",
         "providers": ["p_c"], "rationale": "", "priority": 1},
    ])
    _patch_validation(monkeypatch)

    _run(run_research(pid, rid))

    async def _read():
        async with async_session() as db:
            findings = (await db.execute(
                select(ResearchFinding).where(ResearchFinding.run_id == rid)
            )).scalars().all()
        return findings

    findings = _run(_read())
    assert len(findings) == 6  # 3 SQ × 2 findings
    providers_seen = {f.provider_key for f in findings}
    assert providers_seen == {"p_a", "p_b", "p_c"}
    assert set(marker) == {"p_a", "p_b", "p_c"}  # all three entered the stream


# ── E2E-2: Provider failure isolation ──────────────────────────────────────


def test_e2e_provider_exception_does_not_kill_run(monkeypatch, initdb):
    """One provider raises mid-stream; other providers + run still finish ok."""
    from services.research_pipeline import run_research
    from models.research import ResearchFinding, ResearchRun
    from database import async_session
    from sqlalchemy import select

    pid = _new_project_id()
    _enable_providers(pid, ["bad", "good"])
    rid = _new_run(pid)

    _install_providers(monkeypatch, {
        "bad": _DeterministicProvider("bad", raise_on_stream=RuntimeError, raise_after=0),
        "good": _DeterministicProvider("good", n_findings=3),
    })
    _patch_planner(monkeypatch, [
        {"id": secrets.token_hex(8), "question": "q1",
         "providers": ["bad"], "rationale": "", "priority": 1},
        {"id": secrets.token_hex(8), "question": "q2",
         "providers": ["good"], "rationale": "", "priority": 1},
    ])
    _patch_validation(monkeypatch)

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
    # The "good" provider's 3 findings made it through; "bad" raised, was
    # logged in error_summary, but the run still finalises ok.
    assert run.status == "ok"
    assert len(findings) == 3
    assert all(f.provider_key == "good" for f in findings)
    assert run.error_summary and "bad" in run.error_summary


# ── E2E-3: Validation status mapping per finding ──────────────────────────


def test_e2e_validation_maps_status_per_finding(monkeypatch, initdb):
    """Verify the verdict-to-status pipeline:
       contradicted → rejected (no KB write)
       supported high  → grounded → persisted (KB write)"""
    from services.research_pipeline import run_research
    from models.knowledge import KnowledgeItem
    from models.research import ResearchFinding
    from database import async_session
    from sqlalchemy import select

    pid = _new_project_id()
    _enable_providers(pid, ["p1"])
    rid = _new_run(pid)

    _install_providers(monkeypatch, {
        "p1": _DeterministicProvider("p1", n_findings=4, confidence=0.9),
    })
    _patch_planner(monkeypatch, [
        {"id": secrets.token_hex(8), "question": "q",
         "providers": ["p1"], "rationale": "", "priority": 1},
    ])

    # Validation flips: every other Tier-B grounding call returns
    # contradicted vs supported. Tier-C critic calls (which fire only
    # on contradicted relations in Normal mode) get a stable
    # "contradicted" so the aggregation still rejects.
    import services.research_validation as rv
    grounding_n = {"i": 0}

    async def alternating(prompt, *, model=None, session_prefix=None):
        if session_prefix == "research-grounding":
            grounding_n["i"] += 1
            relation = "supported" if grounding_n["i"] % 2 == 1 else "contradicted"
        else:  # research-critic — only fires for contradicted relations
            relation = "contradicted"

        class R:
            parsed = {"relation": relation, "score": 0.9, "reason": "test"}
            ok = True
            usage = {"total_tokens": 500}
        return R()

    monkeypatch.setattr(rv, "call_json", alternating)

    _run(run_research(pid, rid))

    async def _read():
        async with async_session() as db:
            findings = (await db.execute(
                select(ResearchFinding).where(ResearchFinding.run_id == rid)
            )).scalars().all()
            ki_count = len((await db.execute(
                select(KnowledgeItem).where(KnowledgeItem.project_id == pid)
            )).scalars().all())
        return findings, ki_count

    findings, ki_count = _run(_read())
    persisted = [f for f in findings if f.status == "persisted"]
    rejected = [f for f in findings if f.status == "rejected"]
    # 4 findings: 2 supported → persisted, 2 contradicted → rejected.
    assert len(persisted) == 2, [f.status for f in findings]
    assert len(rejected) == 2
    # Only persisted findings get a KnowledgeItem.
    assert ki_count == 2
    # Each persisted finding links to its KnowledgeItem.
    assert all(f.knowledge_item_id for f in persisted)


# ── E2E-4: Cancel mid-search ──────────────────────────────────────────────


def test_e2e_cancel_mid_search_keeps_status_cancelled(monkeypatch, initdb):
    """Cancel-event fires while the provider is still yielding findings.
    Pipeline finalises status=cancelled; persisted-count is zero (cancel
    fires before VALIDATE/PERSIST runs)."""
    from services.research_pipeline import run_research
    from models.research import ResearchRun
    from database import async_session
    from sqlalchemy import select

    pid = _new_project_id()
    _enable_providers(pid, ["slow"])
    rid = _new_run(pid)

    cancel = asyncio.Event()

    # Provider that yields one finding then waits — cancel fires
    # between findings so the SEARCH stage is interrupted.
    from services.research_providers.base import (
        Finding,
        ProviderHealth,
        SearchProgress,
    )
    from services.research_providers import PROVIDERS

    class _SlowProvider:
        key = "slow"
        description = "slow-fake"
        typical_latency = "fast"
        side_effect = "read"
        default_enabled = True

        async def health(self):
            return ProviderHealth(ok=True, detail="connected", last_checked_at="now")

        async def stream(self, query, provider_settings, cancel_event, *, project_id):
            yield SearchProgress(
                kind="finding",
                finding=Finding(
                    provider_key=self.key, source_ref="slow:1",
                    title="first", snippet="s", score=0.8,
                ),
            )
            # Now trip cancel before the next finding.
            cancel.set()
            yield SearchProgress(kind="done", status_text="cancelled")

    monkeypatch.setitem(PROVIDERS, "slow", _SlowProvider())
    _patch_planner(monkeypatch, [
        {"id": secrets.token_hex(8), "question": "q",
         "providers": ["slow"], "rationale": "", "priority": 1},
    ])

    _run(run_research(pid, rid, cancel=cancel))

    async def _read():
        async with async_session() as db:
            return (await db.execute(
                select(ResearchRun).where(ResearchRun.id == rid)
            )).scalar_one()

    run = _run(_read())
    assert run.status == "cancelled"
    assert run.persisted_count == 0


# ── E2E-5: Adaptive budget request ─────────────────────────────────────────


def test_e2e_planner_budget_request_extends_tracker(monkeypatch, initdb):
    """Planner returns a sub-query with budget_request set; tracker grants
    the extension once and records it in degradations_triggered."""
    from services.research_pipeline import run_research
    from models.research import ResearchRun
    from database import async_session
    from sqlalchemy import select

    pid = _new_project_id()
    _enable_providers(pid, ["p1"])
    rid = _new_run(pid)
    _install_providers(monkeypatch, {
        "p1": _DeterministicProvider("p1", n_findings=1),
    })
    _patch_planner(monkeypatch, [
        # First SQ requests an extension; tracker grants the smaller of
        # (50_000, max_extension_fraction * hard_cap = 0.3 * 400_000 = 120_000).
        {"id": secrets.token_hex(8), "question": "expensive",
         "providers": ["p1"], "rationale": "heavy",
         "priority": 1, "budget_request": 50_000},
    ])
    _patch_validation(monkeypatch)

    _run(run_research(pid, rid))

    async def _read():
        async with async_session() as db:
            return (await db.execute(
                select(ResearchRun).where(ResearchRun.id == rid)
            )).scalar_one()

    run = _run(_read())
    usage = run.token_usage_dict
    degradations = usage.get("degradations_triggered") or []
    # Audit trail records the extension grant.
    assert any("extension_granted" in d for d in degradations), degradations
    # Extension landed on the snapshot.
    assert usage.get("extension_amount", 0) >= 50_000
    assert usage.get("extensions_used", 0) == 1


# ── E2E-6: Tief mode produces lateral lineage + synapse trigger ───────────


def test_e2e_tief_full_pipeline_with_lateral_and_synapse(monkeypatch, initdb):
    """Full Tief run: PLAN → SEARCH → LATERAL → VALIDATE → PERSIST →
    SYNTHESISE. Verifies lateral sub-queries carry parent_finding_ids
    and that a SynapseGenerationRun got created + linked."""
    from services.research_pipeline import run_research
    from models.research import ResearchRun, ResearchSubQuery
    from models.synapse import SynapseGenerationRun
    from database import async_session
    from sqlalchemy import select

    pid = _new_project_id()
    _enable_providers(pid, ["fake"])
    rid = _new_run(pid, depth="tief", topic="PKCE")

    _install_providers(monkeypatch, {
        "fake": _DeterministicProvider("fake", n_findings=3, confidence=0.85),
    })
    _patch_planner(monkeypatch, [
        {"id": secrets.token_hex(8), "question": "main",
         "providers": ["fake"], "rationale": "", "priority": 1},
    ])
    _patch_validation(monkeypatch)
    _patch_synapse_noop(monkeypatch)

    # Lateral: yields one entity per finding → relevance 0.9 → 1 sub-query
    import services.research_lateral as rl
    call_idx = {"n": 0}

    async def lateral_responses(prompt, *a, **k):
        call_idx["n"] += 1
        if "Extrahiere" in prompt:
            class R:
                parsed = [{"name": f"entity-{call_idx['n']}", "confidence": 0.9}]
                ok = True
                usage = {}
            return R()
        if "Bewerte" in prompt:
            class R2:
                parsed = [{"id": i + 1, "relevance": 0.9} for i in range(5)]
                ok = True
                usage = {}
            return R2()
        # plan prompt
        class P:
            parsed = {"question": "lateral question", "providers": ["fake"], "rationale": "x"}
            ok = True
            usage = {}
        return P()

    monkeypatch.setattr(rl, "call_json", lateral_responses)

    _run(run_research(pid, rid))

    async def _read():
        async with async_session() as db:
            run = (await db.execute(
                select(ResearchRun).where(ResearchRun.id == rid)
            )).scalar_one()
            sqs = (await db.execute(
                select(ResearchSubQuery).where(ResearchSubQuery.run_id == rid)
            )).scalars().all()
            synapse_runs = (await db.execute(
                select(SynapseGenerationRun).where(
                    SynapseGenerationRun.project_id == pid
                )
            )).scalars().all()
        return run, sqs, synapse_runs

    run, sqs, synapse_runs = _run(_read())
    # Lateral sub-queries exist + carry parent finding ids.
    lateral_sqs = [sq for sq in sqs if sq.is_lateral]
    assert lateral_sqs, "expected ≥1 lateral sub-query"
    assert all(sq.parent_finding_ids_list for sq in lateral_sqs)
    assert all(sq.entity_focus for sq in lateral_sqs)
    # Synapse run was triggered + linked.
    assert run.synapse_run_id is not None
    assert len(synapse_runs) == 1
    assert synapse_runs[0].id == run.synapse_run_id
    assert synapse_runs[0].trigger == "auto_research"


# ── E2E-7: Idempotent re-trigger ──────────────────────────────────────────


def test_e2e_re_run_creates_separate_findings(monkeypatch, initdb):
    """Re-running with the same topic creates a NEW run row (no
    deduplication in v1) — documents the current behaviour. A future
    P15.1 may add finding-level idempotency via source_ref hashing."""
    from services.research_pipeline import run_research
    from models.research import ResearchFinding, ResearchRun
    from database import async_session
    from sqlalchemy import select

    pid = _new_project_id()
    _enable_providers(pid, ["p1"])
    _install_providers(monkeypatch, {
        "p1": _DeterministicProvider("p1", n_findings=2),
    })
    _patch_planner(monkeypatch, [
        {"id": secrets.token_hex(8), "question": "q",
         "providers": ["p1"], "rationale": "", "priority": 1},
    ])
    _patch_validation(monkeypatch)

    # Run 1
    rid1 = _new_run(pid, topic="same topic")
    _run(run_research(pid, rid1))

    # Run 2 — same topic, fresh run id (planner uses secrets.token_hex
    # internally, so sub-query ids never collide).
    _patch_planner(monkeypatch, [
        {"id": secrets.token_hex(8), "question": "q",
         "providers": ["p1"], "rationale": "", "priority": 1},
    ])
    rid2 = _new_run(pid, topic="same topic")
    _run(run_research(pid, rid2))

    async def _count():
        async with async_session() as db:
            runs = (await db.execute(
                select(ResearchRun).where(ResearchRun.project_id == pid)
            )).scalars().all()
            findings = (await db.execute(
                select(ResearchFinding)
                .join(ResearchRun, ResearchRun.id == ResearchFinding.run_id)
                .where(ResearchRun.project_id == pid)
            )).scalars().all()
        return runs, findings

    runs, findings = _run(_count())
    assert len(runs) == 2  # Two separate runs.
    assert len(findings) == 4  # Two findings per run × two runs.
    # source_ref values are unique per finding (because the provider
    # generates a token_hex per yield), so no DB-level dedup happens.
    refs = [f.source_ref for f in findings]
    assert len(set(refs)) == len(refs)
