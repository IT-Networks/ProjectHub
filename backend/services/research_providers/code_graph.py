"""code_graph — Java/Python code-graph search via AI-Assist's graph_search tool.

The smart-tool ``graph_search(query, type, language, limit)`` returns
symbol-level hits across the configured Java/Python source trees. We
expose ``language`` and ``type`` as provider_settings, default
``language=java`` (most projects on the team's setup are Java).

side_effect = "read" — pure local source-tree analysis; no third-party
reach despite the AI-Assist round-trip.
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

logger = logging.getLogger("projecthub.research.code_graph")

_TOOL_NAME = "graph_search"


def _parse(*, provider_key: str, tool_name: str, payload: dict) -> Iterable[Finding]:
    """Map ``graph_search`` tool_result to Findings.

    Shape: ``{"results"|"symbols": [{"id"|"qualified_name", "name",
    "type", "language", "file"|"path", "line", "signature"|"docstring"}]}``
    """
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict):
        rows = (
            data.get("results")
            or data.get("symbols")
            or data.get("matches")
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
        ref = row.get("id") or row.get("qualified_name") or row.get("fqn")
        if not ref:
            continue
        sym_type = row.get("type") or row.get("kind") or "symbol"
        title = f"{sym_type}: {row.get('name') or ref}"
        body = str(
            row.get("signature") or row.get("docstring") or row.get("summary") or ""
        )
        file_path = row.get("file") or row.get("path")
        out.append(Finding(
            provider_key=provider_key,
            source_ref=f"code:{ref}",
            title=title[:300],
            snippet=make_snippet(body) or (file_path or "")[:300],
            full_content=body or None,
            url=None,
            timestamp=None,
            author=None,
            raw_metadata={
                "type": sym_type,
                "language": row.get("language"),
                "file": file_path,
                "line": row.get("line"),
                **{k: v for k, v in row.items() if k not in {
                    "id", "qualified_name", "fqn", "name", "type", "kind",
                    "signature", "docstring", "summary", "file", "path",
                    "line", "language",
                }},
            },
        ))
    return out


class CodeGraphProvider:
    """Code-graph search via the ``graph_search`` smart tool."""

    key = "code_graph"
    description = (
        "Symbol-Suche im Java/Python-Code-Graph. Gut für Implementation-"
        "Pfade, Klassen/Methoden-Lookup, Impact-Analyse."
    )
    typical_latency = "fast"
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
        max_results = int(provider_settings.get("max_results", 15))
        timeout_sec = float(provider_settings.get("timeout_sec", 30))
        language = provider_settings.get("language", "java")
        sym_type = provider_settings.get("type")  # class | method | module | None=any

        args: dict = {"query": query, "language": language, "limit": max_results}
        if sym_type:
            args["type"] = sym_type

        async for event in stream_agent_tool(
            _TOOL_NAME, args, cancel,
            provider_key=self.key,
            parse_tool_result=_parse,
            timeout_sec=timeout_sec,
        ):
            yield event
