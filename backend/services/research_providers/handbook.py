"""handbook — internal handbook FTS via AI-Assist's research router.

AI-Assist's ``POST /api/research/execute`` orchestrator with
``sources=["handbook"]`` searches the local handbook FTS index and
returns service-grouped findings. No external system, no auth needed
— just the local handbook path config in AI-Assist.

We use the dedicated research-router endpoint rather than the generic
agent_stream + tool route because the research-router already does
multi-source orchestration; for a single-source handbook query it's
the leanest path. Returns a normalised result dict similar to the
Confluence Deep-Research endpoint.
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

logger = logging.getLogger("projecthub.research.handbook")


class HandbookProvider:
    """Internal handbook FTS via ``/api/research/execute``."""

    key = "handbook"
    description = (
        "Internes Handbook (Service-Definitionen + Felder + Funktionen). "
        "Schnelle FTS-Suche, kein LLM-Aufruf in der ersten Stufe."
    )
    typical_latency = "fast"
    side_effect = "read"  # local-FTS on AI-Assist side; no third-party reach
    default_enabled = False

    async def health(self) -> ProviderHealth:
        try:
            ok = await ai_assist.health_check()
        except Exception as e:  # noqa: BLE001
            return ProviderHealth(
                ok=False, detail=f"ai_assist_unreachable: {e!s}"[:120],
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

        max_results = int(provider_settings.get("max_results", 10))

        yield SearchProgress(kind="status", status_text="Suche im Handbook")

        body = {
            "query": query,
            "sources": ["handbook"],
            "scope": "internal-only",
            "max_results": max_results,
        }

        try:
            # ``post`` is a generic helper on the client; we use it
            # directly rather than a dedicated method because there's
            # no semantic added value in another wrapper.
            result = await ai_assist.post("/api/research/execute", body=body)
        except Exception as e:  # noqa: BLE001
            logger.warning("handbook stream failed: %s", e)
            yield SearchProgress(kind="error", error=f"handbook: {e!s}"[:200])
            yield SearchProgress(kind="done", status_text="error")
            return

        if cancel.is_set():
            yield SearchProgress(kind="done", status_text="cancelled")
            return

        if not isinstance(result, dict):
            yield SearchProgress(kind="done", status_text="ok")
            return

        rows = result.get("results") or result.get("findings") or []
        if not isinstance(rows, list):
            rows = []

        for row in rows:
            if cancel.is_set():
                yield SearchProgress(kind="done", status_text="cancelled")
                return
            if not isinstance(row, dict):
                continue
            ref = row.get("id") or row.get("path") or row.get("section")
            if not ref:
                continue
            title = str(row.get("title") or row.get("name") or ref)[:300]
            body_text = str(row.get("content") or row.get("body") or row.get("snippet") or "")
            yield SearchProgress(
                kind="finding",
                finding=Finding(
                    provider_key=self.key,
                    source_ref=f"handbook:{ref}",
                    title=title,
                    snippet=make_snippet(body_text),
                    full_content=body_text or None,
                    url=None,
                    score=row.get("score"),
                    raw_metadata={
                        "service": row.get("service"),
                        "section": row.get("section"),
                        "type": row.get("type"),
                    },
                ),
            )

        yield SearchProgress(kind="done", status_text="ok")
