"""Tests for the bi-temporal SynapseClaim layer (P10).

Two layers under test:

    services/synapse_claims_bitemporal.py — repo helpers
    routers/synapse.py                    — as_of / include_history query

The unit tests drive the helpers directly on a fresh AsyncSession; the
API tests drive the FastAPI app via TestClient. No LLM, no network.

Coverage:

* current_claims: returns only valid_to=NULL rows, ordered by valid_from
* claims_as_of: snapshot semantics at three different timestamps
* supersede_claims: full-regen path closes ALL old + inserts new
* supersede_claims: partial path leaves untargeted current rows alone
* supersede_claim_by_id: happy path + already-superseded short-circuit
* close_synapse_claims: idempotent, closes all currently-valid rows
* SynapseClaim.is_current property
* GET /synapses/{id}: default = current only
* GET /synapses/{id}?as_of=...: snapshot at point in time
* GET /synapses/{id}?include_history=true: every version
"""
from __future__ import annotations

import asyncio
import os
import secrets
import tempfile
from datetime import datetime, timedelta, timezone

import pytest


_TEST_DB_PATH = os.path.join(
    tempfile.gettempdir(),
    f"projecthub_pytest_bitemp_{secrets.token_hex(4)}.db",
)
os.environ["PROJECTHUB_DB_PATH"] = _TEST_DB_PATH


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _gen_id() -> str:
    return secrets.token_hex(8)


