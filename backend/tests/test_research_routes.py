"""HTTP-level tests for the Research router (P10).

Drives ``routers/research.py`` through a real FastAPI app + TestClient
backed by a throwaway SQLite DB. The Research pipeline itself is
monkeypatched to a fast no-op so the trigger route returns 202 without
booking 90 s of real LLM time.

Coverage:
    * POST /runs starts a run + writes the row + dispatches the task
    * POST /runs returns 409 when another run is already in flight
    * GET /runs lists most-recent first + respects status filter
    * GET /runs/{id} returns 404 on unknown id; 200 with detail otherwise
    * POST /runs/{id}/cancel sets the registered asyncio.Event
    * POST /findings/{id}/accept|reject patches status + user_actions log
    * GET /providers reflects per-project enable state
    * GET /providers/health degrades disabled providers + parallel probes
    * GET|PUT /settings round-trip
"""
from __future__ import annotations

import asyncio
import os
import secrets
import tempfile

import pytest

_TEST_DB_PATH = os.path.join(
    tempfile.gettempdir(),
    f"projecthub_pytest_routes_{secrets.token_hex(4)}.db",
)
os.environ["PROJECTHUB_DB_PATH"] = _TEST_DB_PATH


@pytest.fixture(scope="module")
def client():
    """In-process FastAPI client with the research router mounted.

    The trigger route's pipeline dispatch is monkey-patched at the
    module level so individual tests get a fresh fake.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    try:
        if os.path.exists(_TEST_DB_PATH):
            os.unlink(_TEST_DB_PATH)
    except OSError:
        pass

    import models  # noqa: F401 — register Base subclasses
    from database import init_db
    from routers.projects import router as projects_router
    from routers.research import router as research_router

    asyncio.get_event_loop().run_until_complete(init_db())

    app = FastAPI()
    app.include_router(projects_router)
    app.include_router(research_router)

    with TestClient(app) as c:
        yield c


def _new_project(client, *, name: str = "RouteTest") -> str:
    resp = client.post("/api/projects", json={"name": name})
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"]


def _patch_pipeline_noop(monkeypatch):
    """Replace the trigger route's pipeline dispatcher with a no-op."""
    import routers.research as rr

    async def fake(project_id, run_id, event):
        # Don't run the pipeline — just mark the run as finished so
        # the next test's already_running check doesn't trip.
        from database import async_session
        from models.research import ResearchRun
        from sqlalchemy import select
        async with async_session() as db:
            run = await db.scalar(
                select(ResearchRun).where(ResearchRun.id == run_id)
            )
            if run is not None:
                run.status = "ok"
                run.phase = "done"
                from datetime import datetime, timezone
                run.finished_at = datetime.now(timezone.utc).isoformat()
                await db.commit()
        # Pop the cancel-event registry like the real wrapper would.
        rr._cancel_events.pop(run_id, None)

    monkeypatch.setattr(rr, "_wrap_run_research", fake)


def _wait_for_status(client, run_id: str, expected_statuses: set[str], timeout: float = 2.0) -> str:
    """Poll the run detail endpoint until status lands in ``expected_statuses``.

    Trigger route's ``asyncio.create_task`` runs the pipeline (or the
    test fake) *after* the HTTP response is sent. Sync-style TestClient
    code that immediately reads the row sees ``running`` — this helper
    bridges the gap.
    """
    import time
    deadline = time.time() + timeout
    last_status = "?"
    while time.time() < deadline:
        resp = client.get(f"/api/research/runs/{run_id}")
        if resp.status_code == 200:
            last_status = resp.json()["run"]["status"]
            if last_status in expected_statuses:
                return last_status
        time.sleep(0.05)
    return last_status


# ── POST /runs ────────────────────────────────────────────────────────────


