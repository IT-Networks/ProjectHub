"""log_servers — application/WLP log search via AI-Assist tools.

AI-Assist exposes ``log_grep`` (pattern + log_id) and ``search_logs``
(structured pattern). We use ``log_grep`` as the primary entry — it
matches the planner's natural-language style; for per-stage targeted
hits the orchestrator can fall back to ``search_logs`` later (not in v1).

side_effect = "read" — the upstream tools only read uploaded log
caches; they never mutate the source server.
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

logger = logging.getLogger("projecthub.research.log_servers")

_TOOL_NAME = "log_grep"


def _parse(*, provider_key: str, tool_name: str, payload: dict) -> Iterable[Finding]:
    """Map ``log_grep`` tool_result to Findings.

    Shape: ``{"matches"|"results": [{"log_id"|"file", "line_number"|"line",
    "context"|"text", "level"?, "timestamp"?}]}``
    """
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict):
        rows = (
            data.get("matches")
            or data.get("results")
            or data.get("hits")
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
        log_id = row.get("log_id") or row.get("file") or row.get("server")
        line_no = row.get("line_number") or row.get("line")
        if log_id is None or line_no is None:
            continue
        ref = f"{log_id}#L{line_no}"
        ctx = str(row.get("context") or row.get("text") or row.get("message") or "")
        level = row.get("level") or row.get("severity")
        title = f"{level or 'LOG'} @ {log_id}:{line_no}"
        out.append(Finding(
            provider_key=provider_key,
            source_ref=f"log:{ref}",
            title=title[:300],
            snippet=make_snippet(ctx),
            full_content=ctx or None,
            url=None,
            timestamp=row.get("timestamp"),
            author=None,
            raw_metadata={
                "log_id": log_id, "line": line_no, "level": level,
                **{k: v for k, v in row.items() if k not in {
                    "log_id", "file", "server", "line_number", "line",
                    "context", "text", "message", "level", "severity",
                    "timestamp",
                }},
            },
        ))
    return out


class LogServersProvider:
    """Application/WLP log search via the ``log_grep`` tool."""

    key = "log_servers"
    description = (
        "Suche in zuvor hochgeladenen WLP-/Application-Logs. Gut für "
        "Fehlersuche, Stack-Traces, Korrelation zu Incident-Tickets."
    )
    typical_latency = "medium"
    side_effect = "read"
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
        max_results = int(provider_settings.get("max_results", 20))
        context_lines = int(provider_settings.get("context_lines", 2))
        timeout_sec = float(provider_settings.get("timeout_sec", 60))
        log_ids = provider_settings.get("log_ids") or []

        # If specific log_ids are configured, iterate; otherwise let the
        # tool resolve which logs to grep (it knows the cached uploads).
        if isinstance(log_ids, list) and log_ids:
            for log_id in log_ids:
                if cancel.is_set():
                    yield SearchProgress(kind="done", status_text="cancelled")
                    return
                args = {
                    "log_id": log_id, "pattern": query,
                    "context_lines": context_lines, "limit": max_results,
                }
                async for event in stream_agent_tool(
                    _TOOL_NAME, args, cancel,
                    provider_key=self.key,
                    parse_tool_result=_parse,
                    timeout_sec=timeout_sec,
                ):
                    if event.kind == "done":
                        yield SearchProgress(
                            kind="status",
                            status_text=f"log {log_id} → {event.status_text or 'ok'}",
                        )
                    else:
                        yield event
            yield SearchProgress(kind="done", status_text="ok")
            return

        args = {"pattern": query, "context_lines": context_lines, "limit": max_results}
        async for event in stream_agent_tool(
            _TOOL_NAME, args, cancel,
            provider_key=self.key,
            parse_tool_result=_parse,
            timeout_sec=timeout_sec,
        ):
            yield event
