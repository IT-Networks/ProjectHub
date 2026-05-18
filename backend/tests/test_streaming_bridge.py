"""Tests for the AI-Assist streaming bridge (Phase 3).

Covers:
    * Tool blacklist — every write-side tool raises ToolBlacklistedError
      *before* any network call (no leaks through cancel/timeout/parser).
    * Default parser — extracts findings from the canonical AI-Assist
      ``tool_result`` shape (data list + data.results list).
    * Provider-injected parser — runs on the payload, output flows
      through as Findings.
    * Cancel — set during stream → stops yielding findings, emits done.
    * Timeout — fake agent stalls → bridge emits error + done.
    * Failure paths — tool_result with success=False, agent error event,
      wrong tool_name (ignored, not yielded).

The bridge talks to ``ai_assist.agent_stream`` — tests monkeypatch that
to return a deterministic async generator, so no real LiteLLM round-trip
happens.
"""
from __future__ import annotations

import asyncio
import os
import secrets
import tempfile

import pytest

# Conftest pins PROJECTHUB_DB_PATH before backend imports. The bridge
# doesn't touch the DB, but ai_assist_client construction reads settings.
_TEST_DB_PATH = os.path.join(
    tempfile.gettempdir(),
    f"projecthub_pytest_streaming_{secrets.token_hex(4)}.db",
)
os.environ["PROJECTHUB_DB_PATH"] = _TEST_DB_PATH


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


async def _drain(agen):
    out = []
    async for event in agen:
        out.append(event)
    return out


# ── Fakes ──────────────────────────────────────────────────────────────────


def _make_fake_stream(events: list[dict]):
    """Return an async-generator factory that yields ``events`` in order.

    Drop-in replacement for ``ai_assist.agent_stream`` — matching
    signature, captures call args on the holder for assertion.
    """
    holder = {"calls": []}

    def fake_stream(
        *,
        session_id: str,
        message: str,
        model: str | None = None,
        project_path: str | None = None,
        extra: dict | None = None,
    ):
        holder["calls"].append({
            "session_id": session_id,
            "message": message,
            "model": model,
            "project_path": project_path,
            "extra": extra,
        })

        async def agen():
            for e in events:
                # Yield-and-cooperate so cancel can fire between events.
                await asyncio.sleep(0)
                yield e

        return agen()

    return fake_stream, holder


def _make_stall_stream(stall_seconds: float = 5.0):
    """Async generator that yields nothing for ``stall_seconds`` then quits."""

    def fake_stream(**_):
        async def agen():
            await asyncio.sleep(stall_seconds)
            yield {"type": "done", "data": {}}

        return agen()

    return fake_stream


# ── Blacklist ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("blocked_tool", [
    "iq_create_waiver",
    "jenkins_trigger_build",
    "email_send",
    "webex_send",
    "mq_publish",
])
def test_blacklist_raises_before_any_network_call(monkeypatch, blocked_tool):
    """Every blacklisted tool raises ToolBlacklistedError — and the
    raise must happen BEFORE the fake ai_assist.agent_stream is touched."""
    from services.research_providers import _streaming

    fake_called = {"hit": False}

    def fake_stream(**_):
        fake_called["hit"] = True

        async def empty():
            if False:
                yield {}
        return empty()

    monkeypatch.setattr(
        _streaming.ai_assist, "agent_stream", fake_stream
    )

    cancel = asyncio.Event()
    with pytest.raises(_streaming.ToolBlacklistedError):
        # Need to actually iterate the generator — bridge does the
        # blacklist check on first __anext__.
        _run(_drain(_streaming.stream_agent_tool(
            blocked_tool, {"x": 1}, cancel, provider_key="test",
        )))
    assert fake_called["hit"] is False, (
        "blacklist check ran AFTER the fake stream — security boundary leaked"
    )


def test_is_tool_allowed_predicate():
    from services.research_providers._streaming import is_tool_allowed

    assert is_tool_allowed("email_send") is False
    assert is_tool_allowed("email_find") is True  # read-side counterpart
    assert is_tool_allowed("some_new_tool") is True  # default allow


