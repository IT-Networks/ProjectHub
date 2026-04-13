from datetime import datetime, timezone
from sqlalchemy import String, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from database import Base


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ResearchResult(Base):
    __tablename__ = "research_results"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    query: Mapped[str] = mapped_column(Text, nullable=False)
    result: Mapped[str] = mapped_column(Text, nullable=False)
    model_used: Mapped[str] = mapped_column(String(50), default="")
    agent_team: Mapped[str] = mapped_column(String(50), default="")
    session_id: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[str] = mapped_column(String(30), default=_now)