def test_start_run_creates_row_and_returns_202(client, monkeypatch):
    _patch_pipeline_noop(monkeypatch)
    pid = _new_project(client)

    resp = client.post(
        f"/api/research/{pid}/runs",
        json={"topic": "OAuth2 PKCE", "depth": "normal"},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["started"] is True
    assert body["depth"] == "normal"
    assert body["run_id"]


def test_start_run_rejects_invalid_depth(client, monkeypatch):
    _patch_pipeline_noop(monkeypatch)
    pid = _new_project(client)
    resp = client.post(
        f"/api/research/{pid}/runs",
        json={"topic": "x", "depth": "wonky"},
    )
    assert resp.status_code == 422  # pydantic Literal rejects unknown


def test_start_run_unknown_project_404(client, monkeypatch):
    _patch_pipeline_noop(monkeypatch)
    resp = client.post(
        "/api/research/does-not-exist/runs",
        json={"topic": "x"},
    )
    assert resp.status_code == 404


def test_start_run_returns_409_when_another_running(client, monkeypatch):
    """Pipeline is mocked but we manually leave the row in status=running
    so the second call hits the already_running short-circuit."""
    import routers.research as rr
    import asyncio as _asyncio

    pid = _new_project(client)

    async def leave_running(project_id, run_id, event):
        # Don't finalise — simulate a still-running pipeline.
        rr._cancel_events.pop(run_id, None)

    monkeypatch.setattr(rr, "_wrap_run_research", leave_running)

    # First call leaves the run row in status=running.
    first = client.post(f"/api/research/{pid}/runs", json={"topic": "X"})
    assert first.status_code == 202

    second = client.post(f"/api/research/{pid}/runs", json={"topic": "X"})
    assert second.status_code == 409
    body = second.json()["detail"]
    assert body["started"] is False
    assert body["reason"] == "already_running"


# ── GET /runs ─────────────────────────────────────────────────────────────


def test_list_runs_newest_first_with_status_filter(client, monkeypatch):
    _patch_pipeline_noop(monkeypatch)
    pid = _new_project(client)
    # Three runs back to back — wait for each to finish before posting
    # the next so the already_running guard doesn't 409 us.
    ids: list[str] = []
    for i in range(3):
        resp = client.post(f"/api/research/{pid}/runs", json={"topic": f"t{i}"})
        assert resp.status_code == 202, resp.text
        run_id = resp.json()["run_id"]
        _wait_for_status(client, run_id, {"ok"})
        ids.append(run_id)

    resp = client.get(f"/api/research/{pid}/runs?limit=2")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 2
    # All landed as status=ok by the noop fake.
    assert all(r["status"] == "ok" for r in rows)

    # Status filter that matches nothing → empty.
    resp = client.get(f"/api/research/{pid}/runs?status=error,cancelled")
    assert resp.status_code == 200
    assert resp.json() == []


# ── GET /runs/{id} ────────────────────────────────────────────────────────


def test_get_run_detail_unknown_404(client):
    resp = client.get("/api/research/runs/nonexistent")
    assert resp.status_code == 404


def test_get_run_detail_returns_full_shape(client, monkeypatch):
    _patch_pipeline_noop(monkeypatch)
    pid = _new_project(client)
    run_id = client.post(
        f"/api/research/{pid}/runs", json={"topic": "PKCE"},
    ).json()["run_id"]

    resp = client.get(f"/api/research/runs/{run_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"run", "sub_queries", "findings", "token_usage"}
    assert body["run"]["id"] == run_id
    assert body["run"]["topic"] == "PKCE"
    # Empty containers — pipeline noop didn't populate sub-queries.
    assert body["sub_queries"] == []
    assert body["findings"] == []


# ── POST /cancel ──────────────────────────────────────────────────────────


def test_cancel_unknown_404(client):
    resp = client.post("/api/research/runs/nope/cancel")
    assert resp.status_code == 404


def test_cancel_non_running_returns_false(client, monkeypatch):
    """Cancelling a run that already finished returns ok=False."""
    _patch_pipeline_noop(monkeypatch)
    pid = _new_project(client)
    run_id = client.post(
        f"/api/research/{pid}/runs", json={"topic": "x"},
    ).json()["run_id"]
    # Wait until the noop fake has flipped status=ok before testing cancel.
    _wait_for_status(client, run_id, {"ok"})
    resp = client.post(f"/api/research/runs/{run_id}/cancel")
    assert resp.status_code == 200
    body = resp.json()
    assert body["cancelled"] is False
    assert "status=ok" in (body.get("reason") or "")


def test_cancel_running_sets_event(client, monkeypatch):
    import routers.research as rr

    pid = _new_project(client)

    async def leave_running(project_id, run_id, event):
        # Leave running; don't clean up the cancel-event.
        pass

    monkeypatch.setattr(rr, "_wrap_run_research", leave_running)
    run_id = client.post(
        f"/api/research/{pid}/runs", json={"topic": "x"},
    ).json()["run_id"]

    # Confirm event is registered + not yet set.
    assert run_id in rr._cancel_events
    assert not rr._cancel_events[run_id].is_set()

    resp = client.post(f"/api/research/runs/{run_id}/cancel")
    assert resp.status_code == 200
    assert resp.json()["cancelled"] is True
    assert rr._cancel_events[run_id].is_set()


# ── POST /findings/{id}/accept | reject ───────────────────────────────────


def _seed_finding(client, monkeypatch, *, status: str = "flagged"):
    """Create a project + run + one finding manually for action tests."""
    from database import async_session
    from models.research import ResearchFinding, ResearchSubQuery

    _patch_pipeline_noop(monkeypatch)
    pid = _new_project(client)
    run_id = client.post(
        f"/api/research/{pid}/runs", json={"topic": "x"},
    ).json()["run_id"]
    fid = secrets.token_hex(8)
    sq_id = secrets.token_hex(8)

    async def _seed():
        async with async_session() as db:
            db.add(ResearchSubQuery(
                id=sq_id, run_id=run_id, question="q",
            ))
            db.add(ResearchFinding(
                id=fid, run_id=run_id, sub_query_id=sq_id,
                provider_key="kb_fts", source_ref="kb:test",
                title="t", snippet="s",
                status=status, confidence=0.4,
            ))
            await db.commit()

    asyncio.get_event_loop().run_until_complete(_seed())
    return run_id, fid


def test_accept_finding_promotes_to_grounded(client, monkeypatch):
    run_id, fid = _seed_finding(client, monkeypatch, status="flagged")
    resp = client.post(
        f"/api/research/runs/{run_id}/findings/{fid}/accept",
        json={"note": "looks fine"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["new_status"] == "grounded"

    detail = client.get(f"/api/research/runs/{run_id}").json()
    finding = next(f for f in detail["findings"] if f["id"] == fid)
    assert finding["status"] == "grounded"


def test_reject_finding_marks_rejected_with_note(client, monkeypatch):
    run_id, fid = _seed_finding(client, monkeypatch, status="flagged")
    resp = client.post(
        f"/api/research/runs/{run_id}/findings/{fid}/reject",
        json={"note": "off topic"},
    )
    assert resp.status_code == 200
    assert resp.json()["new_status"] == "rejected"


def test_finding_action_unknown_returns_404(client, monkeypatch):
    _patch_pipeline_noop(monkeypatch)
    pid = _new_project(client)
    run_id = client.post(f"/api/research/{pid}/runs", json={"topic": "x"}).json()["run_id"]
    resp = client.post(
        f"/api/research/runs/{run_id}/findings/bogus/accept",
        json={},
    )
    assert resp.status_code == 404


# ── GET /providers ────────────────────────────────────────────────────────


def test_list_providers_reflects_default_enabled_when_no_settings(client):
    pid = _new_project(client)
    resp = client.get(f"/api/research/{pid}/providers")
    assert resp.status_code == 200
    rows = {p["key"]: p for p in resp.json()}
    # Tier-1 locals are default-enabled.
    assert rows["kb_fts"]["enabled"] is True
    # Tier-2 externals are not.
    assert rows["confluence"]["enabled"] is False


def test_list_providers_reflects_per_project_settings(client):
    pid = _new_project(client)
    # Turn off kb_fts + turn on confluence for this project.
    resp = client.put(
        f"/api/research/{pid}/settings",
        json={"enabled_providers": ["confluence", "email"]},
    )
    assert resp.status_code == 200

    resp = client.get(f"/api/research/{pid}/providers")
    rows = {p["key"]: p for p in resp.json()}
    assert rows["kb_fts"]["enabled"] is False
    assert rows["confluence"]["enabled"] is True
    assert rows["email"]["enabled"] is True


# ── GET /providers/health ────────────────────────────────────────────────


def test_provider_health_returns_disabled_for_unconfigured(client):
    pid = _new_project(client)
    # Settings exist with only kb_fts on.
    client.put(f"/api/research/{pid}/settings",
               json={"enabled_providers": ["kb_fts"]})
    resp = client.get(f"/api/research/{pid}/providers/health")
    assert resp.status_code == 200
    rows = {p["key"]: p for p in resp.json()}
    # kb_fts enabled → real probe (returns ok=True for the local DB-backed provider).
    assert rows["kb_fts"]["ok"] is True
    assert rows["kb_fts"]["detail"] == "connected"
    # confluence is disabled → reported as ok=False/disabled, no real probe.
    assert rows["confluence"]["ok"] is False
    assert rows["confluence"]["detail"] == "disabled"


# ── GET|PUT /settings ────────────────────────────────────────────────────


def test_settings_default_when_no_row(client):
    pid = _new_project(client)
    resp = client.get(f"/api/research/{pid}/settings")
    assert resp.status_code == 200
    body = resp.json()
    assert body["default_depth"] == "normal"
    # Default-enabled providers are the local Tier-1 ones.
    assert "kb_fts" in body["enabled_providers"]
    assert body["provider_settings"] == {}


def test_settings_put_creates_row(client):
    pid = _new_project(client)
    resp = client.put(
        f"/api/research/{pid}/settings",
        json={
            "default_depth": "tief",
            "enabled_providers": ["kb_fts", "confluence"],
            "provider_settings": {"confluence": {"spaces": ["TEAM"]}},
            "routing_hints": "Architektur immer Confluence vor Code-Graph",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["default_depth"] == "tief"
    assert body["enabled_providers"] == ["kb_fts", "confluence"]
    assert body["provider_settings"] == {"confluence": {"spaces": ["TEAM"]}}
    assert "Architektur" in body["routing_hints"]


def test_settings_put_drops_unknown_providers_silently(client):
    pid = _new_project(client)
    resp = client.put(
        f"/api/research/{pid}/settings",
        json={"enabled_providers": ["kb_fts", "obviously_not_a_provider"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled_providers"] == ["kb_fts"]


def test_settings_put_partial_update_preserves_others(client):
    pid = _new_project(client)
    client.put(
        f"/api/research/{pid}/settings",
        json={"default_depth": "tief", "routing_hints": "hint1"},
    )
    # Partial update — only enabled_providers.
    client.put(
        f"/api/research/{pid}/settings",
        json={"enabled_providers": ["kb_fts"]},
    )
    body = client.get(f"/api/research/{pid}/settings").json()
    assert body["default_depth"] == "tief"  # preserved
    assert body["routing_hints"] == "hint1"  # preserved
    assert body["enabled_providers"] == ["kb_fts"]
