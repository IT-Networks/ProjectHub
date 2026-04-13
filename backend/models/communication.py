from datetime import datetime, timezone
from sqlalchemy import String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from database import Base


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LinkedMessage(Base):
    __tablename__ = "linked_messages"
    __table_args__ = (
        UniqueConstraint("link_target", "target_id", "source", "source_ref"),
    )

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    link_target: Mapped[str] = mapped_column(String(10), nullable=False)
    # project, todo, note
    target_id: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str] = mapped_column(String(10), nullable=False)  # email, webex
    source_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    subject: Mapped[str] = mapped_column(String(500), default="")
    sender: Mapped[str] = mapped_column(String(200), default="")
    date: Mapped[str] = mapped_column(String(30), default="")
    snippet: Mapped[str] = mapped_column(String(300), default="")
    snapshot: Mapped[str] = mapped_column(Text, default="{}")  # JSON
    created_at: Mapped[str] = mapped_column(String(30), default=_now)
