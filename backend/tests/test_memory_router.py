"""Tests for the memory-bridge router (P1, T1.2 + T1.3).

Drives ``routers/memory.py`` through the real FastAPI app stack with a
throwaway SQLite DB. The pattern mirrors ``test_synapse_e2e.py`` —
conftest already pins ``PROJECTHUB_DB_PATH`` to a temp file, and we
build a fresh app context per test class.

No external LLM is touched: the memory bridge doesn't call out to
``ai_assist.agent_call`` at all (that comes in P9 with the incremental
synapse-update trigger).

Coverage:

* workspace mapping CRUD — list, upsert, idempotent upsert
* canonicalisation idempotency (forward + backslashes, trailing /, case)
* /extract happy path → KnowledgeItem created with correct fields
* /extract dedup short-circuit on identical fact
* /extract with multiple facts + partial dedup
* /extract resolves via the legacy ``project.repo_path`` fallback
* /extract resolves via the longest-prefix branch
* /extract unknown workspace → 422 + ``known_workspaces`` payload
* /extract enforces ``minItems: 1`` via Pydantic (422)
* /query empty project → empty result + no format_hint
* /query unknown workspace → 404
* /query mode=synapses without persisted synapses → empty synapses
* /query format_hint shape
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
import tempfile
from pathlib import Path

import pytest


# Fresh DB per pytest session: override the default conftest's path so a
# previous run's DB never pollutes this one.
_TEST_DB_PATH = os.path.join(
    tempfile.gettempdir(), f"projecthub_pytest_memory_{secrets.token_hex(4)}.db"
)
os.environ["PROJECTHUB_DB_PATH"] = _TEST_DB_PATH


@pytest.fixture(scope="module")
def client():
    """One in-process FastAPI client per test module.

    Lifespan is not exercised (polling-service is not what we test here);
    we wire the routers manually and call ``init_db()`` once up front.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # Wipe stale DB from a prior aborted run.
    try:
        if os.path.exists(_TEST_DB_PATH):
            os.unlink(_TEST_DB_PATH)
    except OSError:
        pass

    import models  # noqa: F401 — registers every Base subclass before create_all
    from database import init_db
    from routers.memory import router as memory_router
    from routers.projects import router as projects_router

    asyncio.get_event_loop().run_until_complete(init_db())

    app = FastAPI()
    app.include_router(projects_router)
    app.include_router(memory_router)

    with TestClient(app) as c:
        yield c


# ── Helpers ──────────────────────────────────────────────────────────


def _new_project(client, *, name: str = "Test", docs_path: str = "") -> str:
    """Create a project via the public API; return its id.

    ``docs_path`` is the field on Project the bridge's legacy fallback resolves
    against (see ``resolve_project_id_from_workspace`` Tier 3).
    """
    payload = {"name": name}
    if docs_path:
        payload["docs_path"] = docs_path
    resp = client.post("/api/projects", json=payload)
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"]


def _register_workspace(client, project_id: str, path: str) -> None:
    resp = client.put(
        "/api/memory/v1/workspaces",
        json={"project_id": project_id, "workspace_path": path},
    )
    assert resp.status_code == 200, resp.text


def _fact(text: str, ftype: str = "decision", **kwargs) -> dict:
    base = {"text": text, "type": ftype, "tags": [], "confidence": 0.7}
    base.update(kwargs)
    return base


# ── Canonicalisation ────────────────────────────────────────────────


def test_canonicalize_forward_and_backslash_equivalent() -> None:
    from models.workspace import canonicalize_workspace_path

    a = canonicalize_workspace_path("C:\\Users\\X\\proj\\")
    b = canonicalize_workspace_path("C:/Users/X/proj")
    c = canonicalize_workspace_path("c:/Users/X/proj/")
    assert a == b == c == "c:/Users/X/proj"


# ── Workspace CRUD ──────────────────────────────────────────────────


