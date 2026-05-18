"""AI-Assist streaming bridge for Auto-Mode providers.

Phase 3 of the Research-Auto-Mode workflow. Every Tier-2 provider that
asks AI-Assist to run a tool (``email_find``, ``find_jira``,
``search_confluence``, …) goes through ``stream_agent_tool`` so:

    1. The hardcoded write-blacklist is enforced server-side **before**
       any network round-trip — there is no path from this module to a
       blacklisted tool.
    2. ``agent_stream`` events are filtered down to the ones that
       matter (TOOL_RESULT for the requested tool) and translated into
       provider-agnostic ``SearchProgress``.
    3. Cancellation, timeout, and error handling are consistent across
       providers — they each only have to write the parser callback
       (``parse_tool_result``) and the directive prompt is built here.

Why a tool *directive* in the prompt instead of a dedicated
``/api/tools/invoke`` endpoint on AI-Assist: that endpoint doesn't
exist yet (the v2 API surface today is stream-only). The directive
prompt + tool-result filter is the smallest change that gives us a
reliable tool-call shape without touching AI-Assist. If a future
direct-invoke endpoint lands, we swap the implementation here without
changing any provider.

Security boundary: Auto-Mode is **read-only**. Tools that mutate
external systems (sending mail, triggering builds, publishing MQ
messages, creating IQ waivers) are blacklisted here AND every call to
``stream_agent_tool`` re-checks the blacklist — no caching, no
single-check-then-trust.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator, Callable, Iterable

from services.ai_assist_client import ai_assist
from services.research_providers.base import Finding, SearchProgress

logger = logging.getLogger("projecthub.research.streaming")


# ── Tool blacklist ──────────────────────────────────────────────────────────

#: Tools Auto-Mode must NEVER invoke — they all have side effects on
#: external systems. Auto-Mode is read-only by contract; if you find
#: yourself wanting to extend this list, that's the right instinct.
#: Removing an entry requires a security review.
_TOOL_BLACKLIST: frozenset[str] = frozenset({
    "iq_create_waiver",
    "jenkins_trigger_build",
    "email_send",
    "webex_send",
    "mq_publish",
})


class ToolBlacklistedError(RuntimeError):
    """Raised when a caller tries to stream a write-side tool.

    Stays an exception (rather than yielding an error event) so a coding
    mistake in a provider implementation fails loud at the call site
    instead of silently producing zero findings.
    """


def is_tool_allowed(tool_name: str) -> bool:
    """Public predicate — exposed so tests and tooling can check the same list."""
    return tool_name not in _TOOL_BLACKLIST


def blacklist_snapshot() -> frozenset[str]:
    """Return a copy of the current blacklist (for audit / UI display)."""
    return _TOOL_BLACKLIST


# ── Prompt builder ──────────────────────────────────────────────────────────

#: Directive prompt — engineered so the LLM picks the named tool and
#: returns; the wording is intentionally explicit + repetitive about
#: "no synthesis, no commentary" because GPT-style models tend to add
#: a confirmation paragraph after a tool call.
_DIRECTIVE_TEMPLATE = """Du bist ein Tool-Ausführer. Deine einzige Aufgabe:

1. Rufe genau EIN MAL das Tool `{tool_name}` mit den unten gegebenen Argumenten auf.
2. Mache KEINE weiteren Tool-Aufrufe danach.
3. Erkläre nichts, fasse nichts zusammen, kommentiere nicht.
4. Antworte nach dem Tool-Call mit dem leeren String oder "ok".

