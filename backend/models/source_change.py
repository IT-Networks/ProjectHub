"""Staging table for external changes (PRs, Jira, Builds, Commits) before they become Knowledge."""

import json
from datetime import datetime, timezone
from sqlalchemy import String, Integer, Text, ForeignKey, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column
from database import Base


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# change_type values — one per external item class
CHANGE_TYPES = {
    "pr",          # GitHub pull request
    "build",       # Jenkins build
    "commit",      # Git commit
    "jira",        # Jira issue
    "jira_comment",
    "pr_comment",
}

ANALYSIS_STATUSES = {
    "pending",      # just collected, not yet analyzed
    "analyzing",    # LLM call in flight
    "analyzed",     # has analysis_result
    "accepted",     # user (or auto-accept) promoted to knowledge
    "dismissed",    # user rejected; keep as tombstone to dedupe future polls
    "error",        # analysis failed; can retry
}


class SourceChange(Base):
    """Staging row for an externally-detected change.

    Lifecycle:
        pending → analyzing → analyzed → accepted (→ knowledge_item)
                                      → dismissed
                           → error (retryable)

    Duplicate detection: (project_id, source_type, external_ref, payload_hash)
    unique — the same external item with the same content hash is only
    stored once. If the hash changes (e.g. PR got a new commit), a NEW
    row is inserted and the previous one stays as history.
    """

    __tablename__ = "source_changes"
    __table_args__ = (
        UniqueConstraint("project_id", "source_type", "external_ref", "payload_hash"),
        Index("ix_source_changes_project_status", "project_id", "analysis_status"),
    )

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    source_link_id: Mapped[str | None] = mapped_column(
        ForeignKey("data_source_links.id", ondelete="SET NULL"), nullable=True, default=None
    )
    source_type: Mapped[str] = mapped_column(String(30), nullable=False)
    # pr, build, commit, jira, jira_comment, pr_comment
    external_ref: Mapped[str] = mapped_column(String(300), nullable=False)
    # e.g. "owner/repo#42", "JOBNAME#157", "sha:abc123", "PROJ-45"
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # SHA-256 over the canonical raw payload — drives dedupe
    payload_raw: Mapped[str] = mapped_column(Text, default="{}")  # JSON
    title: Mapped[str] = mapped_column(String(500), default="")
    # Short human label for UI lists

    detected_at: Mapped[str] = mapped_column(String(30), default=_now)
    analysis_status: Mapped[str] = mapped_column(String(20), default="pending")
    analysis_result: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)  # JSON
    analyzed_at: Mapped[str | None] = mapped_column(String(30), nullable=True, default=None)
    auto_accepted: Mapped[int] = mapped_column(Integer, default=0)

    knowledge_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("knowledge_items.id", ondelete="SET NULL"), nullable=True, default=None
    )

    @property
    def payload(self) -> dict:
        return json.loads(self.payload_raw) if self.payload_raw else {}

    @payload.setter
    def payload(self, value: dict):
        self.payload_raw = json.dumps(value)

    @property
    def analysis(self) -> dict | None:
        return json.loads(self.analysis_result) if self.analysis_result else None

    @analysis.setter
    def analysis(self, value: dict | None):
        self.analysis_result = json.dumps(value) if value is not None else None


class SyncRun(Base):
    """Summary of one sync run for a project — used to drive the UI badge."""

    __tablename__ = "sync_runs"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    started_at: Mapped[str] = mapped_column(String(30), default=_now)
    finished_at: Mapped[str | None] = mapped_column(String(30), nullable=True, default=None)
    trigger: Mapped[str] = mapped_column(String(20), default="manual")
    # manual, auto_open, periodic, sse_event
    status: Mapped[str] = mapped_column(String(20), default="running")
    # running, ok, partial, error
    sources_synced: Mapped[int] = mapped_column(Integer, default=0)
    sources_failed: Mapped[int] = mapped_column(Integer, default=0)
    changes_detected: Mapped[int] = mapped_column(Integer, default=0)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
