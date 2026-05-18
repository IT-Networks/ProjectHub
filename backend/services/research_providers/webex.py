"""webex — Webex room search via AI-Assist's webex tools.

Strategy: ``webex_search_all_rooms`` for cross-room hits when no
specific room is configured; ``webex_messages`` per configured room
when ``rooms=[...]`` is set in provider_settings. The two tool results
share the same Finding shape so the planner sees one consistent stream.
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

logger = logging.getLogger("projecthub.research.webex")


def _parse(*, provider_key: str, tool_name: str, payload: dict) -> Iterable[Finding]:
    """Map webex_search_all_rooms / webex_messages tool_result to Findings.

    Tool data shape: ``{"results"|"messages": [{"id"|"messageId",
    "text"|"markdown", "personEmail"|"author", "roomId"|"room",
    "created"|"timestamp"}]}``
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
        ref = row.get("id") or row.get("messageId")
        if not ref:
            continue
        text = str(row.get("text") or row.get("markdown") or row.get("body") or "")
        sender = (
            row.get("personEmail")
            or row.get("author")
            or row.get("sender")
            or row.get("personDisplayName")
        )
        room = row.get("roomId") or row.get("room") or row.get("space")
        out.append(Finding(
            provider_key=provider_key,
            source_ref=f"webex:{ref}",
            title=f"Webex: {make_snippet(text, max_chars=80)}",
            snippet=make_snippet(text),
            full_content=text or None,
            url=None,
            timestamp=row.get("created") or row.get("timestamp") or row.get("ts"),
            author=str(sender) if sender else None,
            raw_metadata={
                "room": room,
                **{k: v for k, v in row.items()
                   if k not in {
                       "id", "messageId", "text", "markdown", "body",
                       "personEmail", "author", "sender", "personDisplayName",
                       "roomId", "room", "space", "created", "timestamp", "ts",
                   }},
            },
        ))
    return out


class WebexProvider:
    """Webex room/message search via AI-Assist tools."""

    key = "webex"
    description = (
        "Suche in Webex-Teams-Räumen + Nachrichten. Gut für Team-"
        "Diskussionen, Entscheidungen, Zitate."
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
        rooms = provider_settings.get("rooms") or []

        # Two-tool branch:
        #   * with explicit rooms → query webex_messages per room (parallel
        #     would be ideal, but the orchestrator already runs providers
        #     in parallel; we keep one stream linear inside this provider).
        #   * without → webex_search_all_rooms once.
        if isinstance(rooms, list) and rooms:
            for room_id in rooms:
                if cancel.is_set():
                    yield SearchProgress(kind="done", status_text="cancelled")
                    return
                args = {"room_id": room_id, "text": query, "limit": max_results}
                async for event in stream_agent_tool(
                    "webex_messages", args, cancel,
                    provider_key=self.key,
                    parse_tool_result=_parse,
                    timeout_sec=timeout_sec,
                ):
                    # Don't bubble the inner "done" — we still have more
                    # rooms to query. Swap it for a status to stay legal.
                    if event.kind == "done":
                        yield SearchProgress(
                            kind="status",
                            status_text=f"room {room_id} → {event.status_text or 'ok'}",
                        )
                    else:
                        yield event
            yield SearchProgress(kind="done", status_text="ok")
            return

        # No rooms configured → cross-room search.
        args = {"text": query, "limit": max_results}
        async for event in stream_agent_tool(
            "webex_search_all_rooms", args, cancel,
            provider_key=self.key,
            parse_tool_result=_parse,
            timeout_sec=timeout_sec,
        ):
            yield event