def test_blacklist_snapshot_is_immutable():
    from services.research_providers._streaming import blacklist_snapshot

    snap = blacklist_snapshot()
    assert "email_send" in snap
    # frozenset has no .add — accessing it via direct mutation must fail.
    with pytest.raises(AttributeError):
        snap.add("new_tool")  # type: ignore[attr-defined]


# ── Default parser ─────────────────────────────────────────────────────────


def test_default_parser_extracts_findings_from_data_list(monkeypatch):
    from services.research_providers import _streaming

    payload = {
        "name": "email_find",
        "success": True,
        "data": [
            {
                "id": "msg-1",
                "subject": "Auth follow-up",
                "body": "About OAuth2 PKCE rollout",
                "from": "alice",
            },
            {
                "id": "msg-2",
                "subject": "PKCE policy",
                "body": "90-day refresh tokens",
            },
        ],
    }

    fake_stream, _ = _make_fake_stream([
        {"type": "tool_result", "data": payload},
        {"type": "done", "data": {}},
    ])
    monkeypatch.setattr(_streaming.ai_assist, "agent_stream", fake_stream)

    cancel = asyncio.Event()
    events = _run(_drain(_streaming.stream_agent_tool(
        "email_find", {"text": "PKCE"}, cancel, provider_key="email",
    )))
    findings = [e for e in events if e.kind == "finding"]
    assert len(findings) == 2
    assert findings[0].finding.source_ref == "email_find:msg-1"
    assert findings[0].finding.title == "Auth follow-up"
    assert "OAuth2" in findings[0].finding.snippet
    assert findings[0].finding.author == "alice"
    assert events[-1].kind == "done"


def test_default_parser_extracts_findings_from_results_wrapper(monkeypatch):
    """Common alternative shape: ``{"results": [...]}`` instead of bare list."""
    from services.research_providers import _streaming

    fake_stream, _ = _make_fake_stream([
        {
            "type": "tool_result",
            "data": {
                "name": "search_confluence",
                "success": True,
                "data": {"results": [
                    {"id": "page-1", "title": "Auth Architecture", "summary": "OAuth2 PKCE flow"},
                ]},
            },
        },
        {"type": "done", "data": {}},
    ])
    monkeypatch.setattr(_streaming.ai_assist, "agent_stream", fake_stream)

    events = _run(_drain(_streaming.stream_agent_tool(
        "search_confluence", {"query": "PKCE"}, asyncio.Event(),
        provider_key="confluence",
    )))
    findings = [e for e in events if e.kind == "finding"]
    assert len(findings) == 1
    assert findings[0].finding.source_ref == "search_confluence:page-1"
    assert findings[0].finding.title == "Auth Architecture"


def test_default_parser_skips_items_without_id(monkeypatch):
    from services.research_providers import _streaming

    fake_stream, _ = _make_fake_stream([
        {
            "type": "tool_result",
            "data": {
                "name": "find_jira",
                "success": True,
                "data": [
                    {"title": "no id here"},  # dropped
                    {"id": "AUTH-1", "title": "Ticket with id"},
                ],
            },
        },
        {"type": "done", "data": {}},
    ])
    monkeypatch.setattr(_streaming.ai_assist, "agent_stream", fake_stream)

    events = _run(_drain(_streaming.stream_agent_tool(
        "find_jira", {}, asyncio.Event(), provider_key="jira",
    )))
    findings = [e for e in events if e.kind == "finding"]
    assert len(findings) == 1
    assert findings[0].finding.source_ref == "find_jira:AUTH-1"


# ── Custom parser ──────────────────────────────────────────────────────────


