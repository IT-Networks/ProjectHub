import json
from datetime import datetime, timezone
from sqlalchemy import String, Integer, Text, Float, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from database import Base


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Todo(Base):
    __tablename__ = "todos"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="backlog")
    # backlog, in_progress, review, done
    priority: Mapped[str] = mapped_column(String(10), default="medium")
    # high, medium, low
    deadline: Mapped[str | None] = mapped_column(String(30), nullable=True)
    kanban_order: Mapped[int] = mapped_column(Integer, default=0)
    tags: Mapped[str] = mapped_column(Text, default="[]")
    source: Mapped[str] = mapped_column(String(10), default="manual")
    # manual, email, webex
    source_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    ai_analysis: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(30), default=_now)
    updated_at: Mapped[str] = mapped_column(String(30), default=_now)

    @property
    def tags_list(self) -> list[str]:
        return json.loads(self.tags) if self.tags else []


class TodoQueue(Base):
    __tablename__ = "todo_queue"
    __table_args__ = (
        UniqueConstraint("source", "source_ref"),
    )

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    suggested_title: Mapped[str] = mapped_column(String(500), nullable=False)
    suggested_description: Mapped[str] = mapped_column(Text, default="")
    suggested_priority: Mapped[str] = mapped_column(String(10), default="medium")
    suggested_deadline: Mapped[str | None] = mapped_column(String(30), nullable=True)
    suggested_project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    source: Mapped[str] = mapped_column(String(10), nullable=False)  # email, webex
    source_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    source_subject: Mapped[str] = mapped_column(String(500), default="")
    source_sender: Mapped[str] = mapped_column(String(200), default="")
    source_date: Mapped[str] = mapped_column(String(30), default="")
    source_snapshot: Mapped[str] = mapped_column(Text, default="{}")  # JSON
    ai_analysis: Mapped[str] = mapped_column(Text, default="")
    ai_confidence: Mapped[float] = mapped_column(Float, default=0.5)
    queue_status: Mapped[str] = mapped_column(String(10), default="pending")
    # pending, accepted, rejected
    reviewed_at: Mapped[str | None] = mapped_column(String(30), nullable=True)
    created_at: Mapped[str] = mapped_column(String(30), default=_now)
