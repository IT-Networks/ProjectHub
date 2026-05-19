"""Tests for the P9 incremental synapse-update layer.

Drives ``services/synapse_incremental.py`` directly on a fresh DB and
the router endpoint via FastAPI's TestClient. The LLM decider is faked.

Coverage:

* find_affected_synapses returns only synapses that reference item_id
* find_affected_synapses guards against LIKE-substring false positives
* _claims_touching_item filters claims by evidence.item_id (current only)
* decide_for_synapse rule-based: deleted/sole-source → DELETE
* decide_for_synapse rule-based: deleted/multi-source → UPDATE
* decide_for_synapse rule-based: updated → UPDATE
* decide_for_synapse rule-based: created → NOOP
* decide_for_synapse LLM path: valid response wins over rule
* decide_for_synapse LLM path: bad response falls back to rule
* apply_decision DELETE: closes claims + sets status=stale
* apply_decision UPDATE: closes affected claims + sets status=pending_validation
* apply_decision NOOP: no DB change
* update_for_item orchestrator: deleted item → DELETE on sole-source synapse
* update_for_item: created item with no affected synapses → ADD intent
* POST /api/synapse/{proj}/incremental returns 503 when flag is off
* POST /api/synapse/{proj}/incremental returns 200 + breakdown when on
"""
from __future__ import annotations

import asyncio
import os
import secrets
import tempfile
from datetime import datetime, timezone

import pytest


_TEST_DB_PATH = os.path.join(
    tempfile.gettempdir(),
    f"projecthub_pytest_p9_{secrets.token_hex(4)}.db",
)
os.environ["PROJECTHUB_DB_PATH"] = _TEST_DB_PATH


def _gen_id() -> str:
    return secrets.token_hex(8)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


