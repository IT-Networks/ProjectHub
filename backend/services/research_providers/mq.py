"""mq — message-queue inspection via AI-Assist's mq_list_queues tool.

The MQ surface in AI-Assist is intentionally narrow: ``mq_list_queues``
returns the configured queues with name + url + service + role
(``read`` / ``trigger`` / ``both``). Auto-Mode is read-only — we only
expose read-role queues and surface each as a Finding. Trigger queues
are NOT enumerated to avoid tempting the planner to call them.

This provider is the simplest of Tier-2: no per-queue fetch in v1 (the
list-only output is usually sufficient for the planner to decide where
to look manually). Per-queue reads can land in a later phase if the
need surfaces.
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

logger = logging.getLogger("projecthub.research.mq")

_TOOL_NAME = "mq_list_queues"


def _parse(*, provider_key: str, tool_name: str, payload: dict) -> Iterable[Finding]:
    """Map ``mq_list_queues`` to Findings — read-role queues only.

    Shape: ``{"queues": [{"id"|"name", "url", "service", "role"}]}``.
    Trigger-only queues are filtered out so Auto-Mode never points the
    planner at a write surface.
    """
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict):
        rows = data.get("queues") or data.get("results") or data.get("items") or []
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
        role = (row.get("role") or "read").lower()
        # Read-only filter: keep "read" and "both", drop "trigger".
        if role not in ("read", "both", "read_only", "readonly"):
            continue
        qid = row.get("id") or row.get("name")
        if not qid:
            continue
        service = row.get("service") or "?"
        body = (
            f"Queue {qid} (service={service}, role={role}). "
            f"URL: {row.get('url') or '(not set)'}"
        )
        out.append(Finding(
            provider_key=provider_key,
            source_ref=f"mq:{qid}",
            title=f"MQ {qid} — {service}"[:300],
            snippet=make_snippet(body),
            full_content=body,
            url=row.get("url"),
            timestamp=None,
            author=None,
            raw_metadata={
                "service": service,
                "role": role,
                "queue_id": qid,
                **{k: v for k, v in row.items() if k not in {
                    "id", "name", "url", "service", "role",
                }},
            },
        ))
    return out


class MQProvider:
    """Read-side MQ inspection via the ``mq_list_queues`` tool."""

    key = "mq"
    description = (
        "Übersicht der konfigurierten Read-Queues (Message-Queues). Auto-"
        "Mode kann hier NUR lesen — Trigger-Queues werden gefiltert."
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
        return ProviderHealth(
            ok=ok, detail="ai_assist_ok" if ok else "ai_assist_down",
            last_checked_at=_now_iso(),
        )

    async def stream(
        self,
        query: str,
        provider_settings: dict,
        cancel: asyncio.Event,
        *,
        project_id: str,
    ) -> AsyncIterator[SearchProgress]:
        timeout_sec = float(provider_settings.get("timeout_sec", 30))
        # mq_list_queues has no filter arg; we ignore the query for the
        # tool call but pass it through the directive so the LLM
        # doesn't try to "interpret" the listing for us.
        args: dict = {}
        if service := provider_settings.get("service"):
            args["service"] = service

        async for event in stream_agent_tool(
            _TOOL_NAME, args, cancel,
            provider_key=self.key,
            parse_tool_result=_parse,
            timeout_sec=timeout_sec,
        ):
            yield event
