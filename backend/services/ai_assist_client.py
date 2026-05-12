"""HTTP client for AI-Assist (Engine v2).

Migration note (2026-05-12): AI-Assist deleted its v1 routes after the
Engine-v2 cutover (`engine_v2.enabled=true`, V1 router removed in
`app/api/v2_wiring.py`). The legacy `/api/chat`, `/api/chat/stream`,
`/api/chat/{sid}/history` endpoints no longer exist; everything goes
through `/api/v2/agent/stream` with typed SSE events.

This module exposes a small surface tailored to ProjectHub's needs:

    agent_call()           — one-shot LLM call (collects TOKENs to a string)
    agent_stream()         — yields {type, data} dicts from the v2 SSE
    get_session_history()  — GET /api/v2/agent/{sid}/history
    health_check()         — GET /api/health
    plus thin GET/POST helpers for the remaining REST endpoints
    (jenkins, github, webex, email) that ProjectHub still consumes.

Removed in this revision (endpoints never existed in AI-Assist):
    get_jira_credentials   — Jira creds now come from PROJECTHUB_JIRA_*
                             env-vars only (see services/jira_client.py)
    get_build_details      — was always best-effort; adapter now omits
                             the enrichment call
    list_pull_requests     — adapter raises a clear error pointing at the
                             missing AI-Assist endpoint
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, AsyncIterator

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import async_session
from models.cache import OfflineCache

logger = logging.getLogger("projecthub.ai_assist")


# Single source of truth for the v2 endpoint paths.
V2_AGENT_STREAM = "/api/v2/agent/stream"
V2_AGENT_HISTORY = "/api/v2/agent/{session_id}/history"


class AiAssistClient:
    """HTTP client for proxying requests to AI-Assist backend."""

    def __init__(self):
        self._client: httpx.AsyncClient | None = None
        self.is_connected = False
        self.base_url = settings.ai_assist_url

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(
                    connect=5.0,
                    read=float(settings.ai_assist_timeout),
                    write=10.0,
                    pool=5.0,
                ),
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=5),
            )
        return self._client

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    # ── Cache helpers ───────────────────────────────────────────────────────

    async def _save_cache(self, cache_key: str, cache_type: str, data: dict | list):
        try:
            async with async_session() as db:
                result = await db.execute(
                    select(OfflineCache).where(OfflineCache.cache_key == cache_key)
                )
                entry = result.scalar_one_or_none()
                if entry:
                    entry.data = json.dumps(data)
                    entry.fetched_at = datetime.now(timezone.utc).isoformat()
                else:
                    entry = OfflineCache(
                        cache_key=cache_key,
                        cache_type=cache_type,
                        data=json.dumps(data),
                    )
                    db.add(entry)
                await db.commit()
        except Exception as e:
            logger.debug("Cache save failed: %s", e)

    async def _load_cache(self, cache_key: str) -> dict | list | None:
        try:
            async with async_session() as db:
                result = await db.execute(
                    select(OfflineCache).where(OfflineCache.cache_key == cache_key)
                )
                entry = result.scalar_one_or_none()
                if entry:
                    return json.loads(entry.data)
        except Exception as e:
            logger.debug("Cache load failed: %s", e)
        return None

    # ── Core HTTP methods ───────────────────────────────────────────────────

    async def get(
        self,
        path: str,
        params: dict | None = None,
        cache_key: str | None = None,
        cache_type: str = "generic",
    ) -> dict | list | None:
        client = await self._ensure_client()
        try:
            resp = await client.get(path, params=params)
            resp.raise_for_status()
            data = resp.json()
            self.is_connected = True
            if cache_key:
                await self._save_cache(cache_key, cache_type, data)
            return data
        except (httpx.ConnectError, httpx.TimeoutException, httpx.ConnectTimeout) as e:
            self.is_connected = False
            logger.warning("AI-Assist nicht erreichbar: %s", e)
            if cache_key:
                cached = await self._load_cache(cache_key)
                if cached is not None:
                    logger.info("Verwende Cache für %s", cache_key)
                    return cached
            return None
        except httpx.HTTPStatusError as e:
            logger.error("AI-Assist HTTP %s: %s", e.response.status_code, path)
            return None

    async def post(self, path: str, body: dict | None = None) -> dict | None:
        client = await self._ensure_client()
        try:
            resp = await client.post(path, json=body)
            resp.raise_for_status()
            self.is_connected = True
            return resp.json()
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            self.is_connected = False
            logger.warning("AI-Assist nicht erreichbar: %s", e)
            return None
        except httpx.HTTPStatusError as e:
            logger.error("AI-Assist HTTP %s: %s", e.response.status_code, path)
            return None

    async def patch(self, path: str, body: dict | None = None) -> dict | None:
        client = await self._ensure_client()
        try:
            resp = await client.patch(path, json=body)
            resp.raise_for_status()
            self.is_connected = True
            return resp.json()
        except (httpx.ConnectError, httpx.TimeoutException):
            self.is_connected = False
            return None

    # ── Engine v2 agent ─────────────────────────────────────────────────────

    async def agent_stream(
        self,
        *,
        session_id: str,
        message: str,
        model: str | None = None,
        project_path: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream the v2 agent. Yields one dict per SSE event.

        Yield shape::

            {"type": "token", "data": "<fragment string>"}
            {"type": "usage", "data": {...}}
            {"type": "tool_start" | "tool_result", "data": {...}}
            {"type": "done" | "cancelled" | "error" | "max_iterations", "data": {...}}

        Guarantees exactly ONE terminal event (done|cancelled|error|
        max_iterations) per call — on transport failure a synthetic
        ``error`` + ``done`` pair is emitted so consumers can always
        finalise their state machine.
        """
        body: dict[str, Any] = {
            "session_id": session_id,
            "message": message,
            "auto_detect": True,
        }
        if model:
            body["model"] = model
        if project_path:
            body["project_path"] = project_path
        if extra:
            body.update(extra)

        # Separate client with longer read timeout for streaming.
        stream_client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(
                connect=5.0,
                read=float(settings.ai_assist_llm_timeout),
                write=10.0,
                pool=5.0,
            ),
        )
        try:
            try:
                async with stream_client.stream(
                    "POST", V2_AGENT_STREAM, json=body
                ) as resp:
                    if resp.status_code != 200:
                        err_body = ""
                        try:
                            err_body = (await resp.aread()).decode(
                                "utf-8", errors="replace"
                            )
                        except Exception:
                            pass
                        self.is_connected = True  # server is up, just rejected
                        logger.warning(
                            "AI-Assist v2-Agent HTTP %s: %s",
                            resp.status_code, err_body[:200],
                        )
                        yield _format_http_error(resp.status_code, err_body)
                        yield {"type": "done", "data": {"reason": "http_error"}}
                        return

                    self.is_connected = True
                    emitted_terminal = False
                    current_type: str | None = None
                    data_lines: list[str] = []

                    async for raw_line in resp.aiter_lines():
                        line = raw_line.rstrip("\r")
                        if line == "":
                            if current_type is not None:
                                event = _build_event(current_type, data_lines)
                                if event["type"] in _TERMINAL_EVENTS:
                                    emitted_terminal = True
                                yield event
                            current_type = None
                            data_lines = []
                            continue
                        if line.startswith(":"):
                            continue  # SSE comment
                        if line.startswith("event:"):
                            current_type = line[6:].strip()
                        elif line.startswith("data:"):
                            # Strip exactly one leading space per SSE spec.
                            payload = line[5:]
                            if payload.startswith(" "):
                                payload = payload[1:]
                            data_lines.append(payload)

                    # Trailing event without a final blank line.
                    if current_type is not None:
                        event = _build_event(current_type, data_lines)
                        if event["type"] in _TERMINAL_EVENTS:
                            emitted_terminal = True
                        yield event

                    # Server closed the stream without sending a terminal —
                    # synthesize one so consumers don't hang waiting.
                    if not emitted_terminal:
                        yield {"type": "done", "data": {"reason": "stream_ended"}}
                    return
            except (httpx.ConnectError, httpx.TimeoutException) as e:
                self.is_connected = False
                logger.warning("AI-Assist v2-Stream nicht erreichbar: %s", e)
                yield {
                    "type": "error",
                    "data": {"error": "AI-Assist nicht erreichbar", "detail": str(e)},
                }
                yield {"type": "done", "data": {"reason": "transport_error"}}
                return
            except httpx.HTTPError as e:
                self.is_connected = False
                logger.warning("AI-Assist v2-Stream HTTP-Fehler: %s", e)
                yield {
                    "type": "error",
                    "data": {"error": "AI-Assist Stream-Fehler", "detail": str(e)},
                }
                yield {"type": "done", "data": {"reason": "stream_error"}}
                return
        finally:
            # ``finally`` is the only safe spot to clean up the client —
            # it runs on early consumer exit (GeneratorExit) too. Do NOT
            # yield from here; yield during GeneratorExit raises
            # ``RuntimeError: async generator ignored GeneratorExit``.
            await stream_client.aclose()

    async def agent_call(
        self,
        *,
        session_id: str,
        message: str,
        model: str | None = None,
        project_path: str | None = None,
        auto_detect: bool = True,
    ) -> dict[str, Any] | None:
        """One-shot v2 call. Accumulates TOKEN events into ``response``.

        Returns ``{response, model, usage, error}`` on any terminal event
        from the engine (done/cancelled/max_iterations) — callers branch
        on ``response`` for content and on ``error`` for failure state.

        Returns ``None`` ONLY when AI-Assist could not be reached at all
        (transport-level failure with no usable output). This lets
        callers distinguish "couldn't even reach the server" (503-style)
        from "server responded but yielded nothing useful" (502-style)
        from "server responded with partial output + error".
        """
        response_parts: list[str] = []
        used_model: str = ""
        usage: dict[str, Any] = {}
        saw_terminal = False
        transport_failed = False
        error_msg: str | None = None

        async for event in self.agent_stream(
            session_id=session_id,
            message=message,
            model=model,
            project_path=project_path,
            extra={"auto_detect": auto_detect},
        ):
            etype = event.get("type")
            data = event.get("data")
            if etype == "token":
                if isinstance(data, str):
                    response_parts.append(data)
            elif etype == "usage" and isinstance(data, dict):
                usage = data
                model_in_usage = data.get("model")
                if isinstance(model_in_usage, str) and model_in_usage:
                    used_model = model_in_usage
            elif etype == "done":
                saw_terminal = True
                if isinstance(data, dict):
                    reason = data.get("reason")
                    if reason in {"transport_error", "stream_error"}:
                        transport_failed = True
            elif etype in _TERMINAL_EVENTS:
                # error / cancelled / max_iterations / confirm_required
                saw_terminal = True
                if isinstance(data, dict):
                    error_msg = (
                        data.get("error")
                        or data.get("message")
                        or data.get("reason")
                        or etype
                    )
                else:
                    error_msg = str(data) if data else etype

        response_text = "".join(response_parts)
        if not saw_terminal:
            return None  # Generator ended without any terminal — true outage
        if transport_failed and not response_text:
            return None  # Synthetic transport_error done — couldn't talk to AI-Assist

        return {
            "response": response_text,
            "model": used_model,
            "usage": usage,
            "error": error_msg,
        }

    async def get_session_history(self, session_id: str) -> dict | None:
        """GET /api/v2/agent/{session_id}/history.

        Shape::

            {"session_id": "...", "messages": [...], "message_count": N,
             "project_path": "..."}
        """
        return await self.get(V2_AGENT_HISTORY.format(session_id=session_id))

    # ── Health ──────────────────────────────────────────────────────────────

    async def health_check(self) -> bool:
        client = await self._ensure_client()
        try:
            resp = await client.get("/api/health", timeout=5.0)
            self.is_connected = resp.status_code == 200
        except Exception:
            self.is_connected = False
        return self.is_connected

    # ── Convenience GETs (REST endpoints that still exist in v2) ────────────

    async def get_jenkins_jobs(self, path_name: str | None = None) -> dict | None:
        params = {"path_name": path_name} if path_name else None
        return await self.get(
            "/api/jenkins/jobs",
            params=params,
            cache_key=f"jenkins:jobs:{path_name or 'default'}",
            cache_type="jenkins_status",
        )

    async def get_github_prs(self, owner: str, repo: str) -> list | None:
        # AI-Assist exposes /api/github/repos with open_issues_count per
        # repo, but no per-repo PR-list endpoint — callers that need the
        # full PR list must work from the repo summary or wait for a
        # future /api/github/pulls implementation.
        data = await self.get(
            "/api/github/repos",
            cache_key=f"github:repos:{owner}",
            cache_type="github_repos",
        )
        return data

    async def get_pr_details(self, owner: str, repo: str, pr_number: int) -> dict | None:
        return await self.get(
            f"/api/github/pr/{owner}/{repo}/{pr_number}",
            cache_key=f"github:pr:{owner}/{repo}/{pr_number}",
            cache_type="github_pr",
        )

    async def get_pr_diff(self, owner: str, repo: str, pr_number: int) -> dict | None:
        return await self.get(f"/api/github/pr/{owner}/{repo}/{pr_number}/diff")

    async def analyze_pr(self, owner: str, repo: str, pr_number: int) -> dict | None:
        return await self.post(f"/api/github/pr/{owner}/{repo}/{pr_number}/analyze", {})

    async def search_emails(
        self, query: str = "", folder: str = "inbox", limit: int = 20
    ) -> dict | None:
        return await self.post("/api/email/search", {
            "query": query, "folder": folder, "limit": limit,
        })

    async def get_webex_rooms(self) -> dict | None:
        return await self.get(
            "/api/webex/rooms",
            cache_key="webex:rooms",
            cache_type="webex_rooms",
        )

    async def get_webex_messages(self, room_id: str, limit: int = 50) -> dict | None:
        return await self.get(
            f"/api/webex/rooms/{room_id}/messages",
            params={"limit": limit},
        )

    async def get_email_todos(self, status: str = "new") -> dict | None:
        return await self.get("/api/email/todos", params={"status": status})


