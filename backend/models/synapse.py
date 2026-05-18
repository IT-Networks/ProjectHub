"""Synapsen — synthesis layer above flat KnowledgeItems.

A "Synapse" is an LLM-synthesised insight node built from a *cluster* of
related KnowledgeItems. Generation pipeline (see services/synapse_*.py):

    KnowledgeItems
      → entity extraction        (KnowledgeEntity / KnowledgeEntityMention /
                                  KnowledgeEntityRelation)
      → community detection      (cluster the entity graph)
      → synthesis                (one LLM call per community → Synapse draft)
      → validation               (claim decomposition → LLM-as-NLI grounding
                                  → parallel critic fan-out → confidence)
      → persist with verdict     (Synapse + SynapseClaim rows)

All tables here are *additive* — ``KnowledgeItem`` / ``KnowledgeEdge`` are
untouched. Provenance back to the source items lives in
``Synapse.source_item_ids`` (cluster level) and ``SynapseClaim.evidence``
(per-claim level).

ID / timestamp conventions match the rest of the codebase: 16-hex-char
string PKs, ISO-8601 strings for timestamps, JSON-in-Text columns exposed
via typed ``@property`` accessors.
"""

import json
from datetime import datetime, timezone
from sqlalchemy import (
    String, Integer, Float, Text, ForeignKey, UniqueConstraint, Index,
)
from sqlalchemy.orm import Mapped, mapped_column
from database import Base


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- Vocabularies -----------------------------------------------------------

# Synapse.status — lifecycle of a single synthesis node
SYNAPSE_STATUSES = {
    "pending_validation",  # synthesised, validation not yet finished
    "validated",           # validation done, persisted
    "rejected",            # validation rejected it (kept as tombstone)
    "stale",               # source items changed since generation
}

# Synapse.verdict — outcome of the validation pipeline
SYNAPSE_VERDICTS = {
    "persist",          # high confidence → shown normally
    "persist_flagged",  # medium confidence → shown with "ungeprüft" badge
    "human_review",     # low confidence / contradiction → review queue
}

# Synapse.confidence_band — bucketed Synapse.confidence
CONFIDENCE_BANDS = {"high", "medium", "low"}

# SynapseClaim.relation — per-atomic-claim grounding verdict
CLAIM_RELATIONS = {"supported", "contradicted", "unsupported", "partial"}

# SynapseGenerationRun.status — mirrors SyncRun semantics
RUN_STATUSES = {"running", "ok", "partial", "error"}

# SynapseGenerationRun.phase — coarse progress for the UI
RUN_PHASES = {
    "extracting_entities", "resolving_entities", "detecting_communities",
    "synthesising", "validating", "done",
}


# --- Entity layer -----------------------------------------------------------

class KnowledgeEntity(Base):
    """A concept/entity extracted from one or more KnowledgeItems.

    Entity resolution merges surface variants into one row — ``name`` keeps
    the canonical label, ``name_normalized`` (lowercased, trimmed) is the
    dedupe/lookup key.
    """

    __tablename__ = "knowledge_entities"
    __table_args__ = (
        UniqueConstraint("project_id", "name_normalized"),
        Index("ix_knowledge_entities_project", "project_id"),
    )

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    name_normalized: Mapped[str] = mapped_column(String(200), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(40), default="concept")
    # concept, component, person, system, technology, process, decision, ...
    description: Mapped[str] = mapped_column(Text, default="")
    mention_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String(30), default=_now)
    updated_at: Mapped[str] = mapped_column(String(30), default=_now)


class KnowledgeEntityMention(Base):
    """Join row: entity X is mentioned in KnowledgeItem Y."""

    __tablename__ = "knowledge_entity_mentions"
    __table_args__ = (
        UniqueConstraint("entity_id", "item_id"),
        Index("ix_entity_mentions_item", "item_id"),
    )

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    entity_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_entities.id", ondelete="CASCADE"), nullable=False
    )
    item_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_items.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[str] = mapped_column(String(30), default=_now)


class KnowledgeEntityRelation(Base):
    """A directed relation between two entities (co-occurrence or extracted).

    ``weight`` accumulates evidence: incremented each time the same relation
    is seen again, so community detection can weight strong links higher.
    """

    __tablename__ = "knowledge_entity_relations"
    __table_args__ = (
        UniqueConstraint("source_entity_id", "target_entity_id"),
        Index("ix_entity_relations_project", "project_id"),
    )

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    source_entity_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_entities.id", ondelete="CASCADE"), nullable=False
    )
    target_entity_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_entities.id", ondelete="CASCADE"), nullable=False
    )
    description: Mapped[str] = mapped_column(String(300), default="")
    weight: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[str] = mapped_column(String(30), default=_now)