@pytest.fixture(scope="module")
def client():
    """Fresh FastAPI app + DB per test module."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    try:
        if os.path.exists(_TEST_DB_PATH):
            os.unlink(_TEST_DB_PATH)
    except OSError:
        pass

    import models  # noqa: F401
    from database import init_db
    from routers.projects import router as projects_router
    from routers.synapse import router as synapse_router

    asyncio.get_event_loop().run_until_complete(init_db())

    app = FastAPI()
    app.include_router(projects_router)
    app.include_router(synapse_router)

    with TestClient(app) as c:
        yield c


def _make_synapse_and_claims(client, claims_payload: list[dict]) -> tuple[str, str, list[str]]:
    """Seed: project + synapse + N claims directly via SQLAlchemy.

    Returns (project_id, synapse_id, [claim_ids]).
    """
    import asyncio as _aio
    from database import async_session
    from models import Project
    from models.synapse import Synapse, SynapseClaim

    proj_id = _gen_id()
    syn_id = _gen_id()
    claim_ids = []

    async def _seed():
        async with async_session() as db:
            db.add(Project(id=proj_id, name=f"Test {proj_id[:6]}", description="bitemp test"))
            db.add(Synapse(
                id=syn_id, project_id=proj_id,
                title="Test Synapse", summary="x", summary_plain="x",
                confidence=0.8, confidence_band="high", verdict="persist",
                status="validated",
            ))
            for p in claims_payload:
                cid = p.get("id") or _gen_id()
                claim_ids.append(cid)
                row = SynapseClaim(
                    id=cid, synapse_id=syn_id,
                    claim_text=p["claim_text"],
                    relation=p.get("relation", "supported"),
                    valid_from=p.get("valid_from", _iso(datetime.now(timezone.utc))),
                    valid_to=p.get("valid_to"),
                    superseded_by=p.get("superseded_by"),
                )
                db.add(row)
            await db.commit()

    _aio.get_event_loop().run_until_complete(_seed())
    return proj_id, syn_id, claim_ids


# ── Unit: helpers ─────────────────────────────────────────────────────


def test_current_claims_returns_only_open(client):
    """current_claims filters valid_to IS NULL and orders by valid_from."""
    base = datetime.now(timezone.utc)
    proj, syn, ids = _make_synapse_and_claims(client, [
        {"claim_text": "A (current)", "valid_from": _iso(base)},
        {
            "claim_text": "B (superseded)",
            "valid_from": _iso(base - timedelta(days=10)),
            "valid_to": _iso(base - timedelta(days=5)),
        },
        {
            "claim_text": "C (current later)",
            "valid_from": _iso(base + timedelta(seconds=1)),
        },
    ])

    import asyncio as _aio
    from database import async_session
    from services.synapse_claims_bitemporal import current_claims

    async def _go():
        async with async_session() as db:
            return await current_claims(db, syn)

    rows = _aio.get_event_loop().run_until_complete(_go())
    texts = [r.claim_text for r in rows]
    assert texts == ["A (current)", "C (current later)"]
    assert all(r.is_current for r in rows)


def test_claims_as_of_returns_snapshot(client):
    """claims_as_of(t) selects rows where valid_from <= t < valid_to (or open)."""
    base = datetime.now(timezone.utc)
    t_old = _iso(base - timedelta(days=30))
    t_mid = _iso(base - timedelta(days=15))
    t_now = _iso(base)

    proj, syn, _ = _make_synapse_and_claims(client, [
        # Always-valid claim
        {"claim_text": "Always", "valid_from": _iso(base - timedelta(days=60))},
        # Claim that was true days 60–20 ago, then superseded
        {
            "claim_text": "OldOnly",
            "valid_from": _iso(base - timedelta(days=60)),
            "valid_to": _iso(base - timedelta(days=20)),
        },
        # Claim that became true only 10 days ago
        {
            "claim_text": "Recent",
            "valid_from": _iso(base - timedelta(days=10)),
        },
    ])

    import asyncio as _aio
    from database import async_session
    from services.synapse_claims_bitemporal import claims_as_of

    async def _go(ts):
        async with async_session() as db:
            return await claims_as_of(db, syn, ts)

    rows_old = _aio.get_event_loop().run_until_complete(_go(t_old))
    texts_old = sorted(r.claim_text for r in rows_old)
    assert texts_old == ["Always", "OldOnly"]

    rows_mid = _aio.get_event_loop().run_until_complete(_go(t_mid))
    texts_mid = sorted(r.claim_text for r in rows_mid)
    # 15 days ago: OldOnly already superseded (20d ago), Recent not yet (10d ago)
    assert texts_mid == ["Always"]

    rows_now = _aio.get_event_loop().run_until_complete(_go(t_now))
    texts_now = sorted(r.claim_text for r in rows_now)
    assert texts_now == ["Always", "Recent"]


def test_supersede_claims_full_regen_closes_all_old(client):
    """No only_claim_ids → every current row closes + new rows insert with same ts."""
    proj, syn, ids = _make_synapse_and_claims(client, [
        {"claim_text": "Old 1"},
        {"claim_text": "Old 2"},
    ])

    import asyncio as _aio
    from database import async_session
    from models.synapse import SynapseClaim
    from services.synapse_claims_bitemporal import (
        current_claims, supersede_claims,
    )

    async def _go():
        async with async_session() as db:
            new_rows = [
                SynapseClaim(id=_gen_id(), synapse_id=syn, claim_text="New 1"),
                SynapseClaim(id=_gen_id(), synapse_id=syn, claim_text="New 2"),
                SynapseClaim(id=_gen_id(), synapse_id=syn, claim_text="New 3"),
            ]
            outcome = await supersede_claims(
                db, synapse_id=syn, new_claims=new_rows,
            )
            await db.commit()

            currents = await current_claims(db, syn)
            return outcome, currents

    outcome, currents = _aio.get_event_loop().run_until_complete(_go())
    assert outcome["superseded"] == 2
    assert outcome["added"] == 3
    current_texts = sorted(c.claim_text for c in currents)
    assert current_texts == ["New 1", "New 2", "New 3"]
    assert all(c.valid_to is None for c in currents)


def test_supersede_claims_partial_path_leaves_others_alone(client):
    """Targeting only_claim_ids closes those rows; untargeted current rows stay current."""
    keep_id = "keep_" + secrets.token_hex(3)
    repl_id = "repl_" + secrets.token_hex(3)
    proj, syn, ids = _make_synapse_and_claims(client, [
        {"claim_text": "Keep me", "id": keep_id},
        {"claim_text": "Replace me", "id": repl_id},
    ])

    import asyncio as _aio
    from database import async_session
    from models.synapse import SynapseClaim
    from services.synapse_claims_bitemporal import (
        current_claims, supersede_claims,
    )

    async def _go():
        async with async_session() as db:
            new_rows = [
                SynapseClaim(id=_gen_id(), synapse_id=syn, claim_text="Replacement"),
            ]
            outcome = await supersede_claims(
                db, synapse_id=syn, new_claims=new_rows,
                only_claim_ids=[repl_id],
            )
            await db.commit()
            currents = await current_claims(db, syn)
            return outcome, currents

    outcome, currents = _aio.get_event_loop().run_until_complete(_go())
    assert outcome["superseded"] == 1
    assert outcome["added"] == 1
    texts = sorted(c.claim_text for c in currents)
    assert texts == ["Keep me", "Replacement"]


def test_supersede_claim_by_id_blocks_double_supersede(client):
    """A claim that's already been closed can't be superseded again."""
    base = datetime.now(timezone.utc)
    old_id = "old_" + secrets.token_hex(3)
    proj, syn, ids = _make_synapse_and_claims(client, [
        {
            "claim_text": "Already superseded",
            "valid_from": _iso(base - timedelta(days=10)),
            "valid_to": _iso(base - timedelta(days=5)),
            "superseded_by": "ghost_id",
            "id": old_id,
        },
    ])

    import asyncio as _aio
    from database import async_session
    from models.synapse import SynapseClaim
    from services.synapse_claims_bitemporal import supersede_claim_by_id

    async def _go():
        async with async_session() as db:
            new = SynapseClaim(
                id=_gen_id(), synapse_id=syn, claim_text="Attempted replacement",
            )
            return await supersede_claim_by_id(
                db, old_claim_id=old_id, new_claim=new,
            )

    res = _aio.get_event_loop().run_until_complete(_go())
    assert res["error"] == "old_already_superseded"
    assert res["superseded"] == 0


def test_close_synapse_claims_idempotent(client):
    """Closing all current claims twice on the same synapse is a no-op the second time."""
    proj, syn, ids = _make_synapse_and_claims(client, [
        {"claim_text": "X"},
        {"claim_text": "Y"},
    ])

    import asyncio as _aio
    from database import async_session
    from services.synapse_claims_bitemporal import close_synapse_claims

    async def _go():
        async with async_session() as db:
            n1 = await close_synapse_claims(db, syn)
            await db.commit()
        async with async_session() as db:
            n2 = await close_synapse_claims(db, syn)
            await db.commit()
        return n1, n2

    n1, n2 = _aio.get_event_loop().run_until_complete(_go())
    assert n1 == 2
    assert n2 == 0


def test_is_current_property():
    """SynapseClaim.is_current mirrors valid_to is None."""
    from models.synapse import SynapseClaim

    c = SynapseClaim(id="x", synapse_id="y", claim_text="z")
    assert c.is_current is True
    c.valid_to = "2026-05-18T00:00:00+00:00"
    assert c.is_current is False


# ── API: GET /synapses/{id} ───────────────────────────────────────────


def test_synapse_detail_default_returns_current_only(client):
    """GET without query params filters to valid_to IS NULL."""
    base = datetime.now(timezone.utc)
    proj, syn, _ = _make_synapse_and_claims(client, [
        {"claim_text": "current claim"},
        {
            "claim_text": "old claim",
            "valid_from": _iso(base - timedelta(days=10)),
            "valid_to": _iso(base - timedelta(days=5)),
        },
    ])

    r = client.get(f"/api/synapse/{proj}/synapses/{syn}")
    assert r.status_code == 200
    data = r.json()
    texts = [c["claim_text"] for c in data["claims"]]
    assert texts == ["current claim"]
    assert data["claim_count"] == 1


def test_synapse_detail_as_of_returns_snapshot(client):
    """GET ?as_of=<midpoint> returns the claims valid at that instant."""
    base = datetime.now(timezone.utc)
    mid = base - timedelta(days=7)
    proj, syn, _ = _make_synapse_and_claims(client, [
        {
            "claim_text": "Once-true",
            "valid_from": _iso(base - timedelta(days=10)),
            "valid_to": _iso(base - timedelta(days=3)),
        },
        {
            "claim_text": "Current",
            "valid_from": _iso(base - timedelta(days=3)),
        },
    ])

    r = client.get(f"/api/synapse/{proj}/synapses/{syn}", params={"as_of": _iso(mid)})
    assert r.status_code == 200
    data = r.json()
    texts = [c["claim_text"] for c in data["claims"]]
    assert texts == ["Once-true"]
    # The response carries the bi-temporal fields
    assert data["claims"][0]["valid_to"] is not None
    assert data["claims"][0]["is_current"] is False


def test_synapse_detail_include_history_returns_all(client):
    """GET ?include_history=true returns every version, oldest first."""
    base = datetime.now(timezone.utc)
    proj, syn, _ = _make_synapse_and_claims(client, [
        {
            "claim_text": "v1",
            "valid_from": _iso(base - timedelta(days=10)),
            "valid_to": _iso(base - timedelta(days=5)),
        },
        {
            "claim_text": "v2",
            "valid_from": _iso(base - timedelta(days=5)),
        },
    ])
    r = client.get(
        f"/api/synapse/{proj}/synapses/{syn}", params={"include_history": "true"},
    )
    assert r.status_code == 200
    data = r.json()
    texts = [c["claim_text"] for c in data["claims"]]
    assert texts == ["v1", "v2"]


def test_synapse_list_claim_count_only_counts_current(client):
    """list_synapses' badge count excludes superseded versions."""
    base = datetime.now(timezone.utc)
    proj, syn, _ = _make_synapse_and_claims(client, [
        {"claim_text": "live"},
        {
            "claim_text": "history",
            "valid_to": _iso(base - timedelta(days=1)),
        },
        {
            "claim_text": "history2",
            "valid_to": _iso(base - timedelta(days=2)),
        },
    ])

    r = client.get(f"/api/synapse/{proj}/synapses")
    assert r.status_code == 200
    by_id = {s["id"]: s for s in r.json()}
    assert by_id[syn]["claim_count"] == 1
