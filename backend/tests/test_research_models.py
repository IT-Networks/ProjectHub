"""Schema smoke tests for the Research Auto-Mode tables.

Phase 1 of the workflow. Verifies that the four new tables
(``research_runs``, ``research_sub_queries``, ``research_findings``,
``project_research_settings``) auto-create cleanly and survive a
roundtrip Insert + Select with every column populated. Also exercises
the JSON-property accessors so a typo in a setter would surface here
rather than in production.

No external services — uses the throwaway SQLite DB pinned by conftest.
"""
from __future__ import annotations

import asyncio
import os
import secrets
import tempfile

import pytest

# Fresh DB per pytest session so a previous run's schema can't mask a
# missing column. Matches the pattern from test_memory_router.
_TEST_DB_PATH = os.path.join(
    tempfile.gettempdir(), f"projecthub_pytest_research_models_{secrets.token_hex(4)}.db"
)
os.environ["PROJECTHUB_DB_PATH"] = _TEST_DB_PATH


@pytest.fixture(scope="module")
def db_session_factory():
    """Yield an async-session factory pinned to a fresh DB.

    Cleans up the file at teardown so a re-run starts from zero.
    """
    try:
        if os.path.exists(_TEST_DB_PATH):
            os.unlink(_TEST_DB_PATH)
    except OSError:
        pass

    import models  # noqa: F401 — registers every Base subclass
    from database import init_db, async_session

    asyncio.get_event_loop().run_until_complete(init_db())

    yield async_session

    try:
        if os.path.exists(_TEST_DB_PATH):
            os.unlink(_TEST_DB_PATH)
    except OSError:
        pass


def _run(coro):
    """Drive an awaitable from inside a sync test body."""
    return asyncio.get_event_loop().run_until_complete(coro)


def _new_project_id(session_factory) -> str:
    """Create a Project so the FK constraints stay satisfied."""
    from models.project import Project

    async def _go():
        async with session_factory() as db:
            pid = secrets.token_hex(8)
            db.add(Project(id=pid, name="Research-Model-Test"))
            await db.commit()
            return pid

    return _run(_go())


# ── Schema presence ────────────────────────────────────────────────────────

