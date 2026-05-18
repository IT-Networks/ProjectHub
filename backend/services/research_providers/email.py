"""email — Exchange/Outlook search via AI-Assist's email_find tool.

Tool: ``email_find(text, filter)`` returns top-N emails with inline
bodies (subject/body/from/date). Falls per-message body is missing,
a follow-up ``email_read`` would be needed — we don't do that here
in v1 (saves a round trip; bodies are usually present for top-5).
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

logger = logging.getLogger("projecthub.research.email")

_TOOL_NAME = "email_find"


def _parse(*, provider_key: str, tool_name: str, payload: dict) -> Iterable[Finding]:
    """Map ``email_find`` tool_result to Findings.

    Tool data shape: ``{"results": [{"id"|"message_id", "subject", "body"|
    "snippet", "from", "to"?, "date"|"received"|"timestamp", "folder"?}]}``
    """
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict):
        rows = (
            data.get("results")
            or data.get("messages")
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
        ref = row.get("id") or row.get("message_id") or row.get("entry_id")
        if not ref:
            continue
        subject = str(row.get("subject") or row.get("title") or "(kein Betreff)")[:300]
        body = str(row.get("body") or row.get("snippet") or row.get("preview") or "")
        sender = row.get("from") or row.get("sender") or row.get("author")
        out.append(Finding(
            provider_key=provider_key,
            source_ref=f"email:{ref}",
            title=subject,
            snippet=make_snippet(body),
            full_content=body or None,
            url=None,
            timestamp=row.get("date") or row.get("received") or row.get("timestamp"),
            author=str(sender) if sender else None,
            raw_metadata={
                k: v for k, v in row.items()
                if k not in {
                    "id", "message_id", "entry_id", "subject", "title",
                    "body", "snippet", "preview", "from", "sender", "author",
                    "date", "received", "timestamp",
                }
            },
        ))
    return out


class EmailProvider:
    """Exchange/Outlook email search via the ``email_find`` tool."""

    key = "email"
    description = (
        "Suche im Exchange-Postfach (Top-N E-Mails mit Inhalt). Gut für "
        "Kommunikations-Kontext, Entscheidungen, Threads."
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
        max_results = int(provider_settings.get("max_results", 10))
        timeout_sec = float(provider_settings.get("timeout_sec", 60))

        # Build the smart-tool filter string. days_back / folders /
        # sender map to the same key:value DSL the smart_email tool uses.
        filter_parts: list[str] = []
        if days_back := provider_settings.get("days_back"):
            filter_parts.append(f"seit:{days_back}t")
        if sender := provider_settings.get("sender"):
            filter_parts.append(f"from:{sender}")
        if folders := provider_settings.get("folders"):
            if isinstance(folders, list) and folders:
                filter_parts.append(f"folder:{folders[0]}")
            elif isinstance(folders, str):
                filter_parts.append(f"folder:{folders}")
        filter_parts.append(f"limit:{max_results}")

        args = {
            "text": query,
            "filter": " ".join(filter_parts),
        }

        async for event in stream_agent_tool(
            _TOOL_NAME, args, cancel,
            provider_key=self.key,
            parse_tool_result=_parse,
            timeout_sec=timeout_sec,
        ):
            yield event
