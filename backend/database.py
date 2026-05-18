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


async def get_db() -> AsyncSession:
    """Dependency for FastAPI routes."""
    async with async_session() as session:
        yield session