@pytest.fixture(scope="module")
def client():
    """Fresh FastAPI app + DB per module."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    try:
        if os.path.exists(_TEST_DB_PATH):
            os.unlink(_TEST_DB_PATH)
    except OSError:
        pass

    import models  # noqa: F401 — registers Base subclasses
    from database import init_db
    from routers.projects import router as projects_router
    from routers.synapse import router as synapse_router
    from routers.knowledge import router as knowledge_router

    asyncio.get_event_loop().run_until_complete(init_db())

    app = FastAPI()
    app.include_router(projects_router)
    app.include_router(synapse_router)
    app.include_router(knowledge_router)
    with TestClient(app) as c:
        yield c


def _seed(claims_by_synapse: list[dict]) -> dict:
    """Seed project + N synapses + claims via SQLAlchemy.

    ``claims_by_synapse`` is a list of:
        {
            "source_item_ids": [...],
            "claims": [{"text": ..., "evidence_item_ids": [...]}],
        }

    Returns: {"project_id": ..., "synapse_ids": [...], "item_ids": [...]}.
    """
    from database import async_session
    from models import Project
    from models.knowledge import KnowledgeItem
    from models.synapse import Synapse, SynapseClaim

    proj_id = _gen_id()
    syn_ids = []
    all_items: dict[str, KnowledgeItem] = {}

    async def _go():
        async with async_session() as db:
            db.add(Project(id=proj_id, name=f"P9 Test {proj_id[:6]}", description="incr"))
            for spec in claims_by_synapse:
                for iid in spec["source_item_ids"]:
                    if iid not in all_items:
                        all_items[iid] = KnowledgeItem(
                            id=iid, project_id=proj_id,
                            title=f"Item {iid}",
                            content=f"content for {iid}",
                            content_plain=f"content for {iid}",
                        )
                        db.add(all_items[iid])
                syn_id = _gen_id()
                syn_ids.append(syn_id)
                syn = Synapse(
                    id=syn_id, project_id=proj_id,
                    title=spec.get("title", f"Syn {syn_id[:6]}"),
                    summary="x", summary_plain=spec.get("summary", "x"),
                    confidence=0.8, confidence_band="high",
                    verdict="persist", status="validated",
                )
                syn.source_item_ids_list = list(spec["source_item_ids"])
                db.add(syn)
                for cl in spec.get("claims", []):
                    claim = SynapseClaim(
                        id=_gen_id(), synapse_id=syn_id,
                        claim_text=cl["text"],
                        relation="supported",
                    )
                    claim.evidence_list = [
                        {"item_id": iid, "span": "."}
                        for iid in cl.get("evidence_item_ids", [])
                    ]
                    db.add(claim)
            await db.commit()

    asyncio.get_event_loop().run_until_complete(_go())
    return {
        "project_id": proj_id,
        "synapse_ids": syn_ids,
        "item_ids": list(all_items.keys()),
    }


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── find_affected_synapses ───────────────────────────────────────────


def test_find_affected_returns_only_matching(client):
    state = _seed([
        {"source_item_ids": ["itemA001", "itemA002"]},
        {"source_item_ids": ["itemB001"]},
    ])

    async def _go():
        from database import async_session
        from services.synapse_incremental import find_affected_synapses
        async with async_session() as db:
            return await find_affected_synapses(
                db, project_id=state["project_id"], item_id="itemA001",
            )

    rows = _run(_go())
    ids = [r.id for r in rows]
    assert ids == [state["synapse_ids"][0]]


def test_find_affected_resists_substring_false_positives(client):
    """Item id 'abc' would LIKE-match into 'abcdef' — JSON membership check
    must keep that out."""
    state = _seed([
        {"source_item_ids": ["abcdef0001"]},  # would be matched by LIKE for "abc"
    ])

    async def _go():
        from database import async_session
        from services.synapse_incremental import find_affected_synapses
        async with async_session() as db:
            return await find_affected_synapses(
                db, project_id=state["project_id"], item_id="abc",
            )

    rows = _run(_go())
    assert rows == []


# ── decide_for_synapse ──────────────────────────────────────────────


def test_decide_rule_delete_sole_source(client):
    state = _seed([
        {
            "source_item_ids": ["lonely001"],
            "claims": [{"text": "x", "evidence_item_ids": ["lonely001"]}],
        },
    ])

    async def _go():
        from database import async_session
        from models.synapse import Synapse
        from services.synapse_incremental import decide_for_synapse
        async with async_session() as db:
            syn = await db.get(Synapse, state["synapse_ids"][0])
            return await decide_for_synapse(
                db, syn, None, change="deleted",
            )

    d = _run(_go())
    assert d.kind == "DELETE"
    assert d.source == "rule"


def test_decide_rule_update_multi_source(client):
    state = _seed([
        {
            "source_item_ids": ["multi0001", "multi0002"],
            "claims": [
                {"text": "claim1", "evidence_item_ids": ["multi0001"]},
                {"text": "claim2", "evidence_item_ids": ["multi0002"]},
            ],
        },
    ])

    async def _go():
        from database import async_session
        from models.knowledge import KnowledgeItem
        from models.synapse import Synapse
        from services.synapse_incremental import decide_for_synapse
        async with async_session() as db:
            syn = await db.get(Synapse, state["synapse_ids"][0])
            item = await db.get(KnowledgeItem, "multi0001")
            return await decide_for_synapse(db, syn, item, change="updated")

    d = _run(_go())
    assert d.kind == "UPDATE"
    # Exactly one of the two claims references multi0001
    assert len(d.affected_claim_ids) == 1


def test_decide_rule_created_returns_noop(client):
    state = _seed([
        {"source_item_ids": ["nrm0001"], "claims": []},
    ])

    async def _go():
        from database import async_session
        from models.knowledge import KnowledgeItem
        from models.synapse import Synapse
        from services.synapse_incremental import decide_for_synapse
        async with async_session() as db:
            syn = await db.get(Synapse, state["synapse_ids"][0])
            item = await db.get(KnowledgeItem, "nrm0001")
            return await decide_for_synapse(db, syn, item, change="created")

    d = _run(_go())
    assert d.kind == "NOOP"


def test_decide_llm_path_wins_when_valid(client):
    state = _seed([
        {
            "source_item_ids": ["llm0001", "llm0002"],
            "claims": [{"text": "x", "evidence_item_ids": ["llm0001"]}],
        },
    ])

    async def fake_llm(prompt: str):
        return {"decision": "DELETE", "reason": "LLM says abandon"}

    async def _go():
        from database import async_session
        from models.knowledge import KnowledgeItem
        from models.synapse import Synapse
        from services.synapse_incremental import decide_for_synapse
        async with async_session() as db:
            syn = await db.get(Synapse, state["synapse_ids"][0])
            item = await db.get(KnowledgeItem, "llm0001")
            return await decide_for_synapse(
                db, syn, item, change="updated", llm_caller=fake_llm,
            )

    d = _run(_go())
    assert d.kind == "DELETE"  # LLM overrides UPDATE rule
    assert d.source == "llm"
    assert "LLM says abandon" in d.reason


def test_decide_llm_path_falls_back_when_bad(client):
    state = _seed([
        {
            "source_item_ids": ["llm0003"],
            "claims": [{"text": "x", "evidence_item_ids": ["llm0003"]}],
        },
    ])

    async def broken_llm(prompt: str):
        return {"not_a_decision": "garbage"}

    async def _go():
        from database import async_session
        from models.knowledge import KnowledgeItem
        from models.synapse import Synapse
        from services.synapse_incremental import decide_for_synapse
        async with async_session() as db:
            syn = await db.get(Synapse, state["synapse_ids"][0])
            item = await db.get(KnowledgeItem, "llm0003")
            return await decide_for_synapse(
                db, syn, item, change="updated", llm_caller=broken_llm,
            )

    d = _run(_go())
    # Rule for "updated" is UPDATE — must survive a bad LLM call
    assert d.kind == "UPDATE"
    assert d.source == "rule"


def test_decide_llm_path_swallows_exceptions(client):
    state = _seed([
        {"source_item_ids": ["lllm0001"], "claims": []},
    ])

    async def raising_llm(prompt: str):
        raise RuntimeError("network down")

    async def _go():
        from database import async_session
        from models.synapse import Synapse
        from services.synapse_incremental import decide_for_synapse
        async with async_session() as db:
            syn = await db.get(Synapse, state["synapse_ids"][0])
            return await decide_for_synapse(
                db, syn, None, change="deleted", llm_caller=raising_llm,
            )

    d = _run(_go())
    # Crashing LLM → rule fallback (DELETE for sole-source)
    assert d.kind == "DELETE"


# ── apply_decision ──────────────────────────────────────────────────


def test_apply_decision_delete_closes_claims_and_marks_stale(client):
    state = _seed([
        {
            "source_item_ids": ["del0001"],
            "claims": [
                {"text": "a", "evidence_item_ids": ["del0001"]},
                {"text": "b", "evidence_item_ids": ["del0001"]},
            ],
        },
    ])

    async def _go():
        from database import async_session
        from models.synapse import Synapse, SynapseClaim
        from services.synapse_incremental import (
            IncrementalDecision, apply_decision,
        )
        from sqlalchemy import select
        async with async_session() as db:
            d = IncrementalDecision(
                kind="DELETE", synapse_id=state["synapse_ids"][0],
                reason="test",
            )
            res = await apply_decision(db, d)
            await db.commit()

            current = await db.execute(
                select(SynapseClaim)
                .where(SynapseClaim.synapse_id == state["synapse_ids"][0])
                .where(SynapseClaim.valid_to.is_(None))
            )
            still_current = list(current.scalars().all())
            syn = await db.get(Synapse, state["synapse_ids"][0])
            return res, still_current, syn

    res, still_current, syn = _run(_go())
    assert res["action"] == "delete"
    assert res["closed_claims"] == 2
    assert still_current == []
    assert syn.status == "stale"


def test_apply_decision_update_closes_only_targeted_claims(client):
    state = _seed([
        {
            "source_item_ids": ["upd0001", "upd0002"],
            "claims": [
                {"text": "touches upd1", "evidence_item_ids": ["upd0001"]},
                {"text": "touches upd2", "evidence_item_ids": ["upd0002"]},
            ],
        },
    ])

    async def _go():
        from database import async_session
        from models.synapse import Synapse, SynapseClaim
        from services.synapse_incremental import (
            IncrementalDecision, apply_decision, _claims_touching_item,
        )
        from sqlalchemy import select
        async with async_session() as db:
            affected = await _claims_touching_item(
                db, synapse_id=state["synapse_ids"][0], item_id="upd0001",
            )
            d = IncrementalDecision(
                kind="UPDATE", synapse_id=state["synapse_ids"][0],
                reason="test", affected_claim_ids=[c.id for c in affected],
            )
            await apply_decision(db, d)
            await db.commit()

            still_current = await db.execute(
                select(SynapseClaim)
                .where(SynapseClaim.synapse_id == state["synapse_ids"][0])
                .where(SynapseClaim.valid_to.is_(None))
            )
            current_texts = sorted(c.claim_text for c in still_current.scalars().all())
            syn = await db.get(Synapse, state["synapse_ids"][0])
            return current_texts, syn

    current_texts, syn = _run(_go())
    # The "upd2" claim stays current; the "upd1" claim is closed
    assert current_texts == ["touches upd2"]
    assert syn.status == "pending_validation"


def test_apply_decision_noop(client):
    state = _seed([
        {"source_item_ids": ["nop0001"], "claims": []},
    ])

    async def _go():
        from database import async_session
        from services.synapse_incremental import (
            IncrementalDecision, apply_decision,
        )
        async with async_session() as db:
            d = IncrementalDecision(
                kind="NOOP", synapse_id=state["synapse_ids"][0],
                reason="test",
            )
            return await apply_decision(db, d)

    res = _run(_go())
    assert res["action"] == "noop"


# ── update_for_item orchestrator ────────────────────────────────────


def test_update_for_item_deletes_sole_source_synapse(client):
    state = _seed([
        {
            "source_item_ids": ["orchd001"],
            "claims": [{"text": "x", "evidence_item_ids": ["orchd001"]}],
        },
    ])

    async def _go():
        from database import async_session
        from models.synapse import Synapse
        from services.synapse_incremental import update_for_item
        async with async_session() as db:
            res = await update_for_item(
                db, project_id=state["project_id"],
                item_id="orchd001", change="deleted",
            )
            syn = await db.get(Synapse, state["synapse_ids"][0])
            return res, syn

    res, syn = _run(_go())
    assert res["affected"] == 1
    assert res["decisions"][0]["kind"] == "DELETE"
    assert syn.status == "stale"


def test_update_for_item_created_with_no_match_emits_add_intent(client):
    state = _seed([
        {"source_item_ids": ["other001"]},
    ])

    async def _go():
        from database import async_session
        from models.knowledge import KnowledgeItem
        from services.synapse_incremental import update_for_item
        async with async_session() as db:
            db.add(KnowledgeItem(
                id="orphan001", project_id=state["project_id"],
                title="orphan", content="x", content_plain="x",
            ))
            await db.commit()
            return await update_for_item(
                db, project_id=state["project_id"],
                item_id="orphan001", change="created",
            )

    res = _run(_go())
    assert res["affected"] == 0
    # Exactly one ADD intent surfaced
    kinds = [d["kind"] for d in res["decisions"]]
    assert "ADD" in kinds


# ── /incremental endpoint ──────────────────────────────────────────


def test_incremental_endpoint_503_when_flag_off(client):
    state = _seed([
        {"source_item_ids": ["api0001"], "claims": []},
    ])
    # Default config has the flag OFF
    r = client.post(
        f"/api/synapse/{state['project_id']}/incremental",
        json={"item_id": "api0001", "change": "updated"},
    )
    assert r.status_code == 503
    assert "deaktiviert" in r.text.lower()


def test_incremental_endpoint_runs_when_flag_on(client, monkeypatch):
    state = _seed([
        {
            "source_item_ids": ["api0002"],
            "claims": [{"text": "x", "evidence_item_ids": ["api0002"]}],
        },
    ])

    import config as cfg_mod
    monkeypatch.setattr(
        cfg_mod.settings, "brain_incremental_update_enabled", True,
        raising=True,
    )

    r = client.post(
        f"/api/synapse/{state['project_id']}/incremental",
        json={"item_id": "api0002", "change": "deleted"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["affected"] == 1
    assert data["decisions"][0]["kind"] == "DELETE"
    assert data["results"][0]["action"] == "delete"


def test_incremental_endpoint_rejects_bad_change(client, monkeypatch):
    state = _seed([{"source_item_ids": ["api0003"], "claims": []}])
    import config as cfg_mod
    monkeypatch.setattr(
        cfg_mod.settings, "brain_incremental_update_enabled", True,
        raising=True,
    )
    r = client.post(
        f"/api/synapse/{state['project_id']}/incremental",
        json={"item_id": "api0003", "change": "wibble"},
    )
    assert r.status_code == 400


# ── P9 ADD-closure: entity-overlap discovery (v1.10) ────────────────


def _seed_with_entities(claims_by_synapse: list[dict], extra_items: list[dict] | None = None) -> dict:
    """Like _seed, but also seeds KnowledgeEntity rows + Synapse.source_entity_ids.

    Each spec adds an ``entities`` list of (id, name, name_normalized)
    triples; the Synapse's source_entity_ids gets those entity ids.
    """
    from database import async_session
    from models import Project
    from models.knowledge import KnowledgeItem
    from models.synapse import KnowledgeEntity, Synapse

    proj_id = _gen_id()
    syn_ids = []
    extra_items = extra_items or []

    async def _go():
        async with async_session() as db:
            db.add(Project(id=proj_id, name=f"P9-ent {proj_id[:6]}", description="t"))

            for spec in claims_by_synapse:
                # Create source items
                for iid in spec.get("source_item_ids", []):
                    db.add(KnowledgeItem(
                        id=iid, project_id=proj_id,
                        title=f"I {iid}", content="", content_plain="",
                    ))
                # Create entities
                entity_ids = []
                for ent in spec.get("entities", []):
                    db.add(KnowledgeEntity(
                        id=ent["id"], project_id=proj_id,
                        name=ent["name"],
                        name_normalized=ent["name_normalized"],
                        entity_type=ent.get("type", "concept"),
                    ))
                    entity_ids.append(ent["id"])
                # Create synapse
                syn_id = _gen_id()
                syn_ids.append(syn_id)
                syn = Synapse(
                    id=syn_id, project_id=proj_id,
                    title=spec.get("title", f"S {syn_id[:6]}"),
                    summary="x", summary_plain="x",
                    confidence=0.8, confidence_band="high",
                    verdict="persist", status="validated",
                )
                syn.source_item_ids_list = list(spec.get("source_item_ids", []))
                syn.source_entity_ids_list = entity_ids
                db.add(syn)

            for it in extra_items:
                db.add(KnowledgeItem(
                    id=it["id"], project_id=proj_id,
                    title=it.get("title", ""),
                    content=it.get("content", ""),
                    content_plain=it.get("content_plain", it.get("content", "")),
                    category=it.get("category", "reference"),
                ))
            await db.commit()

    asyncio.get_event_loop().run_until_complete(_go())
    return {"project_id": proj_id, "synapse_ids": syn_ids}


def test_entity_overlap_finds_synapse_by_extracted_name(client):
    """The new item is created in isolation but talks about an entity
    that an existing synapse already references → overlap path picks it up."""
    src_iid = "ent_" + secrets.token_hex(3)
    new_iid = "ent_" + secrets.token_hex(3)
    state = _seed_with_entities(
        [
            {
                "source_item_ids": [src_iid],
                "entities": [
                    {
                        "id": "ent_" + secrets.token_hex(3),
                        "name": "OAuth2",
                        "name_normalized": "oauth2",
                        "type": "technology",
                    },
                ],
            },
        ],
        extra_items=[
            {
                "id": new_iid,
                "title": "Auth setup notes",
                "content_plain": "We extended OAuth2 for SSO.",
            },
        ],
    )

    async def fake_llm(prompt: str):
        # The overlap path expects a {"entities": [...]} response.
        return {"entities": [
            {"name": "OAuth2", "type": "technology"},
        ]}

    async def _go():
        from database import async_session
        from models.knowledge import KnowledgeItem
        from services.synapse_incremental import find_affected_via_entity_overlap
        async with async_session() as db:
            item = await db.get(KnowledgeItem, new_iid)
            return await find_affected_via_entity_overlap(
                db, project_id=state["project_id"], item=item,
                llm_caller=fake_llm,
            )

    rows = _run(_go())
    assert len(rows) == 1
    assert rows[0].id == state["synapse_ids"][0]


def test_entity_overlap_returns_empty_when_no_match(client):
    """Extracted entities exist but no synapse references them."""
    other_iid = "iso_" + secrets.token_hex(3)
    new_iid = "iso_" + secrets.token_hex(3)
    state = _seed_with_entities(
        [
            {
                "source_item_ids": [other_iid],
                "entities": [
                    {
                        "id": "ent_" + secrets.token_hex(3),
                        "name": "Docker",
                        "name_normalized": "docker",
                        "type": "technology",
                    },
                ],
            },
        ],
        extra_items=[
            {
                "id": new_iid,
                "title": "About Kubernetes",
                "content_plain": "Kubernetes orchestration notes.",
            },
        ],
    )

    async def fake_llm(prompt: str):
        return {"entities": [{"name": "Kubernetes", "type": "technology"}]}

    async def _go():
        from database import async_session
        from models.knowledge import KnowledgeItem
        from services.synapse_incremental import find_affected_via_entity_overlap
        async with async_session() as db:
            item = await db.get(KnowledgeItem, new_iid)
            return await find_affected_via_entity_overlap(
                db, project_id=state["project_id"], item=item,
                llm_caller=fake_llm,
            )

    rows = _run(_go())
    assert rows == []


def test_entity_overlap_handles_empty_content(client):
    """Item with no text → no LLM call, empty result."""
    blank_iid = "blank_" + secrets.token_hex(3)
    state = _seed_with_entities(
        [],
        extra_items=[
            {"id": blank_iid, "content_plain": "   "},
        ],
    )

    called = {"n": 0}
    async def tracking_llm(prompt: str):
        called["n"] += 1
        return {"entities": []}

    async def _go():
        from database import async_session
        from models.knowledge import KnowledgeItem
        from services.synapse_incremental import find_affected_via_entity_overlap
        async with async_session() as db:
            item = await db.get(KnowledgeItem, blank_iid)
            return await find_affected_via_entity_overlap(
                db, project_id=state["project_id"], item=item,
                llm_caller=tracking_llm,
            )

    rows = _run(_go())
    assert rows == []
    assert called["n"] == 0  # short-circuit before LLM call


def test_entity_overlap_swallows_llm_failure(client):
    boom_iid = "boom_" + secrets.token_hex(3)
    state = _seed_with_entities(
        [],
        extra_items=[{"id": boom_iid, "content_plain": "Some text."}],
    )

    async def bad_llm(prompt: str):
        raise RuntimeError("LLM down")

    async def _go():
        from database import async_session
        from models.knowledge import KnowledgeItem
        from services.synapse_incremental import find_affected_via_entity_overlap
        async with async_session() as db:
            item = await db.get(KnowledgeItem, boom_iid)
            return await find_affected_via_entity_overlap(
                db, project_id=state["project_id"], item=item,
                llm_caller=bad_llm,
            )

    rows = _run(_go())
    assert rows == []


def test_orchestrator_promotes_entity_overlap_to_update(client):
    """End-to-end: 'created' + no direct match + LLM provided → entity
    overlap path runs, picks the synapse, decisions = UPDATE."""
    src_iid = "or_" + secrets.token_hex(3)
    new_iid = "or_" + secrets.token_hex(3)
    ent_iid = "ent_" + secrets.token_hex(3)
    state = _seed_with_entities(
        [
            {
                "source_item_ids": [src_iid],
                "entities": [
                    {
                        "id": ent_iid,
                        "name": "JWT",
                        "name_normalized": "jwt",
                        "type": "technology",
                    },
                ],
            },
        ],
        extra_items=[
            {
                "id": new_iid,
                "title": "JWT migration notes",
                "content_plain": "Migrated to JWT tokens.",
            },
        ],
    )

    async def fake_llm(prompt: str):
        # Heuristic dispatch: the extraction prompt vs the decider prompt
        # differ — extraction asks for "entities", decider for "decision".
        if "JSON" in prompt and "\"entities\"" in prompt:
            return {"entities": [{"name": "JWT", "type": "technology"}]}
        # Decider prompt
        return {"decision": "UPDATE", "reason": "new item supplements"}

    async def _go():
        from database import async_session
        from services.synapse_incremental import update_for_item
        async with async_session() as db:
            return await update_for_item(
                db, project_id=state["project_id"], item_id=new_iid,
                change="created", llm_caller=fake_llm,
            )

    res = _run(_go())
    # One synapse picked up via entity overlap
    assert res["affected"] == 1
    kinds = [d["kind"] for d in res["decisions"]]
    assert "UPDATE" in kinds
    # And the reason carries the entity-overlap annotation
    reasons = " ".join(d["reason"] for d in res["decisions"])
    assert "entity-overlap" in reasons
    # No ADD-pending intent — overlap path handled it
    assert "ADD" not in kinds


def test_orchestrator_without_llm_falls_through_to_add(client):
    """Same setup but no llm_caller → entity overlap is skipped → ADD intent."""
    src_iid = "or2_" + secrets.token_hex(3)
    new_iid = "or2_" + secrets.token_hex(3)
    state = _seed_with_entities(
        [
            {
                "source_item_ids": [src_iid],
                "entities": [
                    {
                        "id": "ent_" + secrets.token_hex(3),
                        "name": "Redis",
                        "name_normalized": "redis",
                        "type": "technology",
                    },
                ],
            },
        ],
        extra_items=[
            {
                "id": new_iid, "title": "Redis intro",
                "content_plain": "Using Redis for caching.",
            },
        ],
    )

    async def _go():
        from database import async_session
        from services.synapse_incremental import update_for_item
        async with async_session() as db:
            return await update_for_item(
                db, project_id=state["project_id"], item_id=new_iid,
                change="created", llm_caller=None,
            )

    res = _run(_go())
    assert res["affected"] == 0
    kinds = [d["kind"] for d in res["decisions"]]
    assert kinds == ["ADD"]
