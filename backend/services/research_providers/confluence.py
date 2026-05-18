"""confluence — Confluence Deep-Research via AI-Assist's dedicated endpoint.

Sonderfall im Tier-2-Sprint: AI-Assist's ``POST /api/research/confluence``
implements its own full pipeline (Discovery → Planning → Execution →
Synthesis) and returns a synthesised result. We just adapt that result
into ``SearchProgress`` / ``Finding`` events.

Rerank is skipped for this provider — the upstream synthesis already
chose the relevant pages, so a second Stage-2 over a handful of
already-curated findings is wasted budget. The pipeline orchestrator
recognises this provider as "pre-synthesised" via the ``score`` field
on each Finding (we set it to the upstream confidence).

Latency: slow (30 s – 5 min). The orchestrator's per-provider
``timeout_sec`` setting applies; the AI-Assist client itself has its
own LLM-timeout that caps the upstream call.
"""
from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

from services.ai_assist_client import ai_assist
from services.research_providers.base import (
    Finding,
    ProviderHealth,
    SearchProgress,
    _now_iso,
    make_snippet,
)

logger = logging.getLogger("projecthub.research.confluence")


class ConfluenceProvider:
    """Provider for Confluence Deep-Research (synthesised markdown)."""

    key = "confluence"
    description = (
        "Confluence-Tiefenrecherche über einen Space oder Seitenbaum "
        "(inklusive PDF-Anhänge). Liefert bereits synthetisiertes Wissen — "
        "ideal für Architektur-/Konzept-Themen."
    )
    typical_latency = "slow"
    side_effect = "external"
    default_enabled = False

    async def health(self) -> ProviderHealth:
        """Reachable iff AI-Assist itself is up — the endpoint is mandatory
        on every v2 deployment."""
        try:
            ok = await ai_assist.health_check()
        except Exception as e:  # noqa: BLE001
            return ProviderHealth(
                ok=False,
                detail=f"ai_assist_unreachable: {e!s}"[:120],
                last_checked_at=_now_iso(),
            )
        if not ok:
            return ProviderHealth(
                ok=False, detail="ai_assist_down", last_checked_at=_now_iso()
            )
        return ProviderHealth(
            ok=True, detail="ai_assist_ok", last_checked_at=_now_iso()
        )

    async def stream(
        self,
        query: str,
        provider_settings: dict,
        cancel: asyncio.Event,
        *,
        project_id: str,
    ) -> AsyncIterator[SearchProgress]:
        if cancel.is_set():
            yield SearchProgress(kind="done", status_text="cancelled")
            return

        space_key = provider_settings.get("space_key") or None
        url = provider_settings.get("url") or None
        include_children = bool(provider_settings.get("include_children", True))

        yield SearchProgress(
            kind="status",
            status_text=f"Confluence Deep-Research ({'space=' + space_key if space_key else 'topic-search'})",
        )

        try:
            result = await ai_assist.research_confluence(
                query,
                space_key=space_key,
                url=url,
                include_children=include_children,
            )
        except ConnectionError as e:
            yield SearchProgress(kind="error", error=f"confluence: {e!s}"[:200])
            yield SearchProgress(kind="done", status_text="error")
            return
        except Exception as e:  # noqa: BLE001
            logger.warning("confluence research raised: %s", e)
            yield SearchProgress(kind="error", error=f"confluence: {e!s}"[:200])
            yield SearchProgress(kind="done", status_text="error")
            return

        if cancel.is_set():
            yield SearchProgress(kind="done", status_text="cancelled")
            return

        # The endpoint returns ``findings: [{title, summary, page_id,
        # url, confidence?}]`` plus a top-level ``summary`` + ``markdown``
        # synthesis. We emit each finding individually.
        findings_payload = result.get("findings") if isinstance(result, dict) else None
        if not isinstance(findings_payload, list) or not findings_payload:
            # Some queries land on the synthesis-only path with zero
            # discrete findings — emit the top-level summary as a single
            # finding so the planner still has *something* to work with.
            summary = (result.get("summary") if isinstance(result, dict) else None) or ""
            if summary:
                yield SearchProgress(
                    kind="finding",
                    finding=Finding(
                        provider_key=self.key,
                        source_ref=f"confluence:topic:{abs(hash(query)) & 0xFFFFFFFF:x}",
                        title=f"Confluence-Synthese: {query[:120]}",
                        snippet=make_snippet(summary),
                        full_content=summary,
                        url=None,
                        score=0.5,
                        raw_metadata={
                            "pages_analyzed": result.get("pages_analyzed"),
                            "pdfs_analyzed": result.get("pdfs_analyzed"),
                            "errors": result.get("errors") or [],
                        },
                    ),
                )
            yield SearchProgress(kind="done", status_text="ok")
            return

        for entry in findings_payload:
            if cancel.is_set():
                yield SearchProgress(kind="done", status_text="cancelled")
                return
            if not isinstance(entry, dict):
                continue
            page_id = entry.get("page_id") or entry.get("id") or ""
            title = str(entry.get("title") or entry.get("name") or "(ohne Titel)")[:300]
            summary = str(
                entry.get("summary") or entry.get("snippet") or entry.get("body") or ""
            )
            confidence = entry.get("confidence")
            try:
                score = float(confidence) if confidence is not None else 0.7
            except (TypeError, ValueError):
                score = 0.7
            yield SearchProgress(
                kind="finding",
                finding=Finding(
                    provider_key=self.key,
                    source_ref=f"confluence:page:{page_id}" if page_id else
                               f"confluence:f:{abs(hash(title)) & 0xFFFFFFFF:x}",
                    title=title,
                    snippet=make_snippet(summary),
                    full_content=summary or None,
                    url=entry.get("url"),
                    timestamp=entry.get("updated") or entry.get("timestamp"),
                    author=entry.get("author"),
                    score=score,
                    raw_metadata={
                        k: v for k, v in entry.items()
                        if k not in {
                            "id", "page_id", "title", "name", "summary",
                            "snippet", "body", "url", "updated", "timestamp",
                            "author", "confidence",
                        }
                    },
                ),
            )

        yield SearchProgress(kind="done", status_text="ok")
