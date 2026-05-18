"""jira — Jira issue search via AI-Assist's find_jira tool.

The smart tool ``find_jira`` accepts a natural-language ``text`` plus a
key:value DSL ``filter`` string (project:PROJ status:open assignee:me…)
and returns the matching issues. We surface each issue as one Finding
with the issue key in ``source_ref`` (PROJ-1234), making the find both
human-readable in the UI and stable for re-search idempotency.
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

logger = logging.getLogger("projecthub.research.jira")

_TOOL_NAME = "find_jira"


def _parse(*, provider_key: str, tool_name: str, payload: dict) -> Iterable[Finding]:
    """Map ``find_jira`` tool_result to Findings.

    Tool data shape: ``{"issues"|"results": [{"key"|"id", "summary"|
    "title", "description"|"body", "status", "assignee", "priority",
    "type", "url", "created", "updated"}]}``
    """
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict):
        rows = (
            data.get("issues")
            or data.get("results")
            or data.get("items")
            or []
        )
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
        key = row.get("key") or row.get("id")
        if not key:
            continue
        title = str(row.get("summary") or row.get("title") or f"Issue {key}")[:300]
        body = str(row.get("description") or row.get("body") or "")
        out.append(Finding(
            provider_key=provider_key,
            source_ref=f"jira:{key}",
            title=f"{key}: {title}",
            snippet=make_snippet(body),
            full_content=body or None,
            url=row.get("url") or row.get("self"),
            timestamp=row.get("updated") or row.get("created"),
            author=row.get("assignee") or row.get("reporter"),
            raw_metadata={
                "key": key,
                "status": row.get("status"),
                "priority": row.get("priority"),
                "type": row.get("type") or row.get("issuetype"),
                **{k: v for k, v in row.items()
                   if k not in {
                       "key", "id", "summary", "title", "description", "body",
                       "status", "priority", "type", "issuetype",
                       "url", "self", "updated", "created", "assignee", "reporter",
                   }},
            },
        ))
    return out


class JiraProvider:
    """Jira issue search via the ``find_jira`` smart tool."""

    key = "jira"
    description = (
        "Suche in Jira-Tickets. Gut für Bug-Historie, offene Issues, "
        "Architektur-Entscheidungen die als Story/Spike erfasst sind."
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
        max_results = int(provider_settings.get("max_results", 20))
        timeout_sec = float(provider_settings.get("timeout_sec", 60))

        # Compose the smart-tool filter string. Settings keys mirror the
        # smart_jira DSL (project: status: assignee: priority: type:).
        filter_parts: list[str] = []
        if proj := provider_settings.get("default_project"):
            filter_parts.append(f"project:{proj}")
        statuses = provider_settings.get("statuses")
        if isinstance(statuses, list) and statuses:
            filter_parts.append(f"status:{','.join(statuses)}")
        elif isinstance(statuses, str) and statuses:
            filter_parts.append(f"status:{statuses}")
        if assignee := provider_settings.get("assignee"):
            filter_parts.append(f"assignee:{assignee}")
        if priority := provider_settings.get("priority"):
            filter_parts.append(f"priority:{priority}")
        filter_parts.append(f"limit:{max_results}")

        args = {"text": query, "filter": " ".join(filter_parts)}

        async for event in stream_agent_tool(
            _TOOL_NAME, args, cancel,
            provider_key=self.key,
            parse_tool_result=_parse,
            timeout_sec=timeout_sec,
        ):
            yield event
