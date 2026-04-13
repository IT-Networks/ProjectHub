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


async def get_db() -> AsyncSession:
    """Dependency for FastAPI routes."""
    async with async_session() as session:
        yield session