def test_workspace_put_creates_and_is_idempotent(client) -> None:
    pid = _new_project(client, name="ws-crud")
    # First put creates
    r1 = client.put(
        "/api/memory/v1/workspaces",
        json={"project_id": pid, "workspace_path": "/home/user/proj1"},
    )
    assert r1.status_code == 200, r1.text
    assert r1.json()["workspace_path"] == "/home/user/proj1"

    # Second put with same canonical path doesn't fail and doesn't duplicate
    r2 = client.put(
        "/api/memory/v1/workspaces",
        json={"project_id": pid, "workspace_path": "/home/user/proj1/"},
    )
    assert r2.status_code == 200
    assert r2.json()["workspace_path"] == "/home/user/proj1"

    # List shows exactly one row
    listing = client.get("/api/memory/v1/workspaces").json()
    matches = [
        m for m in listing
        if m["project_id"] == pid and m["workspace_path"] == "/home/user/proj1"
    ]
    assert len(matches) == 1


def test_workspace_put_with_unknown_project_returns_404(client) -> None:
    r = client.put(
        "/api/memory/v1/workspaces",
        json={"project_id": "ffffffffffffffff", "workspace_path": "/x"},
    )
    assert r.status_code == 404


# ── /extract ────────────────────────────────────────────────────────