Tool: {tool_name}
Argumente:
{args_json}
"""


def _build_directive(tool_name: str, args: dict[str, Any]) -> str:
    """Render the directive prompt for one tool call."""
    return _DIRECTIVE_TEMPLATE.format(
        tool_name=tool_name,
        args_json=json.dumps(args, ensure_ascii=False, indent=2),
    )


# ── Default parser ──────────────────────────────────────────────────────────


def _default_parse_tool_result(
    *, provider_key: str, tool_name: str, payload: dict
) -> Iterable[Finding]:
    """Fallback parser when a provider doesn't pass its own.

    Treats every dict-with-a-string-id in ``payload["data"]`` as a hit;
    leaves everything else empty so a noisy ``data`` shape fails quietly
    rather than producing garbage findings. Concrete providers
    (confluence, email, …) override this with a typed parser that
    knows the exact tool-output schema.
    """
    data = payload.get("data") if isinstance(payload, dict) else None
    if not data:
        return []

    items: list[dict] = []
    if isinstance(data, list):
        items = [d for d in data if isinstance(d, dict)]
    elif isinstance(data, dict):
        # Common AI-Assist shape: {"results": [...]}
        inner = data.get("results") or data.get("items") or data.get("hits")
        if isinstance(inner, list):
            items = [d for d in inner if isinstance(d, dict)]

    findings: list[Finding] = []
    for it in items:
        ref = (
            it.get("id")
            or it.get("key")
            or it.get("ref")
            or it.get("url")
        )
        if not ref:
            continue
        findings.append(Finding(
            provider_key=provider_key,
            source_ref=f"{tool_name}:{ref}",
            title=str(it.get("title") or it.get("subject") or it.get("name") or ref)[:300],
            snippet=str(it.get("snippet") or it.get("summary") or it.get("body") or "")[:500],
            url=it.get("url"),
            timestamp=it.get("timestamp") or it.get("created_at") or it.get("updated_at"),
            author=it.get("author") or it.get("from"),
            score=None,
            raw_metadata={k: v for k, v in it.items() if k not in {
                "id", "key", "ref", "title", "subject", "name", "snippet",
                "summary", "body", "url", "timestamp", "created_at",
                "updated_at", "author", "from",
            }},
        ))
    return findings


# ── Streaming bridge ────────────────────────────────────────────────────────


# Type alias for the parser callback. Returns 0+ Findings per tool_result event.
ToolResultParser = Callable[..., Iterable[Finding]]


async def stream_agent_tool(
    tool_name: str,
    args: dict[str, Any],
    cancel: asyncio.Event,
    *,
    provider_key: str,
    parse_tool_result: ToolResultParser | None = None,
    timeout_sec: float = 60.0,
    session_prefix: str = "research",
    model: str | None = None,
    project_path: str | None = None,
) -> AsyncIterator[SearchProgress]:
    """Stream an AI-Assist tool call as ``SearchProgress`` events.

    Pre-call check: blacklist. Mid-stream: filters AI-Assist events down
    to ``tool_result`` for the named tool, runs ``parse_tool_result`` on
    each payload, and yields one ``SearchProgress(kind="finding")`` per
    extracted Finding. Terminal: exactly one ``done`` (success / cancel /
    timeout) or ``error`` event.

    Args:
        tool_name: AI-Assist tool key (e.g. ``"email_find"``).
        args: keyword arguments handed to the tool.
        cancel: when set, the stream stops yielding new findings and
            emits a terminal ``done`` within ~500 ms.
        provider_key: which Auto-Mode provider is making this call —
            tagged onto every Finding so the orchestrator can attribute.
        parse_tool_result: callback ``(*, provider_key, tool_name, payload)
            → Iterable[Finding]``. ``None`` uses
            ``_default_parse_tool_result``. Custom parsers MUST NOT
            raise — they should return ``[]`` on any malformed input.
        timeout_sec: caps total wall-clock for the tool call. On
            timeout we emit a ``done`` with ``status_text="timeout"``.
        session_prefix / model / project_path: forwarded to
            ``ai_assist.agent_stream``.

    Raises:
        ToolBlacklistedError: when ``tool_name`` is on the blacklist.
            This is intentional — a programming bug, not a runtime
            condition the provider should "handle".
    """
    if not is_tool_allowed(tool_name):
        # Loud failure: a provider tried to call a write-side tool.
        raise ToolBlacklistedError(
            f"Tool {tool_name!r} is blacklisted from Auto-Mode (write side-effect)"
        )

    parser: ToolResultParser = parse_tool_result or _default_parse_tool_result

    if cancel.is_set():
        yield SearchProgress(kind="done", status_text="cancelled")
        return

    session_id = f"{session_prefix}-{tool_name}-{id(cancel):x}"
    message = _build_directive(tool_name, args)
    yield SearchProgress(
        kind="status", status_text=f"Tool-Call: {tool_name}"
    )

    try:
        async for event in _iter_with_timeout(
            ai_assist.agent_stream(
                session_id=session_id,
                message=message,
                model=model,
                project_path=project_path,
            ),
            timeout_sec=timeout_sec,
            cancel=cancel,
        ):
            etype = event.get("type")
            data = event.get("data")

            if etype == "tool_result":
                payload = data if isinstance(data, dict) else {}
                if payload.get("name") != tool_name:
                    # Agent picked a different tool — ignore. The directive
                    # is strong; this branch fires rarely but is the right
                    # defensive default.
                    continue
                if not payload.get("success", False):
                    err = payload.get("error") or "tool reported failure"
                    yield SearchProgress(
                        kind="error", error=f"{tool_name}: {err}"[:300]
                    )
                    continue
                # Parser must never raise — but defend anyway.
                try:
                    for f in parser(
                        provider_key=provider_key,
                        tool_name=tool_name,
                        payload=payload,
                    ):
                        if cancel.is_set():
                            break
                        yield SearchProgress(kind="finding", finding=f)
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "tool_result parser for %s raised: %s", tool_name, e
                    )
                    yield SearchProgress(
                        kind="error",
                        error=f"{tool_name} parser failed: {e!s}"[:300],
                    )
                continue

            if etype in ("done", "cancelled", "max_iterations"):
                # Terminal — the inner loop emitted the relevant findings
                # via tool_result already; we just close the SearchProgress
                # stream.
                yield SearchProgress(
                    kind="done", status_text=str(etype)
                )
                return

            if etype == "error":
                err = (data or {}).get("error") if isinstance(data, dict) else str(data)
                yield SearchProgress(
                    kind="error", error=f"agent_stream: {err}"[:300]
                )
                yield SearchProgress(kind="done", status_text="error")
                return

            # token / reasoning_token / usage / tool_start / agent_progress /
            # stuck_detected / confirm_required → ignored for tool-only flows
            continue

        # ``_iter_with_timeout`` exited without a terminal event.
        # That can only happen on timeout or external cancel — both
        # already emit their own ``done`` below.
        yield SearchProgress(kind="done", status_text="ended_without_terminal")
    except asyncio.TimeoutError:
        yield SearchProgress(
            kind="error", error=f"{tool_name}: timeout after {timeout_sec}s"
        )
        yield SearchProgress(kind="done", status_text="timeout")
    except Exception as e:  # noqa: BLE001
        logger.exception("stream_agent_tool unexpected failure for %s", tool_name)
        yield SearchProgress(kind="error", error=f"bridge error: {e!s}"[:300])
        yield SearchProgress(kind="done", status_text="error")


# ── Internals ──────────────────────────────────────────────────────────────


async def _iter_with_timeout(
    source: AsyncIterator[dict],
    *,
    timeout_sec: float,
    cancel: asyncio.Event,
) -> AsyncIterator[dict]:
    """Wrap an async iterator with a wall-clock timeout and cancel-poll.

    Each ``__anext__`` is bounded by ``timeout_sec`` minus elapsed; we
    poll ``cancel`` on every step. On cancel, we exit cleanly so the
    caller's ``finally`` (e.g. ``done``-event emission) still runs.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_sec
    iterator = source.__aiter__()
    while True:
        if cancel.is_set():
            return
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise asyncio.TimeoutError
        try:
            event = await asyncio.wait_for(iterator.__anext__(), timeout=remaining)
        except StopAsyncIteration:
            return
        yield event
