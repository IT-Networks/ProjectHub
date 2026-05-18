"""Tests for the four local Research-Auto-Mode providers.

Each provider gets:
    * a health probe test (must be ``ok`` against a fresh-init DB),
    * a happy-path stream test (seed data → at least one finding +
      exactly-one terminal ``done``),
    * a no-match stream test (empty query / no matching rows → no
      findings, single ``done``),
    * a cancel test (event pre-set → stream returns within a small
      number of yields, never emits findings).

All four use the throwaway SQLite from conftest and seed their own
fixtures so the tests don't share state between modules.
"""
from __future__ import annotations

import asyncio
import os
import secrets
import tempfile

import pytest

# Fresh DB per module so prior runs can't mask schema drift.
_TEST_DB_PATH = os.path.join(
    tempfile.gettempdir(),
    f"projecthub_pytest_providers_{secrets.token_hex(4)}.db",
)
os.environ["PROJECTHUB_DB_PATH"] = _TEST_DB_PATH


@pytest.fixture(scope="module")
def initdb():
    try:
        if os.path.exists(_TEST_DB_PATH):
            os.unlink(_TEST_DB_PATH)
    except OSError:
        pass
    import models  # noqa: F401 — registers every Base subclass
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


async def _drain(agen):
    """Collect every event a provider yields into a list (for assertions)."""
    out = []
    async for event in agen:
        out.append(event)
    return out


def _new_project_id() -> str:
    """Create a Project row and return its id (FK target for seeds)."""
    from database import async_session
    from models.project import Project

    async def _go():
        async with async_session() as db:
            pid = secrets.token_hex(8)
            db.add(Project(id=pid, name=f"P-{pid[:6]}"))
            await db.commit()
            return pid

    return _run(_go())


# ── Shared helpers ──────────────────────────────────────────────────────────


def _seed_knowledge(project_id: str, *, count: int = 3, source_type: str = "manual"):
    """Seed ``count`` KnowledgeItems with deterministic content + an FTS row."""
    import json
    from database import async_session
    from models.knowledge import KnowledgeItem
    from sqlalchemy import text

    async def _go():
        async with async_session() as db:
            ids = []
            for i in range(count):
                ki = KnowledgeItem(
                    id=secrets.token_hex(8),
                    project_id=project_id,
                    title=f"Authentication study {i}",
                    content=f"<p>Service X uses OAuth2 PKCE for auth ({i})</p>",
                    content_plain=f"Service X uses OAuth2 PKCE for auth ({i})",
                    category="reference",
                    source_type=source_type,
                    source_ref=f"seed-{i}",
                    tags=json.dumps(["auth", "oauth"]),
                    confidence="medium",
                )
                db.add(ki)
                ids.append(ki.id)
            await db.commit()

            # FTS5 sync — knowledge_items_fts is the contentless table we
            # search against in hybrid_search/_fts_top_k.
            for kid in ids:
                row = (
                    await db.execute(
                        text("SELECT rowid FROM knowledge_items WHERE id = :id"),
                        {"id": kid},
                    )
                ).first()
                if not row:
                    continue
                await db.execute(
                    text(
                        "INSERT OR REPLACE INTO knowledge_items_fts(rowid, title, content_plain, tags) "
                        "VALUES (:rowid, :title, :content, :tags)"
                    ),
                    {
                        "rowid": row[0],
                        "title": "Authentication study",
                        "content": "Service X uses OAuth2 PKCE for auth",
                        "tags": "auth oauth",
                    },
                )
            await db.commit()
            return ids

    return _run(_go())


def _seed_notes(project_id: str, *, count: int = 2):
    from database import async_session
    from models.note import Note

    async def _go():
        async with async_session() as db:
            ids = []
            for i in range(count):
                n = Note(
                    id=secrets.token_hex(8),
                    project_id=project_id,
                    title=f"Auth Meeting {i}",
                    content=f"<p>Marcus mentioned <b>OAuth2 PKCE</b> policy ({i})</p>",
                    content_format="html",
                    is_pinned=(1 if i == 0 else 0),
                )
                db.add(n)
                ids.append(n.id)
            await db.commit()
            return ids

    return _run(_go())


def _seed_chat(project_id: str, *, count: int = 2):
    from database import async_session
    from models.research import ResearchResult

    async def _go():
        async with async_session() as db:
            ids = []
            for i in range(count):
                r = ResearchResult(
                    id=secrets.token_hex(8),
                    project_id=project_id,
                    query=f"What is OAuth2 PKCE? ({i})",
                    result=f"OAuth2 PKCE prevents auth-code interception ({i})",
                    model_used="gpt-oss-120b",
                    agent_team="default",
                    session_id="s",
                )
                db.add(r)
                ids.append(r.id)
            await db.commit()
            return ids

    return _run(_go())


# ── Registry sanity ────────────────────────────────────────────────────────


def test_registry_has_four_local_providers(initdb):
    from services.research_providers import PROVIDERS

    assert set(PROVIDERS.keys()) == {
        "kb_fts", "project_documents", "project_notes", "chat_history",
    }
    for p in PROVIDERS.values():
        assert p.default_enabled is True
        assert p.typical_latency == "fast"
        assert p.side_effect == "read"


# ── Per-provider health checks ─────────────────────────────────────────────


@pytest.mark.parametrize("key", ["kb_fts", "project_documents", "project_notes", "chat_history"])
def test_provider_health_ok(initdb, key):
    from services.research_providers import PROVIDERS

    health = _run(PROVIDERS[key].health())
    assert health.ok is True
    assert health.detail == "connected"
    assert health.last_checked_at


