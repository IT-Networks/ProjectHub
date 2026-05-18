from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from config import settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(
    f"sqlite+aiosqlite:///{settings.db_path}",
    echo=False,
)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db():
    """Create all tables on startup, including FTS5 virtual table."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            __import__("sqlalchemy").text("PRAGMA journal_mode=WAL")
        )
        # Create FTS5 virtual table for knowledge items (content-based, no triggers needed)
        await conn.execute(__import__("sqlalchemy").text("""
            CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_items_fts
            USING fts5(title, content_plain, tags, content='', tokenize='unicode61')
        """))
        # Migrate: add docs_path to projects if missing
        try:
            await conn.execute(
                __import__("sqlalchemy").text("ALTER TABLE projects ADD COLUMN docs_path TEXT")
            )
        except Exception:
            pass  # Column already exists

        # Migrate: add sync fields to notes
        try:
            await conn.execute(
                __import__("sqlalchemy").text("ALTER TABLE notes ADD COLUMN linked_knowledge_ids TEXT DEFAULT '[]'")
            )
        except Exception:
            pass  # Column already exists

        # Migrate: add sync fields to knowledge_items
        try:
            await conn.execute(
                __import__("sqlalchemy").text("ALTER TABLE knowledge_items ADD COLUMN source_note_id TEXT")
            )
        except Exception:
            pass  # Column already exists

        try:
            await conn.execute(
                __import__("sqlalchemy").text("ALTER TABLE knowledge_items ADD COLUMN sync_status TEXT DEFAULT 'synced'")
            )
        except Exception:
            pass  # Column already exists

        try:
            await conn.execute(
                __import__("sqlalchemy").text("ALTER TABLE knowledge_items ADD COLUMN last_synced_at TEXT")
            )
        except Exception:
            pass  # Column already exists

        try:
            await conn.execute(
                __import__("sqlalchemy").text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_knowledge_dedup "
                    "ON knowledge_items(project_id, source_type, source_ref) "
                    "WHERE source_ref IS NOT NULL"
                )
            )
        except Exception:
            pass

        # ── M1 (P2) — Brain embedding columns ────────────────────────────
        # Additive ALTERs; each guarded so re-runs are no-op when the column
        # already exists. The FTS5 schema extension that adds context_summary
        # as a fourth indexed column lives in T2.4 (Contextual Snippet
        # Generator) — keeping schema-shape changes separate from
        # indexing-pipeline changes is what makes this migration safe to
        # run before any embedding-aware retrieval code lands.
        for ddl in (
            "ALTER TABLE knowledge_items ADD COLUMN context_summary TEXT DEFAULT ''",
            "ALTER TABLE knowledge_items ADD COLUMN embedding BLOB",
            "ALTER TABLE knowledge_items ADD COLUMN embedding_model TEXT",
            "ALTER TABLE knowledge_items ADD COLUMN embedded_at TEXT",
        ):
            try:
                await conn.execute(__import__("sqlalchemy").text(ddl))
            except Exception:
                pass  # column already exists

        # Backfill-state index. Speeds up the "give me all items lacking
        # an embedding for project X" scan that the backfill job runs
        # at startup — without this it falls back to a table scan on
        # every poll.
        try:
            await conn.execute(__import__("sqlalchemy").text(
                "CREATE INDEX IF NOT EXISTS ix_knowledge_items_embedded "
                "ON knowledge_items(project_id, embedded_at)"
            ))
        except Exception:
            pass

        # Migrate: add sync-tracking fields to data_source_links (S1)
        for ddl in (
            "ALTER TABLE data_source_links ADD COLUMN last_synced_at TEXT",
            "ALTER TABLE data_source_links ADD COLUMN last_sync_status TEXT DEFAULT 'idle'",
            "ALTER TABLE data_source_links ADD COLUMN last_error_msg TEXT",
            "ALTER TABLE data_source_links ADD COLUMN sync_enabled INTEGER DEFAULT 1",
        ):
            try:
                await conn.execute(__import__("sqlalchemy").text(ddl))
            except Exception:
                pass  # Column already exists

        # ── P10 — Bi-temporal SynapseClaim columns ─────────────────────────
        # Additive; old rows get valid_from = their created_at (best effort)
        # so the "currently-valid" filter (valid_to IS NULL) immediately
        # returns every existing claim. We DON'T touch valid_to / superseded_by
        # — NULL is the right default ("currently valid", "never superseded").
        for ddl in (
            "ALTER TABLE synapse_claims ADD COLUMN valid_from TEXT DEFAULT ''",
            "ALTER TABLE synapse_claims ADD COLUMN valid_to TEXT",
            "ALTER TABLE synapse_claims ADD COLUMN superseded_by TEXT",
            "ALTER TABLE synapse_claims ADD COLUMN updated_at TEXT DEFAULT ''",
        ):
            try:
                await conn.execute(__import__("sqlalchemy").text(ddl))
            except Exception:
                pass  # Column already exists

        # Backfill valid_from = created_at for legacy rows so existing
        # claims remain queryable via the bi-temporal API. Idempotent —
        # only touches rows where valid_from is still empty.
        try:
            await conn.execute(__import__("sqlalchemy").text(
                "UPDATE synapse_claims SET valid_from = created_at "
                "WHERE valid_from IS NULL OR valid_from = ''"
            ))
            await conn.execute(__import__("sqlalchemy").text(
                "UPDATE synapse_claims SET updated_at = created_at "
                "WHERE updated_at IS NULL OR updated_at = ''"
            ))
        except Exception:
            pass

        # Bi-temporal lookup index — speeds up "claims valid on date X"
        # and "currently-valid claims for synapse Y" queries.
        try:
            await conn.execute(__import__("sqlalchemy").text(
                "CREATE INDEX IF NOT EXISTS ix_synapse_claims_validity "
                "ON synapse_claims(synapse_id, valid_from, valid_to)"
            ))
        except Exception:
            pass


async def get_db() -> AsyncSession:
    """Dependency for FastAPI routes."""
    async with async_session() as session:
        yield session