def test_tables_exist_after_create_all(db_session_factory):
    """All four Auto-Mode tables landed via Base.metadata.create_all."""
    from sqlalchemy import text

    async def _go():
        async with db_session_factory() as db:
            rows = (await db.execute(
                text(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name IN ('research_runs','research_sub_queries',"
                    "'research_findings','project_research_settings')"
                )
            )).all()
            return {r[0] for r in rows}

    names = _run(_go())
    assert names == {
        "research_runs",
        "research_sub_queries",
        "research_findings",
        "project_research_settings",
    }


def test_indexes_exist(db_session_factory):
    """Composite indexes from __table_args__ landed."""
    from sqlalchemy import text

    async def _go():
        async with db_session_factory() as db:
            rows = (await db.execute(
                text("SELECT name FROM sqlite_master WHERE type='index'")
            )).all()
            return {r[0] for r in rows}

    idx = _run(_go())
    expected = {
        "ix_research_runs_project_status",
        "ix_research_runs_project_started",
        "ix_research_sub_queries_run_hop",
        "ix_research_findings_run_source",
        "ix_research_findings_sub_query",
        "ix_research_findings_run_status",
    }
    missing = expected - idx
    assert not missing, f"missing indexes: {missing}"


# ── Roundtrip per model ───────────────────────────────────────────────────

def test_research_run_roundtrip(db_session_factory):
    """ResearchRun with all fields + token_usage_dict accessor."""
    from models.research import ResearchRun

    pid = _new_project_id(db_session_factory)
    run_id = secrets.token_hex(8)

    async def _go():
        async with db_session_factory() as db:
            run = ResearchRun(
                id=run_id,
                project_id=pid,
                topic="OAuth2 PKCE",
                depth="tief",
                mode="auto",
                status="running",
                phase="planning",
                current_hop=0,
                sub_query_count=0,
                finding_count=0,
            )
            run.token_usage_dict = {
                "by_category": {"planning": 4200, "rerank": 18400},
                "total": 22600,
                "soft_cap": 600_000,
                "hard_cap": 1_000_000,
                "max_pressure_reached": "ok",
                "degradations_triggered": [],
            }
            db.add(run)
            await db.commit()

        async with db_session_factory() as db:
            from sqlalchemy import select

            loaded = (
                await db.execute(select(ResearchRun).where(ResearchRun.id == run_id))
            ).scalar_one()
            return loaded

    loaded = _run(_go())
    assert loaded.topic == "OAuth2 PKCE"
    assert loaded.depth == "tief"
    assert loaded.mode == "auto"
    assert loaded.status == "running"
    assert loaded.phase == "planning"
    assert loaded.token_usage_dict["total"] == 22600
    assert loaded.token_usage_dict["by_category"]["planning"] == 4200
    assert loaded.started_at  # default `_now()` ran
    assert loaded.finished_at is None
    assert loaded.synapse_run_id is None
    assert loaded.error_summary is None


def test_research_sub_query_roundtrip_with_lateral_fields(db_session_factory):
    """Lateral sub-query carries hop / parent_finding_ids / entity_focus."""
    from models.research import ResearchRun, ResearchSubQuery

    pid = _new_project_id(db_session_factory)
    run_id = secrets.token_hex(8)
    sq_id = secrets.token_hex(8)

    async def _go():
        async with db_session_factory() as db:
            db.add(ResearchRun(id=run_id, project_id=pid, topic="t"))
            sq = ResearchSubQuery(
                id=sq_id,
                run_id=run_id,
                hop=1,
                is_lateral=True,
                question="How is keycloak-broker configured?",
                rationale="Extracted from F1's high-conf grounding",
                priority=2,
                relevance_score=0.78,
                entity_focus="keycloak-broker",
                status="pending",
            )
            sq.providers_list = ["confluence", "code_graph"]
            sq.parent_finding_ids_list = ["fid_aaa", "fid_bbb"]
            db.add(sq)
            await db.commit()

        async with db_session_factory() as db:
            from sqlalchemy import select

            loaded = (
                await db.execute(
                    select(ResearchSubQuery).where(ResearchSubQuery.id == sq_id)
                )
            ).scalar_one()
            return loaded

    loaded = _run(_go())
    assert loaded.hop == 1
    assert loaded.is_lateral is True
    assert loaded.providers_list == ["confluence", "code_graph"]
    assert loaded.parent_finding_ids_list == ["fid_aaa", "fid_bbb"]
    assert loaded.entity_focus == "keycloak-broker"
    assert loaded.relevance_score == 0.78


def test_research_finding_roundtrip_with_status_transitions(db_session_factory):
    """Finding roundtrips with all optional FKs + extra_data dict."""
    from models.research import ResearchFinding, ResearchRun, ResearchSubQuery

    pid = _new_project_id(db_session_factory)
    run_id = secrets.token_hex(8)
    sq_id = secrets.token_hex(8)
    fid = secrets.token_hex(8)

    async def _go():
        async with db_session_factory() as db:
            db.add(ResearchRun(id=run_id, project_id=pid, topic="t"))
            db.add(
                ResearchSubQuery(
                    id=sq_id, run_id=run_id, question="q", hop=0
                )
            )
            finding = ResearchFinding(
                id=fid,
                run_id=run_id,
                sub_query_id=sq_id,
                provider_key="confluence",
                source_ref="confluence:page-456",
                title="PKCE in Service X",
                snippet="Service X uses PKCE since v4.2…",
                url="https://confluence.intern/x/abc",
                timestamp="2026-05-12T08:15:00Z",
                author="alice",
                status="grounded",
                confidence=0.91,
            )
            finding.raw_metadata_dict = {"space": "ARCH", "labels": ["auth"]}
            finding.extra_data_dict = {
                "claims": [{"text": "PKCE since v4.2", "relation": "supported"}],
                "rerank": {"strategy": "bm25_embedding", "score": 0.83},
            }
            db.add(finding)
            await db.commit()

        async with db_session_factory() as db:
            from sqlalchemy import select

            loaded = (
                await db.execute(
                    select(ResearchFinding).where(ResearchFinding.id == fid)
                )
            ).scalar_one()
            return loaded

    loaded = _run(_go())
    assert loaded.provider_key == "confluence"
    assert loaded.source_ref == "confluence:page-456"
    assert loaded.status == "grounded"
    assert loaded.confidence == 0.91
    assert loaded.raw_metadata_dict["space"] == "ARCH"
    assert loaded.extra_data_dict["rerank"]["strategy"] == "bm25_embedding"
    assert loaded.knowledge_item_id is None  # not persisted yet


def test_project_research_settings_roundtrip(db_session_factory):
    """Per-project settings: PK is project_id, JSON accessors work."""
    from models.research import ProjectResearchSettings

    pid = _new_project_id(db_session_factory)

    async def _go():
        async with db_session_factory() as db:
            cfg = ProjectResearchSettings(
                project_id=pid,
                default_depth="tief",
                routing_hints="Bei Auth-Themen immer Confluence vor Code-Graph.",
            )
            cfg.enabled_providers_list = [
                "kb_fts", "project_documents", "confluence", "email", "webex",
            ]
            cfg.provider_settings_dict = {
                "confluence": {"spaces": ["TEAM", "ARCH"], "max_pages": 15},
                "email": {"days_back": 30, "max_results": 10},
            }
            db.add(cfg)
            await db.commit()

        async with db_session_factory() as db:
            from sqlalchemy import select

            loaded = (
                await db.execute(
                    select(ProjectResearchSettings).where(
                        ProjectResearchSettings.project_id == pid
                    )
                )
            ).scalar_one()
            return loaded

    loaded = _run(_go())
    assert loaded.default_depth == "tief"
    assert "Bei Auth-Themen" in loaded.routing_hints
    assert "confluence" in loaded.enabled_providers_list
    assert loaded.provider_settings_dict["confluence"]["max_pages"] == 15


# ── Idempotency: re-running create_all is a no-op ─────────────────────────

def test_re_init_db_is_idempotent(db_session_factory):
    """create_all must not error when the tables already exist."""
    from database import init_db

    # Just must not raise.
    _run(init_db())


# ── Cascade: deleting a Project cleans up the Auto-Mode rows ──────────────

def test_cascade_delete_project_removes_research_rows(db_session_factory):
    """ondelete=CASCADE on project_id removes runs / settings together."""
    from sqlalchemy import select, delete
    from models.project import Project
    from models.research import (
        ResearchRun, ResearchSubQuery, ResearchFinding, ProjectResearchSettings,
    )

    pid = _new_project_id(db_session_factory)
    run_id = secrets.token_hex(8)
    sq_id = secrets.token_hex(8)
    fid = secrets.token_hex(8)

    async def _setup():
        async with db_session_factory() as db:
            db.add(ResearchRun(id=run_id, project_id=pid, topic="t"))
            db.add(ResearchSubQuery(id=sq_id, run_id=run_id, question="q"))
            db.add(
                ResearchFinding(
                    id=fid, run_id=run_id, sub_query_id=sq_id,
                    provider_key="kb_fts", source_ref="kb:item-1",
                )
            )
            db.add(ProjectResearchSettings(project_id=pid))
            await db.commit()

    async def _delete_project():
        async with db_session_factory() as db:
            # SQLite needs foreign-key enforcement turned on per-connection
            # for ON DELETE CASCADE to fire.
            from sqlalchemy import text
            await db.execute(text("PRAGMA foreign_keys = ON"))
            await db.execute(delete(Project).where(Project.id == pid))
            await db.commit()

    async def _count():
        async with db_session_factory() as db:
            from sqlalchemy import text
            await db.execute(text("PRAGMA foreign_keys = ON"))
            runs = len((await db.execute(
                select(ResearchRun).where(ResearchRun.project_id == pid)
            )).all())
            sqs = len((await db.execute(
                select(ResearchSubQuery).where(ResearchSubQuery.run_id == run_id)
            )).all())
            fs = len((await db.execute(
                select(ResearchFinding).where(ResearchFinding.run_id == run_id)
            )).all())
            cfg = len((await db.execute(
                select(ProjectResearchSettings).where(
                    ProjectResearchSettings.project_id == pid
                )
            )).all())
            return runs, sqs, fs, cfg

    _run(_setup())
    _run(_delete_project())
    runs, sqs, fs, cfg = _run(_count())
    assert (runs, sqs, fs, cfg) == (0, 0, 0, 0), (
        f"cascade did not clear all rows: {(runs, sqs, fs, cfg)}"
    )
