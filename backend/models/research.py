"""Research models.

Two generations live in this module side by side:

    ``ResearchResult``  — legacy single-shot chat-research log. One row
                          per "I asked the agent a question" event;
                          consumed by chat.py + activity.py for the
                          recent-research widget. Schema is intentionally
                          minimal (query+result+model+session).

    ``ResearchRun`` + ``ResearchSubQuery`` + ``ResearchFinding`` +
    ``ProjectResearchSettings`` — Auto-Mode pipeline state (see
    ``claudedocs/design_research_auto_mode_20260516.md`` §7). Drives the
    parallel multi-source pipeline with depth modes and live streaming.

Schema conventions match the Synapse tables: 16-hex-char PKs, ISO-8601
timestamps, JSON-in-Text columns exposed via typed property accessors.
Registration in ``models/__init__.py`` triggers auto-create on startup.
"""

import json
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Legacy: single-shot chat research ──────────────────────────────────────

class ResearchResult(Base):
    """Legacy log of one-off agent-research calls from the chat UI.

    Kept untouched: consumed by ``routers/chat.py`` (creates rows) and
    ``routers/activity.py`` (recent-research feed). NOT part of the
    Auto-Mode pipeline — those use ``ResearchRun`` and friends below.
    """

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


# ── Auto-Mode: vocabularies ────────────────────────────────────────────────

#: ResearchRun.depth — picks the ResearchDepthProfile from settings.
RESEARCH_DEPTHS = {"normal", "tief"}

#: ResearchRun.mode — "auto" is the new pipeline, "single" is the legacy
#: blocking single-shot path (kept reachable via the same router for
#: backward compatibility during rollout).
RESEARCH_MODES = {"auto", "single"}

#: ResearchRun.status — lifecycle of one Auto-Mode execution.
#: ``partial`` = budget exhausted / cancelled mid-run but persisted findings
#: were kept. ``cancelled`` = user-initiated stop before any persists.
RESEARCH_RUN_STATUSES = {"running", "ok", "partial", "error", "cancelled"}

#: ResearchRun.phase — coarse progress for the UI stepper.
RESEARCH_RUN_PHASES = {
    "planning",
    "searching",
    "extracting",
    "lateral",
    "validating",
    "persisting",
    "synthesising",
    "done",
}

#: ResearchSubQuery.status — per-sub-query lifecycle.
SUB_QUERY_STATUSES = {"pending", "running", "done", "failed", "cancelled"}

#: ResearchFinding.status — the state model from spec §10.
#: ``blocked`` is reserved for the web provider's sanitize gate.
FINDING_STATUSES = {
    "candidate",
    "grounded",
    "flagged",
    "rejected",
    "persisted",
    "failed",
    "cancelled",
    "blocked",
}


# ── Auto-Mode: run state ───────────────────────────────────────────────────

