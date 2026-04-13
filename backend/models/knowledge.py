import json
from datetime import datetime, timezone
from sqlalchemy import String, Integer, Text, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from database import Base


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class KnowledgeItem(Base):
    __tablename__ = "knowledge_items"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="")  # Rich-text HTML
    content_plain: Mapped[str] = mapped_column(Text, default="")  # Stripped for FTS
    category: Mapped[str] = mapped_column(String(30), default="reference")
    # architecture, business_logic, infrastructure, process, decision, reference, custom
    source_type: Mapped[str] = mapped_column(String(20), default="manual")
    # manual, research, note_import, email_extract, chat_extract, confluence, document
    source_ref: Mapped[str] = mapped_column(String(200), nullable=True, default=None)
    tags: Mapped[str] = mapped_column(Text, default="[]")  # JSON array
    confidence: Mapped[str] = mapped_column(String(10), default="medium")  # high, medium, low
    extra_data: Mapped[str] = mapped_column(Text, default="{}")  # JSON blob
    is_pinned: Mapped[bool] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String(30), default=_now)
    updated_at: Mapped[str] = mapped_column(String(30), default=_now)

    @property
    def tags_list(self) -> list[str]:
        return json.loads(self.tags) if self.tags else []

    @tags_list.setter
    def tags_list(self, value: list[str]):
        self.tags = json.dumps(value)

    @property
    def extra_data_dict(self) -> dict:
        return json.loads(self.extra_data) if self.extra_data else {}

    @extra_data_dict.setter
    def extra_data_dict(self, value: dict):
        self.extra_data = json.dumps(value)


class KnowledgeEdge(Base):
    __tablename__ = "knowledge_edges"
    __table_args__ = (
        UniqueConstraint("source_item_id", "target_item_id", "edge_type"),
    )

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    source_item_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_items.id", ondelete="CASCADE"), nullable=False
    )
    target_item_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_items.id", ondelete="CASCADE"), nullable=False
    )
    edge_type: Mapped[str] = mapped_column(String(20), default="related")
    # related, references, based_on, extends
    label: Mapped[str] = mapped_column(String(200), nullable=True, default=None)
    created_at: Mapped[str] = mapped_column(String(30), default=_now)


class ProjectDocument(Base):
    __tablename__ = "project_documents"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_name: Mapped[str] = mapped_column(String(300), nullable=False)
    file_type: Mapped[str] = mapped_column(String(10), default="docx")  # docx, pdf
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    file_hash: Mapped[str] = mapped_column(String(64), default="")  # SHA256
    last_scanned_at: Mapped[str] = mapped_column(String(30), nullable=True, default=None)
    scan_status: Mapped[str] = mapped_column(String(20), default="pending")
    # pending, scanning, done, error
    total_sections: Mapped[int] = mapped_column(Integer, default=0)
    extracted_items: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String(30), default=_now)
    updated_at: Mapped[str] = mapped_column(String(30), default=_now)