def test_custom_parser_is_called_with_payload(monkeypatch):
    from services.research_providers import _streaming
    from services.research_providers.base import Finding

    captured: dict = {}

    def custom_parse(*, provider_key, tool_name, payload):
        captured["provider_key"] = provider_key
        captured["tool_name"] = tool_name
        captured["payload_name"] = payload.get("name")
        return [Finding(
            provider_key=provider_key,
            source_ref=f"custom:{tool_name}",
            title="From custom parser",
            snippet="hello",
        )]

    fake_stream, _ = _make_fake_stream([
        {"type": "tool_result", "data": {
            "name": "find_jira", "success": True, "data": {"anything": "goes"},
        }},
        {"type": "done", "data": {}},
    ])
    monkeypatch.setattr(_streaming.ai_assist, "agent_stream", fake_stream)

    events = _run(_drain(_streaming.stream_agent_tool(
        "find_jira", {}, asyncio.Event(),
        provider_key="jira",
        parse_tool_result=custom_parse,
    )))
    assert captured == {
        "provider_key": "jira", "tool_name": "find_jira", "payload_name": "find_jira",
    }
    findings = [e for e in events if e.kind == "finding"]
    assert len(findings) == 1
    assert findings[0].finding.title == "From custom parser"


def test_custom_parser_exception_yields_error_then_continues(monkeypatch):
    """A parser that raises must not crash the bridge — it must emit
    an error event and let subsequent tool_results still be processed."""
    from services.research_providers import _streaming

    def boom_parser(**_):
        raise ValueError("intentional test failure")

    fake_stream, _ = _make_fake_stream([
        {"type": "tool_result", "data": {
            "name": "find_jira", "success": True, "data": [{"id": "X"}],
        }},
        {"type": "done", "data": {}},
    ])
    monkeypatch.setattr(_streaming.ai_assist, "agent_stream", fake_stream)

    events = _run(_drain(_streaming.stream_agent_tool(
        "find_jira", {}, asyncio.Event(),
        provider_key="jira",
        parse_tool_result=boom_parser,
    )))
    errors = [e for e in events if e.kind == "error"]
    assert len(errors) == 1
    assert "parser failed" in errors[0].error
    assert events[-1].kind == "done"


# ── Filtering: wrong tool name ─────────────────────────────────────────────


def test_tool_result_for_different_tool_is_ignored(monkeypatch):
    from services.research_providers import _streaming

    fake_stream, _ = _make_fake_stream([
        # Agent called the wrong tool (shouldn't happen w/ directive, but defend)
        {"type": "tool_result", "data": {
            "name": "OTHER_TOOL", "success": True, "data": [{"id": "X"}],
        }},
        # Then the right one
        {"type": "tool_result", "data": {
            "name": "find_jira", "success": True, "data": [{"id": "AUTH-1", "title": "ok"}],
        }},
        {"type": "done", "data": {}},
    ])
    monkeypatch.setattr(_streaming.ai_assist, "agent_stream", fake_stream)

    events = _run(_drain(_streaming.stream_agent_tool(
        "find_jira", {}, asyncio.Event(), provider_key="jira",
    )))
    findings = [e for e in events if e.kind == "finding"]
    assert len(findings) == 1
    assert findings[0].finding.source_ref == "find_jira:AUTH-1"


# ── Tool failure ───────────────────────────────────────────────────────────


def test_tool_result_with_success_false_yields_error(monkeypatch):
    from services.research_providers import _streaming

    fake_stream, _ = _make_fake_stream([
        {"type": "tool_result", "data": {
            "name": "find_jira", "success": False, "error": "auth expired",
        }},
        {"type": "done", "data": {}},
    ])
    monkeypatch.setattr(_streaming.ai_assist, "agent_stream", fake_stream)

    events = _run(_drain(_streaming.stream_agent_tool(
        "find_jira", {}, asyncio.Event(), provider_key="jira",
    )))
    errors = [e for e in events if e.kind == "error"]
    assert len(errors) == 1
    assert "auth expired" in errors[0].error
    assert events[-1].kind == "done"


# ── Cancel ─────────────────────────────────────────────────────────────────