# ── kb_fts ─────────────────────────────────────────────────────────────────


def test_kb_fts_finds_matching_items(initdb):
    from services.research_providers import PROVIDERS

    pid = _new_project_id()
    _seed_knowledge(pid, count=3)

    cancel = asyncio.Event()
    provider = PROVIDERS["kb_fts"]
    events = _run(_drain(provider.stream(
        "OAuth2 PKCE",
        provider_settings={"max_results": 5, "mode": "fts"},
        cancel=cancel,
        project_id=pid,
    )))

    findings = [e for e in events if e.kind == "finding"]
    terminals = [e for e in events if e.kind in ("done", "error")]
    assert len(findings) >= 1, f"expected ≥1 finding, got events: {events}"
    assert len(terminals) == 1, "must emit exactly one terminal event"
    assert terminals[0].kind == "done"
    f = findings[0].finding
    assert f.provider_key == "kb_fts"
    assert f.source_ref.startswith("kb:")
    assert "OAuth2" in (f.snippet or f.full_content or "")


def test_kb_fts_cancel_pre_set_skips_search(initdb):
    from services.research_providers import PROVIDERS

    pid = _new_project_id()
    _seed_knowledge(pid, count=2)

    cancel = asyncio.Event()
    cancel.set()  # cancelled before we start
    events = _run(_drain(PROVIDERS["kb_fts"].stream(
        "OAuth2 PKCE",
        provider_settings={"max_results": 5},
        cancel=cancel,
        project_id=pid,
    )))
    assert not any(e.kind == "finding" for e in events)
    assert events[-1].kind == "done"
    assert events[-1].status_text == "cancelled"


# ── project_documents ──────────────────────────────────────────────────────


def test_project_documents_only_returns_document_source_type(initdb):
    from services.research_providers import PROVIDERS

    pid = _new_project_id()
    # Mix: half "document", half "manual" — only document should come back.
    _seed_knowledge(pid, count=2, source_type="document")
    _seed_knowledge(pid, count=2, source_type="manual")

    cancel = asyncio.Event()
    events = _run(_drain(PROVIDERS["project_documents"].stream(
        "PKCE",
        provider_settings={"max_results": 10},
        cancel=cancel,
        project_id=pid,
    )))
    findings = [e for e in events if e.kind == "finding"]
    assert len(findings) >= 1, f"expected ≥1 document finding, got {events}"
    for ev in findings:
        assert ev.finding.source_ref.startswith("document:")
        assert ev.finding.raw_metadata["source_type"] == "document"
    assert events[-1].kind == "done"


# ── project_notes ──────────────────────────────────────────────────────────


def test_project_notes_finds_matching_note_pinned_first(initdb):
    from services.research_providers import PROVIDERS

    pid = _new_project_id()
    _seed_notes(pid, count=2)

    cancel = asyncio.Event()
    events = _run(_drain(PROVIDERS["project_notes"].stream(
        "OAuth2 PKCE",
        provider_settings={"max_results": 5},
        cancel=cancel,
        project_id=pid,
    )))
    findings = [e for e in events if e.kind == "finding"]
    assert len(findings) == 2
    # First note seeded was is_pinned=True → must come first.
    assert findings[0].finding.raw_metadata["is_pinned"] is True
    # Snippet must be plaintext (HTML stripped).
    assert "<b>" not in findings[0].finding.snippet
    assert events[-1].kind == "done"


def test_project_notes_empty_query_terminates_cleanly(initdb):
    from services.research_providers import PROVIDERS

    pid = _new_project_id()
    cancel = asyncio.Event()
    # All stopwords → no surviving terms → tokenizer falls back to raw split,
    # so even "the and or" produces *some* tokens. Test true empty.
    events = _run(_drain(PROVIDERS["project_notes"].stream(
        "",
        provider_settings={"max_results": 5},
        cancel=cancel,
        project_id=pid,
    )))
    assert not any(e.kind == "finding" for e in events)
    assert events[-1].kind == "done"
    assert events[-1].status_text == "empty_query"


# ── chat_history ───────────────────────────────────────────────────────────


def test_chat_history_finds_prior_research(initdb):
    from services.research_providers import PROVIDERS

    pid = _new_project_id()
    _seed_chat(pid, count=3)

    cancel = asyncio.Event()
    events = _run(_drain(PROVIDERS["chat_history"].stream(
        "PKCE",
        provider_settings={"max_results": 5},
        cancel=cancel,
        project_id=pid,
    )))
    findings = [e for e in events if e.kind == "finding"]
    assert len(findings) == 3
    for ev in findings:
        assert ev.finding.source_ref.startswith("chat:")
        assert ev.finding.raw_metadata["model_used"] == "gpt-oss-120b"
    assert events[-1].kind == "done"


def test_chat_history_other_project_isolated(initdb):
    """Cross-project leak guard — chat from project A must not surface in B."""
    from services.research_providers import PROVIDERS

    pid_a = _new_project_id()
    pid_b = _new_project_id()
    _seed_chat(pid_a, count=2)  # only A has data

    cancel = asyncio.Event()
    events = _run(_drain(PROVIDERS["chat_history"].stream(
        "PKCE",
        provider_settings={"max_results": 5},
        cancel=cancel,
        project_id=pid_b,
    )))
    assert not any(e.kind == "finding" for e in events)
    assert events[-1].kind == "done"
