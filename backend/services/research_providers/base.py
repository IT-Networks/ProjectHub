"""SearchProvider — adapter contract for one Auto-Mode data source.

Phase 2 of the Research-Auto-Mode workflow. Defines the stable interface
that every concrete provider (kb_fts, confluence, email, …) implements.
The pipeline orchestrator (``services/research_pipeline.py``, P6) treats
all providers uniformly through this shape — no provider-specific
plumbing in the orchestrator itself.

Streaming contract: ``stream`` is an *async generator* yielding
``SearchProgress`` events as the provider works. Findings come through
``SearchProgress(kind="finding", finding=...)`` so the pipeline can
forward each one over SSE without waiting for the provider to finish.
Terminal: exactly one ``kind="done"`` (success) or ``kind="error"``
(failure) event per call — consumers can finalise their state machines
on it.

Cancellation: every ``stream`` call gets an ``asyncio.Event``. Providers
MUST check it between expensive operations (LLM calls, paged HTTP fetches,
embedder batches) and return cleanly when set. Latency budget for cancel
reaction: < 500 ms in the worst case, < 100 ms for local providers.

The runtime ``Finding`` DTO here is *not* the SQLAlchemy
``ResearchFinding`` row — the orchestrator translates one into the other
at persist time. Keeping them separate means providers don't carry a DB
session and stay unit-testable.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import AsyncIterator, Literal, Protocol, runtime_checkable


# ── Value types ─────────────────────────────────────────────────────────────


@dataclass
class ProviderHealth:
    """Result of a provider's reachability/auth probe.

    Filled by ``SearchProvider.health()`` and surfaced in the
    /api/research/{pid}/providers/health endpoint + the Settings UI.
    """

    ok: bool
    detail: str
    # short categorical: "connected" | "auth_missing" | "timeout" |
    # "unreachable" | "disabled" | "config_missing" | "error"
    last_checked_at: str  # ISO-8601 timestamp


@dataclass
class Finding:
    """One normalised search result, provider-agnostic.

    The compact representation that flows through the orchestrator; the
    full content stays in the provider's adapter scope (lazy-load only
    when the user clicks a finding for detail).
    """

    provider_key: str  # "kb_fts" | "confluence" | ...
    source_ref: str
    # Stable id for idempotency / lazy-load: "confluence:page-456",
    # "kb:item-abc", "email:msg-7821", "notes:note-xyz".
    title: str
    snippet: str  # 200-500 chars
    full_content: str | None = None  # set only when the provider already has it cheaply
    url: str | None = None  # external URL when applicable
    timestamp: str | None = None  # ISO author/event time of the source
    author: str | None = None
    score: float | None = None  # provider-native relevance (FTS rank, cosine, ...)
    raw_metadata: dict = field(default_factory=dict)


@dataclass
class SearchProgress:
    """One streaming event from a provider during ``stream()``.

    Pipeline guarantees: terminal event always carries
    ``kind in {"done", "error"}``; ``kind="finding"`` always populates
    ``finding``; ``kind="status"`` is optional.
    """

    kind: Literal["status", "finding", "error", "done"]
    finding: Finding | None = None
    status_text: str | None = None
    error: str | None = None


# ── Protocol ────────────────────────────────────────────────────────────────


@runtime_checkable
class SearchProvider(Protocol):
    """The single shape every Auto-Mode source adapter implements.

    Implementations are stateless wrappers — they receive a DB session
    factory or HTTP client at construction (or look it up via ``settings``
    on each call), never hold per-run state. Per-call state lives in the
    pipeline orchestrator.
    """

    #: Short stable id; matches the keys in ``ResearchProviderRegistry``
    #: in ``config.py`` and the strings the planner emits.
    key: str

    #: One-sentence description fed into the planner prompt so it can
    #: pick the right providers per sub-query.
    description: str

    #: ``"fast"`` = local DB / FTS5 (< 2 s typical).
    #: ``"medium"`` = single external HTTP call (2–30 s).
    #: ``"slow"`` = deep multi-call pipeline (30 s+; e.g. Confluence-Deep).
    typical_latency: Literal["fast", "medium", "slow"]

    #: ``"read"`` = local or read-only; safe to call without confirmation.
    #: ``"external"`` = touches a third-party system, respects its rate-limits.
    side_effect: Literal["read", "external"]

    #: Whether the provider is on by default in a fresh project's settings.
    #: Tier-1 (local) providers default True; everything else defaults False.
    default_enabled: bool

    async def health(self) -> ProviderHealth:
        """Probe reachability/auth without doing real work.

        Should be cheap (< 200 ms) and idempotent. Local providers can
        return ``ok=True`` unconditionally; remote providers ping the
        relevant test endpoint via AI-Assist.
        """
        ...

    def stream(
        self,
        query: str,
        settings: dict,
        cancel: asyncio.Event,
    ) -> AsyncIterator[SearchProgress]:
        """Yield ``SearchProgress`` events for ``query`` against this source.

        Args:
            query: the (sub-)question text to search for.
            settings: provider-specific overrides merged from the global
                ``ResearchProviderDefaults`` and the project's
                ``ProjectResearchSettings.provider_settings[key]``.
                Always at least ``{"max_results": int, "timeout_sec": int}``.
            cancel: when set, the provider must stop yielding new findings
                and emit a final ``kind="done"`` (status "cancelled" in
                metadata if relevant) within 500 ms.

        Yields:
            Zero or more ``kind="status"`` updates, zero or more
            ``kind="finding"`` events with a ``Finding``, then EXACTLY ONE
            terminal ``kind="done"`` or ``kind="error"`` event. The
            terminal guarantee is what lets the orchestrator finalise the
            sub-query row deterministically.
        """
        ...


# ── Shared helpers ──────────────────────────────────────────────────────────


def _now_iso() -> str:
    """ISO-8601 timestamp helper for ProviderHealth.last_checked_at."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def make_snippet(text: str, *, max_chars: int = 320) -> str:
    """Trim ``text`` to a single-line snippet of at most ``max_chars``.

    Collapses internal whitespace so the UI gets a clean preview line
    even when the source had hard line breaks or HTML mid-stream.
    """
    if not text:
        return ""
    cleaned = " ".join(text.split())
    if len(cleaned) <= max_chars:
        return cleaned
    # Cut at the last space before the limit so we don't end mid-word.
    cut = cleaned[: max_chars - 1].rsplit(" ", 1)[0]
    return f"{cut}…" if cut else cleaned[: max_chars - 1] + "…"