class ResearchRun(Base):
    """One Auto-Mode execution for a project.

    Created in ``status="running"`` by the trigger route; the background
    pipeline owns the row from there. Counts + ``token_usage`` are
    updated incrementally as findings flow through the pipeline.
    """

    __tablename__ = "research_runs"
    __table_args__ = (
        # "already_running" short-circuit lookup in the trigger route.
        Index("ix_research_runs_project_status", "project_id", "status"),
        # Recent-runs list in the UI.
        Index("ix_research_runs_project_started", "project_id", "started_at"),
    )

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )

    # Request shape
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    depth: Mapped[str] = mapped_column(String(10), default="normal")
    mode: Mapped[str] = mapped_column(String(10), default="auto")

    # Lifecycle
    status: Mapped[str] = mapped_column(String(20), default="running")
    phase: Mapped[str] = mapped_column(String(20), default="planning")
    current_hop: Mapped[int] = mapped_column(Integer, default=0)

    # Counts (snapshot — kept in sync with the child rows for cheap UI reads)
    sub_query_count: Mapped[int] = mapped_column(Integer, default=0)
    finding_count: Mapped[int] = mapped_column(Integer, default=0)
    validated_count: Mapped[int] = mapped_column(Integer, default=0)
    persisted_count: Mapped[int] = mapped_column(Integer, default=0)
    flagged_count: Mapped[int] = mapped_column(Integer, default=0)
    rejected_count: Mapped[int] = mapped_column(Integer, default=0)

    # Optional link to the synapse run that ran post-persist (Tief mode).
    synapse_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("synapse_generation_runs.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )

    # Budget bookkeeping
    llm_calls_used: Mapped[int] = mapped_column(Integer, default=0)
    token_usage: Mapped[str] = mapped_column(Text, default="{}")
    # JSON: {"by_category": {...}, "total": N, "soft_cap": ..., "hard_cap": ...,
    #        "max_pressure_reached": "warn", "degradations_triggered": [...]}

    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    started_at: Mapped[str] = mapped_column(String(30), default=_now)
    finished_at: Mapped[str | None] = mapped_column(String(30), nullable=True, default=None)

    @property
    def token_usage_dict(self) -> dict:
        return json.loads(self.token_usage) if self.token_usage else {}

    @token_usage_dict.setter
    def token_usage_dict(self, value: dict) -> None:
        self.token_usage = json.dumps(value)


# ── Auto-Mode: sub-query layer ─────────────────────────────────────────────

class ResearchSubQuery(Base):
    """An atomic question generated by the planner (or by lateral expansion).

    Lateral sub-queries (Tief mode) carry ``is_lateral=True``, the hop
    number, and the parent finding IDs they were derived from — the UI
    uses these to draw the lineage tree.
    """

    __tablename__ = "research_sub_queries"
    __table_args__ = (
        # Lateral-join lookup ("give me all sub-queries for run X at hop N").
        Index("ix_research_sub_queries_run_hop", "run_id", "hop"),
    )

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("research_runs.id", ondelete="CASCADE"), nullable=False
    )

    hop: Mapped[int] = mapped_column(Integer, default=0)
    is_lateral: Mapped[bool] = mapped_column(Boolean, default=False)
    parent_finding_ids: Mapped[str] = mapped_column(Text, default="[]")
    # JSON array of ResearchFinding.id — the High-Conf-Findings that
    # produced the entity for which this lateral sub-query was spawned.

    question: Mapped[str] = mapped_column(Text, nullable=False)
    providers: Mapped[str] = mapped_column(Text, default="[]")
    # JSON array of provider keys (kb_fts, confluence, ...).
    rationale: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[int] = mapped_column(Integer, default=1)

    # Lateral-only signals (NULL for hop=0 initial sub-queries).
    relevance_score: Mapped[float | None] = mapped_column(
        Float, nullable=True, default=None
    )
    entity_focus: Mapped[str | None] = mapped_column(
        String(200), nullable=True, default=None
    )

    status: Mapped[str] = mapped_column(String(20), default="pending")
    started_at: Mapped[str | None] = mapped_column(
        String(30), nullable=True, default=None
    )
    finished_at: Mapped[str | None] = mapped_column(
        String(30), nullable=True, default=None
    )

    @property
    def providers_list(self) -> list[str]:
        return json.loads(self.providers) if self.providers else []

    @providers_list.setter
    def providers_list(self, value: list[str]) -> None:
        self.providers = json.dumps(value)

    @property
    def parent_finding_ids_list(self) -> list[str]:
        return json.loads(self.parent_finding_ids) if self.parent_finding_ids else []

    @parent_finding_ids_list.setter
    def parent_finding_ids_list(self, value: list[str]) -> None:
        self.parent_finding_ids = json.dumps(value)


# ── Auto-Mode: findings ────────────────────────────────────────────────────