# --- Synthesis layer --------------------------------------------------------

class Synapse(Base):
    """An LLM-synthesised insight node built from a cluster of KnowledgeItems.

    ``confidence`` is a calibrated 0–1 score from the validation pipeline;
    ``confidence_band`` is its bucket. ``community_level`` supports an
    optional hierarchy (0 = leaf community, higher = parent summaries)
    with ``parent_id`` linking up the tree.
    """

    __tablename__ = "synapses"
    __table_args__ = (
        Index("ix_synapses_project_status", "project_id", "status"),
        Index("ix_synapses_run", "generation_run_id"),
    )

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    generation_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("synapse_generation_runs.id", ondelete="SET NULL"),
        nullable=True, default=None,
    )
    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("synapses.id", ondelete="SET NULL"), nullable=True, default=None
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="")          # rich text / HTML
    summary_plain: Mapped[str] = mapped_column(Text, default="")    # stripped, for context/FTS
    community_level: Mapped[int] = mapped_column(Integer, default=0)

    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    confidence_band: Mapped[str] = mapped_column(String(10), default="low")
    verdict: Mapped[str] = mapped_column(String(20), default="human_review")
    status: Mapped[str] = mapped_column(String(20), default="pending_validation")

    source_item_ids: Mapped[str] = mapped_column(Text, default="[]")    # JSON array
    source_entity_ids: Mapped[str] = mapped_column(Text, default="[]")  # JSON array
    extra_data: Mapped[str] = mapped_column(Text, default="{}")         # JSON blob (token usage, model info, defects)

    created_at: Mapped[str] = mapped_column(String(30), default=_now)
    updated_at: Mapped[str] = mapped_column(String(30), default=_now)

    @property
    def source_item_ids_list(self) -> list[str]:
        return json.loads(self.source_item_ids) if self.source_item_ids else []

    @source_item_ids_list.setter
    def source_item_ids_list(self, value: list[str]):
        self.source_item_ids = json.dumps(value)

    @property
    def source_entity_ids_list(self) -> list[str]:
        return json.loads(self.source_entity_ids) if self.source_entity_ids else []

    @source_entity_ids_list.setter
    def source_entity_ids_list(self, value: list[str]):
        self.source_entity_ids = json.dumps(value)

    @property
    def extra_data_dict(self) -> dict:
        return json.loads(self.extra_data) if self.extra_data else {}

    @extra_data_dict.setter
    def extra_data_dict(self, value: dict):
        self.extra_data = json.dumps(value)


