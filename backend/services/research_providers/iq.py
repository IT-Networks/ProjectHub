"""iq — Sonatype IQ findings via AI-Assist's iq_findings tool.

Special prerequisite: ``app_id`` MUST be present in ``provider_settings``
(or in the project's default_app config) — IQ has no project-agnostic
"search". A missing app_id raises a clean error event so the planner
can surface the misconfiguration in the UI rather than failing silent.

Tool: ``iq_findings(app_id, organization_id?)`` returns violations +
applicable waivers + remediation hints. We surface each violation as
one Finding and put the waiver/template info in raw_metadata so the
orchestrator can show the "fix" alongside the "problem".
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

logger = logging.getLogger("projecthub.research.iq")

_TOOL_NAME = "iq_findings"


def _parse(*, provider_key: str, tool_name: str, payload: dict) -> Iterable[Finding]:
    """Map ``iq_findings`` tool_result to Findings.

    Shape: ``{"violations": [{"id"|"violation_id", "component"|"coordinates",
    "policy"|"policy_name", "severity", "summary", "waiver"?, "remediation"?}]}``
    """
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict):
        rows = (
            data.get("violations")
            or data.get("findings")
            or data.get("results")
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
        vid = row.get("id") or row.get("violation_id")
        if not vid:
            continue
        component = row.get("component") or row.get("coordinates") or "?"
        policy = row.get("policy") or row.get("policy_name") or "?"
        severity = row.get("severity") or row.get("threat_level") or "unknown"
        summary = str(row.get("summary") or row.get("description") or "")
        out.append(Finding(
            provider_key=provider_key,
            source_ref=f"iq:{vid}",
            title=f"IQ [{severity}] {policy} — {component}"[:300],
            snippet=make_snippet(summary),
            full_content=summary or None,
            url=row.get("url"),
            timestamp=row.get("opened") or row.get("detected"),
            author=None,
            raw_metadata={
                "violation_id": vid,
                "component": component,
                "policy": policy,
                "severity": severity,
                "waiver": row.get("waiver"),
                "remediation": row.get("remediation"),
                **{k: v for k, v in row.items() if k not in {
                    "id", "violation_id", "component", "coordinates",
                    "policy", "policy_name", "severity", "threat_level",
                    "summary", "description", "url", "opened", "detected",
                    "waiver", "remediation",
                }},
            },
        ))
    return out


class IQProvider:
    """Sonatype IQ findings (component-policy violations + waivers)."""

    key = "iq"
    description = (
        "Sonatype-IQ Findings für eine konfigurierte App-ID (Verstöße, "
        "Waiver, Remediation-Vorschläge). Gut für Compliance- und "
        "Vulnerability-Themen."
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
        if cancel.is_set():
            yield SearchProgress(kind="done", status_text="cancelled")
            return

        app_id = provider_settings.get("app_id")
        if not app_id:
            yield SearchProgress(
                kind="error",
                error="iq: provider_settings.app_id fehlt — IQ braucht eine App-ID",
            )
            yield SearchProgress(kind="done", status_text="config_missing")
            return

        timeout_sec = float(provider_settings.get("timeout_sec", 60))
        args: dict = {"app_id": app_id}
        if org_id := provider_settings.get("organization_id"):
            args["organization_id"] = org_id
        # The query itself doesn't map to a filter param on iq_findings —
        # it returns all violations for the app and the planner filters
        # downstream. We just include it as a hint for the directive
        # prompt (the tool ignores it).
        if query:
            args["topic_hint"] = query[:200]

        async for event in stream_agent_tool(
            _TOOL_NAME, args, cancel,
            provider_key=self.key,
            parse_tool_result=_parse,
            timeout_sec=timeout_sec,
        ):
            yield event