class ResearchFinding(Base):
    """A normalised hit from one provider for one sub-query.

    Lifecycle (see spec §10):
        candidate → grounded → (persisted | flagged | rejected)
                  ↘ failed / cancelled / blocked (terminal)
    """

    __tablename__ = "research_findings"
    __table_args__ = (
        # Idempotency lookup: (run_id, source_ref) is the dedupe key.
        Index("ix_research_findings_run_source", "run_id", "source_ref"),
        # Per-sub-query streaming order.
        Index("ix_research_findings_sub_query", "sub_query_id", "created_at"),
        # FTS-free "give me everything still streaming for run X".
        Index("ix_research_findings_run_status", "run_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("research_runs.id", ondelete="CASCADE"), nullable=False
    )
    sub_query_id: Mapped[str] = mapped_column(
        ForeignKey("research_sub_queries.id", ondelete="CASCADE"), nullable=False
    )
    provider_key: Mapped[str] = mapped_column(String(40), nullable=False)

    # Source reference — used for idempotency and lazy full-content load.
    source_ref: Mapped[str] = mapped_column(String(300), nullable=False)
    # e.g. "confluence:page-456", "email:msg-7821", "kb:item-abc"

    title: Mapped[str] = mapped_column(Text, default="")
    snippet: Mapped[str] = mapped_column(Text, default="")
    # 200-500 chars; the compact representation that flows through the
    # orchestrator. Full content stays in the provider until requested.
    full_content: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    url: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    timestamp: Mapped[str | None] = mapped_column(
        String(30), nullable=True, default=None
    )
    # Author-time of the source (e.g. message sent-at). NOT the discovery time.
    author: Mapped[str | None] = mapped_column(
        String(200), nullable=True, default=None
    )
    raw_metadata: Mapped[str] = mapped_column(Text, default="{}")  # JSON

    status: Mapped[str] = mapped_column(String(20), default="candidate")
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)

    # Link to the KnowledgeItem that was persisted from this finding (if any).
    knowledge_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("knowledge_items.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )

    extra_data: Mapped[str] = mapped_column(Text, default="{}")
    # JSON: {"claims": [...], "validation": {"tier_b": ..., "tier_c": ...},
    #        "rerank": {"strategy": "bm25_embedding", "score": 0.83}, ...}

    created_at: Mapped[str] = mapped_column(String(30), default=_now)
    updated_at: Mapped[str] = mapped_column(String(30), default=_now)

    @property
    def raw_metadata_dict(self) -> dict:
        return json.loads(self.raw_metadata) if self.raw_metadata else {}

    @raw_metadata_dict.setter
    def raw_metadata_dict(self, value: dict) -> None:
        self.raw_metadata = json.dumps(value)

    @property
    def extra_data_dict(self) -> dict:
        return json.loads(self.extra_data) if self.extra_data else {}

    @extra_data_dict.setter
    def extra_data_dict(self, value: dict) -> None:
        self.extra_data = json.dumps(value)


# ── Auto-Mode: per-project settings ────────────────────────────────────────

class ProjectResearchSettings(Base):
    """Per-project overlay over the global ``settings.research`` defaults.

    Sparse — only set values override the global. ``project_id`` is the
    primary key (1:1 with Project), so a project either has overrides or
    falls back to defaults across the board.
    """

    __tablename__ = "project_research_settings"

    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    default_depth: Mapped[str] = mapped_column(String(10), default="normal")
    enabled_providers: Mapped[str] = mapped_column(Text, default="[]")
    # JSON array of provider keys: ["kb_fts","project_documents",...]
    provider_settings: Mapped[str] = mapped_column(Text, default="{}")
    # JSON: per-key dict, e.g. {"confluence": {"spaces": ["TEAM"]}, ...}
    routing_hints: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[str] = mapped_column(String(30), default=_now)

    @property
    def enabled_providers_list(self) -> list[str]:
        return json.loads(self.enabled_providers) if self.enabled_providers else []

    @enabled_providers_list.setter
    def enabled_providers_list(self, value: list[str]) -> None:
        self.enabled_providers = json.dumps(value)

    @property
    def provider_settings_dict(self) -> dict:
        return json.loads(self.provider_settings) if self.provider_settings else {}

    @provider_settings_dict.setter
    def provider_settings_dict(self, value: dict) -> None:
        self.provider_settings = json.dumps(value)
