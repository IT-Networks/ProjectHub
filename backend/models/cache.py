from datetime import datetime, timezone
from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column
from database import Base


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class OfflineCache(Base):
    __tablename__ = "offline_cache"

    cache_key: Mapped[str] = mapped_column(String(200), primary_key=True)
    cache_type: Mapped[str] = mapped_column(String(30), nullable=False)
    data: Mapped[str] = mapped_column(Text, nullable=False)  # JSON
    fetched_at: Mapped[str] = mapped_column(String(30), default=_now)
