from datetime import datetime, timezone
from sqlalchemy import String, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column
from database import Base


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WidgetConfig(Base):
    __tablename__ = "widget_configs"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    dashboard_id: Mapped[str] = mapped_column(String(50), default="main")
    widget_type: Mapped[str] = mapped_column(String(30), nullable=False)
    # build_status, pr_list, todo_count, note, project_status,
    # jira_issues, activity, kanban_mini, inbox_preview,
    # research_history, deadline_calendar
    grid_col: Mapped[int] = mapped_column(Integer, default=0)
    grid_row: Mapped[int] = mapped_column(Integer, default=0)
    grid_width: Mapped[int] = mapped_column(Integer, default=1)
    grid_height: Mapped[int] = mapped_column(Integer, default=1)
    config: Mapped[str] = mapped_column(Text, default="{}")  # JSON
    is_visible: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[str] = mapped_column(String(30), default=_now)
    updated_at: Mapped[str] = mapped_column(String(30), default=_now)
