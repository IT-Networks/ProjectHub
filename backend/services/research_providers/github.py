"""github — GitHub Enterprise search via AI-Assist's github tools.

Strategy: ``github_search_repos`` for repo-level hits when no specific
repo is configured; ``github_list_prs`` per configured repo otherwise.
The two tool results share the Finding shape — one stream regardless.

side_effect = "external" — touches the GitHub Enterprise API on the
configured base_url.
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

logger = logging.getLogger("projecthub.research.github")


def _parse_repos(*, provider_key: str, tool_name: str, payload: dict) -> Iterable[Finding]:
    """Map ``github_search_repos`` to Findings."""
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict):
        rows = data.get("results") or data.get("repos") or data.get("items") or []
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
        full_name = row.get("full_name") or row.get("name")
        if not full_name:
            continue
        out.append(Finding(
            provider_key=provider_key,
            source_ref=f"github:repo:{full_name}",
            title=str(full_name)[:300],
            snippet=make_snippet(str(row.get("description") or "")),
            full_content=row.get("description"),
            url=row.get("html_url") or row.get("url"),
            timestamp=row.get("updated_at") or row.get("pushed_at"),
            author=row.get("owner"),
            raw_metadata={
                "stars": row.get("stargazers_count"),
                "language": row.get("language"),
                **{k: v for k, v in row.items() if k not in {
                    "name", "full_name", "description", "html_url", "url",
                    "updated_at", "pushed_at", "owner", "stargazers_count",
                    "language",
                }},
            },
        ))
    return out


def _parse_prs(*, provider_key: str, tool_name: str, payload: dict) -> Iterable[Finding]:
    """Map ``github_list_prs`` to Findings."""
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict):
        rows = data.get("results") or data.get("prs") or data.get("items") or []
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
        number = row.get("number") or row.get("id")
        if not number:
            continue
        repo = row.get("repo") or row.get("base_repo") or "repo"
        out.append(Finding(
            provider_key=provider_key,
            source_ref=f"github:pr:{repo}#{number}",
            title=f"PR #{number}: {row.get('title') or '(no title)'}"[:300],
            snippet=make_snippet(str(row.get("body") or row.get("description") or "")),
            full_content=row.get("body"),
            url=row.get("html_url") or row.get("url"),
            timestamp=row.get("updated_at") or row.get("created_at"),
            author=row.get("user") or row.get("author"),
            raw_metadata={
                "state": row.get("state"),
                "repo": repo,
                "number": number,
                **{k: v for k, v in row.items() if k not in {
                    "number", "id", "title", "body", "description",
                    "html_url", "url", "updated_at", "created_at",
                    "user", "author", "state", "repo", "base_repo",
                }},
            },
        ))
    return out


class GitHubProvider:
    """GitHub-Enterprise search (repos + PRs) via AI-Assist tools."""

    key = "github"
    description = (
        "Suche in GitHub-Enterprise: Repositories und Pull-Requests. Gut "
        "für Implementations-Historie, Review-Diskussionen, Architektur-"
        "Entscheidungen die per PR-Beschreibung dokumentiert wurden."
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
        max_results = int(provider_settings.get("max_results", 10))
        timeout_sec = float(provider_settings.get("timeout_sec", 60))
        default_repo = provider_settings.get("default_repo")
        state = provider_settings.get("pr_state", "all")  # open | closed | all

        # If a specific repo is set, list PRs there directly — that's
        # the common case for the planner's "what's happening in X" path.
        if default_repo:
            args = {"repo": default_repo, "state": state, "limit": max_results}
            async for event in stream_agent_tool(
                "github_list_prs", args, cancel,
                provider_key=self.key,
                parse_tool_result=_parse_prs,
                timeout_sec=timeout_sec,
            ):
                yield event
            return

        # No repo configured → broader search across repos.
        args = {"query": query, "limit": max_results}
        if org := provider_settings.get("default_org"):
            args["org"] = org
        async for event in stream_agent_tool(
            "github_search_repos", args, cancel,
            provider_key=self.key,
            parse_tool_result=_parse_repos,
            timeout_sec=timeout_sec,
        ):
            yield event
