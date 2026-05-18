"""jenkins — Jenkins read-only inspection via AI-Assist's jenkins tools.

**Strict read-only**: this provider MUST NOT call ``jenkins_trigger_build``
even when the planner suggests it. The tool blacklist in ``_streaming.py``
enforces that globally, but we add a defense-in-depth assertion at
provider construction time so a regression that loosens the blacklist
would still be caught here.

Tools used: ``jenkins_job_status`` (one job summary) and
``jenkins_build_info`` (one build's console + result). The planner picks
which based on the query — typically ``job_status`` for "is service-X
green?" and ``build_info`` for "what failed in build N?".

We expose ``jenkins_job_status`` as the primary entry. ``build_info``
is reachable when ``provider_settings.build`` carries a specific build
number — both kept in a single stream for the orchestrator.
"""
from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator, Iterable

from services.ai_assist_client import ai_assist
from services.research_providers._streaming import (
    is_tool_allowed,
    stream_agent_tool,
)
from services.research_providers.base import (
    Finding,
    ProviderHealth,
    SearchProgress,
    _now_iso,
    make_snippet,
)

logger = logging.getLogger("projecthub.research.jenkins")

#: Read-only tools this provider may call. The defensive assertion in
#: ``__init__`` checks all three are NOT blacklisted (sanity) and that
#: the write-side ``jenkins_trigger_build`` IS blacklisted.
_ALLOWED_TOOLS = ("jenkins_job_status", "jenkins_build_info", "jenkins_queue_info")


def _parse_status(*, provider_key: str, tool_name: str, payload: dict) -> Iterable[Finding]:
    """Map ``jenkins_job_status`` to Findings."""
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return []

    name = data.get("name") or data.get("job") or "?"
    last_build = data.get("last_build") or data.get("lastBuild") or {}
    status = data.get("color") or data.get("status") or "unknown"
    body = (
        f"Status: {status}. "
        f"Last build: #{last_build.get('number') if isinstance(last_build, dict) else '?'} — "
        f"{last_build.get('result') if isinstance(last_build, dict) else '?'}"
    )
    return [Finding(
        provider_key=provider_key,
        source_ref=f"jenkins:job:{name}",
        title=f"Jenkins {name}: {status}",
        snippet=make_snippet(body),
        full_content=body,
        url=data.get("url"),
        timestamp=last_build.get("timestamp") if isinstance(last_build, dict) else None,
        author=None,
        raw_metadata={
            "last_build": last_build,
            "in_queue": data.get("in_queue", False),
            **{k: v for k, v in data.items() if k not in {
                "name", "job", "color", "status", "last_build", "lastBuild",
                "url", "in_queue",
            }},
        },
    )]


def _parse_build(*, provider_key: str, tool_name: str, payload: dict) -> Iterable[Finding]:
    """Map ``jenkins_build_info`` to Findings (console excerpt + result)."""
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return []

    job = data.get("job") or data.get("name") or "?"
    number = data.get("number") or data.get("build_number")
    if number is None:
        return []
    result = data.get("result") or "unknown"
    console = str(data.get("console") or data.get("log_excerpt") or "")
    return [Finding(
        provider_key=provider_key,
        source_ref=f"jenkins:build:{job}#{number}",
        title=f"Jenkins {job} #{number}: {result}"[:300],
        snippet=make_snippet(console),
        full_content=console or None,
        url=data.get("url"),
        timestamp=data.get("timestamp"),
        author=None,
        raw_metadata={
            "result": result, "duration_ms": data.get("duration"),
            "number": number, "job": job,
            **{k: v for k, v in data.items() if k not in {
                "job", "name", "number", "build_number", "result", "console",
                "log_excerpt", "url", "timestamp", "duration",
            }},
        },
    )]


class JenkinsProvider:
    """Jenkins read-only inspection (job status + build info)."""

    key = "jenkins"
    description = (
        "Jenkins-Status für konfigurierte Jobs/Builds. Liefert Job-Health "
        "+ Build-Konsole. Strikt read-only — Trigger sind serverseitig "
        "blockiert."
    )
    typical_latency = "medium"
    side_effect = "external"
    default_enabled = False

    def __init__(self):
        # Defense-in-depth: the global tool blacklist must protect us
        # from jenkins_trigger_build. If a regression ever loosens it,
        # this construction-time assertion stops the provider from
        # registering rather than silently going through.
        from services.research_providers._streaming import _TOOL_BLACKLIST

        assert "jenkins_trigger_build" in _TOOL_BLACKLIST, (
            "regression: jenkins_trigger_build dropped from blacklist — "
            "Auto-Mode must NEVER mutate Jenkins"
        )
        # Sanity: every tool we *will* call must NOT be blacklisted —
        # would mean the read-side got accidentally banned, which is
        # a different kind of bug worth catching loudly.
        for t in _ALLOWED_TOOLS:
            assert is_tool_allowed(t), f"read-side tool {t} was blacklisted"

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
        job = provider_settings.get("job") or query.strip()
        build = provider_settings.get("build")

        # Branch: when a specific build number is set, fetch build_info
        # (deeper detail). Otherwise summarise the job status.
        if build is not None and job:
            args = {"job": job, "build": int(build)}
            async for event in stream_agent_tool(
                "jenkins_build_info", args, cancel,
                provider_key=self.key,
                parse_tool_result=_parse_build,
                timeout_sec=timeout_sec,
            ):
                yield event
            return

        if not job:
            yield SearchProgress(
                kind="error",
                error="jenkins: keine Job-Angabe in query oder provider_settings.job",
            )
            yield SearchProgress(kind="done", status_text="config_missing")
            return

        args = {"job": job}
        async for event in stream_agent_tool(
            "jenkins_job_status", args, cancel,
            provider_key=self.key,
            parse_tool_result=_parse_status,
            timeout_sec=timeout_sec,
        ):
            yield event