def test_cancel_set_before_call_short_circuits(monkeypatch):
    from services.research_providers import _streaming

    fake_called = {"hit": False}

    def fake_stream(**_):
        fake_called["hit"] = True

        async def agen():
            yield {"type": "tool_result", "data": {
                "name": "find_jira", "success": True, "data": [{"id": "X"}]
            }}
        return agen()

    monkeypatch.setattr(_streaming.ai_assist, "agent_stream", fake_stream)
    cancel = asyncio.Event()
    cancel.set()

    events = _run(_drain(_streaming.stream_agent_tool(
        "find_jira", {}, cancel, provider_key="jira",
    )))
    assert fake_called["hit"] is False, "must not invoke agent_stream when pre-cancelled"
    assert events[-1].kind == "done"
    assert events[-1].status_text == "cancelled"


def test_cancel_mid_stream_stops_findings(monkeypatch):
    from services.research_providers import _streaming

    cancel = asyncio.Event()

    async def cancelling_stream(**_):
        # Send a finding first, then trip cancel before the next.
        yield {"type": "tool_result", "data": {
            "name": "find_jira", "success": True,
            "data": [{"id": "A"}, {"id": "B"}, {"id": "C"}],
        }}
        cancel.set()
        yield {"type": "tool_result", "data": {
            "name": "find_jira", "success": True,
            "data": [{"id": "D"}],
        }}
        yield {"type": "done", "data": {}}

    monkeypatch.setattr(_streaming.ai_assist, "agent_stream", cancelling_stream)

    events = _run(_drain(_streaming.stream_agent_tool(
        "find_jira", {}, cancel, provider_key="jira",
    )))
    findings = [e for e in events if e.kind == "finding"]
    refs = [f.finding.source_ref for f in findings]
    # First batch was processed; second batch must not appear (cancel fires
    # before we'd start yielding it).
    assert "find_jira:D" not in refs
    assert events[-1].kind == "done"


# ── Timeout ────────────────────────────────────────────────────────────────


def test_timeout_yields_error_and_done(monkeypatch):
    from services.research_providers import _streaming

    monkeypatch.setattr(
        _streaming.ai_assist, "agent_stream", _make_stall_stream(stall_seconds=2.0)
    )

    events = _run(_drain(_streaming.stream_agent_tool(
        "find_jira", {}, asyncio.Event(),
        provider_key="jira",
        timeout_sec=0.3,
    )))
    errors = [e for e in events if e.kind == "error"]
    assert errors and "timeout" in errors[0].error
    assert events[-1].kind == "done"
    assert events[-1].status_text == "timeout"


# ── Agent error event ──────────────────────────────────────────────────────


def test_agent_error_event_propagates(monkeypatch):
    from services.research_providers import _streaming

    fake_stream, _ = _make_fake_stream([
        {"type": "error", "data": {"error": "litellm unreachable"}},
    ])
    monkeypatch.setattr(_streaming.ai_assist, "agent_stream", fake_stream)

    events = _run(_drain(_streaming.stream_agent_tool(
        "find_jira", {}, asyncio.Event(), provider_key="jira",
    )))
    errors = [e for e in events if e.kind == "error"]
    assert errors and "litellm unreachable" in errors[0].error
    assert events[-1].kind == "done"
    assert events[-1].status_text == "error"


# ── Args propagation ───────────────────────────────────────────────────────


def test_args_appear_in_directive_message(monkeypatch):
    """The args dict is rendered into the prompt as JSON — the LLM
    can't call the tool with them otherwise."""
    from services.research_providers import _streaming

    fake_stream, holder = _make_fake_stream([
        {"type": "done", "data": {}},
    ])
    monkeypatch.setattr(_streaming.ai_assist, "agent_stream", fake_stream)

    _run(_drain(_streaming.stream_agent_tool(
        "find_jira", {"text": "auth", "filter": "status:open"}, asyncio.Event(),
        provider_key="jira",
    )))
    assert len(holder["calls"]) == 1
    msg = holder["calls"][0]["message"]
    assert "find_jira" in msg
    assert "auth" in msg
    assert "status:open" in msg
