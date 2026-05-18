"""confluence_search — light Confluence search via the ``search_confluence`` tool.

Sister provider to ``confluence`` (deep research). This one runs the
much cheaper full-text search tool through AI-Assist and yields one
finding per page hit. Use when the planner only needs a quick lookup,
not a multi-page synthesis.
"""
from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator, Iterable

from services.ai_assist_client import ai_assist
from services.research_providers._streaming import stream_agent_tool
from services.research_providers.base import (
    Finding,
    ProviderHealth,
    SearchProgress,
    _now_iso,
    make_snippet,
)

logger = logging.getLogger("projecthub.research.confluence_search")

_TOOL_NAME = "search_confluence"


def _parse(*, provider_key: str, tool_name: str, payload: dict) -> Iterable[Finding]:
    """Map ``search_confluence`` tool_result to Findings.

    Tool result data shape (from AI-Assist's search_confluence):
        {"results": [{"id" | "page_id", "title", "summary"|"body",
                      "url", "space"?, "labels"?}]}
    """
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict):
        rows = data.get("results") or data.get("items") or data.get("pages") or []
    elif isinstance(data, list):
        rows = data
    else:
        rows = []
    if not isinstance(rows, list):
        return []

    out: list[Finding] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ref = row.get("id") or row.get("page_id") or row.get("url")
        if not ref:
            continue
        title = str(row.get("title") or row.get("name") or ref)[:300]
        body = str(row.get("summary") or row.get("body") or row.get("excerpt") or "")
        out.append(Finding(
            provider_key=provider_key,
            source_ref=f"{tool_name}:{ref}",
            title=title,
            snippet=make_snippet(body),
            full_content=body or None,
            url=row.get("url"),
            timestamp=row.get("updated") or row.get("modified"),
            author=row.get("author") or row.get("contributor"),
            raw_metadata={
                k: v for k, v in row.items()
                if k not in {
                    "id", "page_id", "title", "name", "summary", "body",
                    "excerpt", "url", "updated", "modified", "author",
                    "contributor",
                }
            },
        ))
    return out


class ConfluenceSearchProvider:
    """Light-weight Confluence lookup via search tool."""

    key = "confluence_search"
    description = (
        "Schnelle Confluence-Volltextsuche (Top-N Seiten ohne Tief-Analyse). "
        "Wähle diesen Provider für gezielte Begriffs-Suche; für eine "
        "umfassende Recherche eines Spaces den Provider `confluence` nutzen."
    )
    typical_latency = "medium"
    side_effect = "external"
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
        limit = int(provider_settings.get("max_results", 10))
        include_body = bool(provider_settings.get("include_body", True))
        timeout_sec = float(provider_settings.get("timeout_sec", 60))

        args = {"query": query, "limit": limit, "include_body": include_body}
        async for event in stream_agent_tool(
            _TOOL_NAME, args, cancel,
            provider_key=self.key,
            parse_tool_result=_parse,
            timeout_sec=timeout_sec,
        ):
            yield event