class SynapseClaim(Base):
    """One atomic claim within a Synapse, plus its validation result.

    The validation pipeline decomposes a Synapse's summary into atomic
    claims, grounds each against the source items (LLM-as-NLI) and runs a
    parallel critic fan-out. ``evidence`` holds the supporting/contradicting
    spans; ``verifier_votes`` holds the raw per-relation vote tally.

    Bi-temporal (P10):
        * ``valid_from`` / ``valid_to`` form a closed-open validity window
          (``valid_to is None`` = currently valid). When a synapse is
          re-generated and a claim is updated, the old row keeps its
          history — its ``valid_to`` is set to the new timestamp and
          ``superseded_by`` links to the new row.
        * ``updated_at`` is the transaction-time companion to the
          valid-time window: when the row was last *written*, regardless
          of when the claim was *true*. This is what audit / replay tools
          read to answer "what did the system know on day X".
    """

    __tablename__ = "synapse_claims"
    __table_args__ = (
        Index("ix_synapse_claims_synapse", "synapse_id"),
        # Bi-temporal lookups: "all currently-valid claims for synapse Y"
        # and "what was true on date X" both hit this index.
        Index(
            "ix_synapse_claims_validity",
            "synapse_id", "valid_from", "valid_to",
        ),
    )

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    synapse_id: Mapped[str] = mapped_column(
        ForeignKey("synapses.id", ondelete="CASCADE"), nullable=False
    )
    claim_text: Mapped[str] = mapped_column(Text, nullable=False)
    relation: Mapped[str] = mapped_column(String(20), default="unsupported")
    # supported, contradicted, unsupported, partial
    evidence: Mapped[str] = mapped_column(Text, default="[]")
    # JSON: [{"item_id": "...", "span": "...", "nli_score": 0.91}]
    nli_score: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    verifier_agreement: Mapped[float] = mapped_column(Float, default=0.0)
    verifier_votes: Mapped[str] = mapped_column(Text, default="{}")
    # JSON: {"supported": 4, "partial": 1}
    created_at: Mapped[str] = mapped_column(String(30), default=_now)

    # ── Bi-temporal fields (P10) ──────────────────────────────────────
    # valid_from defaults to created_at on the SQL side via DEFAULT clause
    # in the migration; new rows still set it explicitly in code to keep
    # the model usable without hitting the DB default.
    valid_from: Mapped[str] = mapped_column(String(30), default=_now)
    # ``None`` = currently valid; an ISO timestamp = the moment this claim
    # was superseded by a newer row.
    valid_to: Mapped[str | None] = mapped_column(
        String(30), nullable=True, default=None
    )
    # FK-like reference (no actual FK constraint; we don't want cascades to
    # break the supersede chain if the new claim is later deleted).
    superseded_by: Mapped[str | None] = mapped_column(
        String(16), nullable=True, default=None
    )
    # Transaction-time: when this ROW was last written. ``created_at`` is
    # immutable; ``updated_at`` moves whenever the row is modified.
    updated_at: Mapped[str] = mapped_column(String(30), default=_now)

    @property
    def evidence_list(self) -> list[dict]:
        return json.loads(self.evidence) if self.evidence else []

    @evidence_list.setter
    def evidence_list(self, value: list[dict]):
        self.evidence = json.dumps(value)

    @property
    def verifier_votes_dict(self) -> dict:
        return json.loads(self.verifier_votes) if self.verifier_votes else {}

    @verifier_votes_dict.setter
    def verifier_votes_dict(self, value: dict):
        self.verifier_votes = json.dumps(value)

    @property
    def is_current(self) -> bool:
        """True when the claim is currently valid (valid_to is open)."""
        return self.valid_to is None


class SynapseGenerationRun(Base):
    """Summary of one manual synapse-generation run for a project.

    Mirrors ``SyncRun`` — drives the UI progress indicator. The background
    runner (``services/synapse_pipeline.py``) owns this row's lifecycle:
    created ``running`` by the trigger route, finalised by the runner.
    """

    __tablename__ = "synapse_generation_runs"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    trigger: Mapped[str] = mapped_column(String(20), default="manual")
    status: Mapped[str] = mapped_column(String(20), default="running")
    # running, ok, partial, error
    phase: Mapped[str] = mapped_column(String(30), default="extracting_entities")

    item_count: Mapped[int] = mapped_column(Integer, default=0)
    entity_count: Mapped[int] = mapped_column(Integer, default=0)
    synapse_count: Mapped[int] = mapped_column(Integer, default=0)
    validated_count: Mapped[int] = mapped_column(Integer, default=0)
    flagged_count: Mapped[int] = mapped_column(Integer, default=0)
    review_count: Mapped[int] = mapped_column(Integer, default=0)

    token_usage: Mapped[str] = mapped_column(Text, default="{}")  # JSON
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    started_at: Mapped[str] = mapped_column(String(30), default=_now)
    finished_at: Mapped[str | None] = mapped_column(String(30), nullable=True, default=None)

    @property
    def token_usage_dict(self) -> dict:
        return json.loads(self.token_usage) if self.token_usage else {}

    @token_usage_dict.setter
    def token_usage_dict(self, value: dict):
        self.token_usage = json.dumps(value)


class KnowledgeReviewQueue(Base):
    """A low-confidence Synapse awaiting a human verdict.

    Populated when the validation pipeline returns ``human_review`` (low
    confidence or a contradicted claim). The human verdict closes the row;
    accumulated verdicts become labelled data for tuning the confidence
    thresholds later.
    """

    __tablename__ = "knowledge_review_queue"
    __table_args__ = (
        Index("ix_review_queue_project", "project_id", "human_verdict"),
    )

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    synapse_id: Mapped[str] = mapped_column(
        ForeignKey("synapses.id", ondelete="CASCADE"), nullable=False
    )
    reason: Mapped[str] = mapped_column(String(300), default="")
    # e.g. "node_confidence 0.41 < 0.5", "claim 3 CONTRADICTED"
    human_verdict: Mapped[str | None] = mapped_column(String(20), nullable=True, default=None)
    # None = open, "accepted", "rejected", "edited"
    created_at: Mapped[str] = mapped_column(String(30), default=_now)
    resolved_at: Mapped[str | None] = mapped_column(String(30), nullable=True, default=None)