# ── SSE-parser helpers ──────────────────────────────────────────────────────

# Terminal event types emitted by the v2 engine. Mirrors
# ``AgentEventType`` in AI-Assist's ``app/agent/v2/events.py``.
_TERMINAL_EVENTS = frozenset({
    "done", "cancelled", "error", "max_iterations", "confirm_required",
})


def _build_event(event_type: str, data_lines: list[str]) -> dict[str, Any]:
    """Construct a ``{type, data}`` event from buffered SSE lines."""
    if not data_lines:
        return {"type": event_type, "data": None}
    raw = "\n".join(data_lines)
    try:
        data: Any = json.loads(raw)
    except json.JSONDecodeError:
        data = raw
    return {"type": event_type, "data": data}


def _format_http_error(status: int, body: str) -> dict[str, Any]:
    """Build an ``error`` event for non-2xx responses from /api/v2/agent/stream.

    Tries to extract structured detail from the JSON body when present —
    e.g. 409 ``pending_confirmation`` carries the pending tool name and
    args; surfacing them helps the caller (or operator) decide whether
    to POST ``/confirm`` or reset the session.
    """
    detail: Any = body[:500]
    parsed: dict[str, Any] | None = None
    try:
        parsed = json.loads(body) if body else None
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        # FastAPI HTTPException-shape: {"detail": <str|dict>}
        inner = parsed.get("detail", parsed)
        if isinstance(inner, dict):
            detail = inner
        elif isinstance(inner, str):
            detail = inner
    payload: dict[str, Any] = {
        "error": f"AI-Assist HTTP {status}",
        "status": status,
        "detail": detail,
    }
    # 409 from the v2 endpoint means either a pending_confirmation on the
    # session or a concurrent /stream claim — neither is fatal, but both
    # need operator action. Tag them so consumers can render a hint.
    if status == 409 and isinstance(detail, dict):
        err_code = detail.get("error")
        if err_code in {"pending_confirmation", "concurrent_stream"}:
            payload["code"] = err_code
    return {"type": "error", "data": payload}


# Singleton
ai_assist = AiAssistClient()