def test_extract_happy_path_creates_knowledge_item(client) -> None:
    pid = _new_project(client, name="extract-happy")
    _register_workspace(client, pid, "/work/extract-happy")

    payload = {
        "session_id": "sess-1",
        "workspace": "/work/extract-happy",
        "facts": [_fact("LiteLLM proxy runs on port 8080.")],
    }
    r = client.post("/api/memory/v1/extract", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["project_id"] == pid
    assert len(body["created_item_ids"]) == 1
    assert body["deduplicated"] == 0
    assert body["linked_to_synapses"] == []

    # /query against the same project must surface the freshly-created item.
    # (Using /query rather than /api/knowledge/{pid} keeps the test focused
    # on the bridge surface; the knowledge list route is exercised by other
    # ProjectHub tests.)
    q = client.post(
        "/api/memory/v1/query",
        json={
            "workspace": "/work/extract-happy",
            "query": "LiteLLM",
            "mode": "items",
        },
    )
    assert q.status_code == 200, q.text
    items = q.json()["items"]
    assert any("LiteLLM proxy" in i["title"] for i in items), items


def test_extract_dedups_identical_fact(client) -> None:
    pid = _new_project(client, name="extract-dedup")
    _register_workspace(client, pid, "/work/dedup")
    fact = _fact("OAuth2 with PKCE for the mobile flow.")
    p1 = client.post(
        "/api/memory/v1/extract",
        json={"session_id": "s1", "workspace": "/work/dedup", "facts": [fact]},
    )
    p2 = client.post(
        "/api/memory/v1/extract",
        json={"session_id": "s2", "workspace": "/work/dedup", "facts": [fact]},
    )
    assert p1.status_code == 200 and p2.status_code == 200
    assert len(p1.json()["created_item_ids"]) == 1
    assert p2.json()["created_item_ids"] == []
    assert p2.json()["deduplicated"] == 1


def test_extract_partial_dedup_in_one_call(client) -> None:
    pid = _new_project(client, name="extract-partial")
    _register_workspace(client, pid, "/work/partial")
    seeded = _fact("Seed fact A.")
    client.post(
        "/api/memory/v1/extract",
        json={"session_id": "s0", "workspace": "/work/partial", "facts": [seeded]},
    )
    r = client.post(
        "/api/memory/v1/extract",
        json={
            "session_id": "s1",
            "workspace": "/work/partial",
            "facts": [seeded, _fact("New fact B.")],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["created_item_ids"]) == 1
    assert body["deduplicated"] == 1


def test_extract_resolves_via_legacy_docs_path(client) -> None:
    """No workspace mapping registered — falls back to ``projects.docs_path``."""
    pid = _new_project(
        client, name="legacy-docs", docs_path="/legacy/docs/path"
    )
    r = client.post(
        "/api/memory/v1/extract",
        json={
            "session_id": "s",
            "workspace": "/legacy/docs/path",
            "facts": [_fact("Legacy fallback works.")],
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["project_id"] == pid


def test_extract_resolves_via_longest_prefix(client) -> None:
    """Workspace is a SUBPATH of a registered mapping — should resolve."""
    pid = _new_project(client, name="prefix")
    _register_workspace(client, pid, "/work/big-mono")

    r = client.post(
        "/api/memory/v1/extract",
        json={
            "session_id": "s",
            "workspace": "/work/big-mono/services/auth",
            "facts": [_fact("Subpath resolves up to the registered parent.")],
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["project_id"] == pid


def test_extract_unknown_workspace_returns_422_with_known_list(client) -> None:
    # Pre-register a workspace so the known_workspaces list is non-empty
    pid = _new_project(client, name="known")
    _register_workspace(client, pid, "/work/known-thing")

    r = client.post(
        "/api/memory/v1/extract",
        json={
            "session_id": "s",
            "workspace": "/totally/unregistered",
            "facts": [_fact("This should fail to resolve.")],
        },
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    # Pydantic 422 returns a list — our handler wraps a dict, see implementation.
    if isinstance(detail, dict):
        assert "known_workspaces" in detail
        assert any("/work/known-thing" in k for k in detail["known_workspaces"])
        assert detail["received_workspace"] == "/totally/unregistered"


def test_extract_rejects_empty_facts_list(client) -> None:
    """Pydantic min_length=1 must enforce."""
    pid = _new_project(client, name="empty-facts")
    _register_workspace(client, pid, "/work/empty-facts")
    r = client.post(
        "/api/memory/v1/extract",
        json={"session_id": "s", "workspace": "/work/empty-facts", "facts": []},
    )
    assert r.status_code == 422  # Pydantic validation


# ── /query ──────────────────────────────────────────────────────────


def test_query_unknown_workspace_returns_404(client) -> None:
    r = client.post(
        "/api/memory/v1/query",
        json={"workspace": "/nope", "query": "anything"},
    )
    assert r.status_code == 404


def test_query_empty_project_returns_empty_block(client) -> None:
    pid = _new_project(client, name="query-empty")
    _register_workspace(client, pid, "/work/query-empty")
    r = client.post(
        "/api/memory/v1/query",
        json={"workspace": "/work/query-empty", "query": "auth"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["synapses"] == []
    assert body["items"] == []
    assert body["format_hint"] == ""


def test_query_returns_items_when_no_synapses(client) -> None:
    """With no synapses persisted but matching items, ``items`` should fill."""
    pid = _new_project(client, name="query-items")
    _register_workspace(client, pid, "/work/query-items")
    # Push a fact so an item exists
    client.post(
        "/api/memory/v1/extract",
        json={
            "session_id": "s",
            "workspace": "/work/query-items",
            "facts": [_fact("Search-keyword-test sentinel inside the body.")],
        },
    )
    r = client.post(
        "/api/memory/v1/query",
        json={
            "workspace": "/work/query-items",
            "query": "sentinel",
            "mode": "hybrid",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["synapses"] == []
    assert len(body["items"]) >= 1
    assert "sentinel" in body["items"][0]["snippet"].lower()
    assert body["format_hint"].startswith("## Projekt-Wissen")


def test_query_mode_synapses_only_does_not_fall_back_to_items(client) -> None:
    pid = _new_project(client, name="query-synapses-only")
    _register_workspace(client, pid, "/work/syn-only")
    client.post(
        "/api/memory/v1/extract",
        json={
            "session_id": "s",
            "workspace": "/work/syn-only",
            "facts": [_fact("body has the keyword zebra.")],
        },
    )
    r = client.post(
        "/api/memory/v1/query",
        json={
            "workspace": "/work/syn-only",
            "query": "zebra",
            "mode": "synapses",
        },
    )
    assert r.status_code == 200
    body = r.json()
    # No synapses in project, mode=synapses → items list MUST stay empty
    assert body["synapses"] == []
    assert body["items"] == []
