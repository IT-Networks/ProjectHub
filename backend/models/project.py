import json
from datetime import datetime, timezone
from sqlalchemy import String, Integer, Text, ForeignKey, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column, relationship
from database import Base


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="aktiv")  # aktiv, pausiert, archiviert
    color: Mapped[str] = mapped_column(String(7), default="#6366f1")
    tags: Mapped[str] = mapped_column(Text, default="[]")  # JSON array
    docs_path: Mapped[str] = mapped_column(Text, nullable=True, default=None)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String(30), default=_now)
    updated_at: Mapped[str] = mapped_column(String(30), default=_now)

    sources: Mapped[list["DataSourceLink"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )

    @property
    def tags_list(self) -> list[str]:
        return json.loads(self.tags) if self.tags else []

    @tags_list.setter
    def tags_list(self, value: list[str]):
        self.tags = json.dumps(value)


class DataSourceLink(Base):
    __tablename__ = "data_source_links"
    __table_args__ = (
        UniqueConstraint("project_id", "source_type", "source_config"),
    )

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    source_type: Mapped[str] = mapped_column(String(30), nullable=False)
    # jenkins_job, github_repo, git_repo, jira_project, confluence_space, email_folder, webex_room
    source_config: Mapped[str] = mapped_column(Text, default="{}")  # JSON
    display_name: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[str] = mapped_column(String(30), default=_now)

    project: Mapped["Project"] = relationship(back_populates="sources")

    @property
    def config_dict(self) -> dict:
        return json.loads(self.source_config) if self.source_config else {}

    @config_dict.setter
    def config_dict(self, value: dict):
        self.source_config = json.dumps(value)
