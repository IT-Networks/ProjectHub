"""Tests for the six Tier-2 internal Research providers (P4).

Coverage per provider:

    * Registry shape: present, default_enabled=False, external side-effect
      (with the handbook exception — local FTS, side_effect=read)
    * Health probe: ok when ai_assist.health_check() returns True;
      degrades to ok=False with categorical detail when False/raises
    * Stream happy-path: monkeypatched fake stream → at least one
      Finding, terminal "done"
    * Cancel pre-set short-circuits

The two endpoint-direct providers (confluence, handbook) are tested
with monkeypatched ``ai_assist.research_confluence`` / ``ai_assist.post``;
the four tool-providers use a fake ``agent_stream`` that yields
``tool_result`` events the parser can decode.
"""
from __future__ import annotations

import asyncio
import os
import secrets
import tempfile

import pytest

_TEST_DB_PATH = os.path.join(
    tempfile.gettempdir(),
    f"projecthub_pytest_internal_{secrets.token_hex(4)}.db",
)
os.environ["PROJECTHUB_DB_PATH"] = _TEST_DB_PATH


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


async def _drain(agen):
    out = []
    async for event in agen:
        out.append(event)
    return out


# ── Fake agent_stream factory (mirrors test_streaming_bridge) ──────────────


def _fake_tool_stream(events: list[dict]):
    def fake_stream(*, session_id, message, model=None, project_path=None, extra=None):
        async def agen():
            for e in events:
                await asyncio.sleep(0)
                yield e
        return agen()
    return fake_stream


# ── Registry shape ─────────────────────────────────────────────────────────


def test_registry_has_sprint1_internal_providers():
    """The six sprint-1 internal providers are registered. Other
    providers may coexist (sprint-2 in test_research_providers_internal2)."""
    from services.research_providers import PROVIDERS

    sprint1 = {
        "confluence", "confluence_search", "email", "webex", "jira", "handbook",
    }
    assert sprint1.issubset(set(PROVIDERS.keys()))


def test_internal_providers_default_off_with_correct_side_effects():
    from services.research_providers import PROVIDERS

    internal_external = {"confluence", "confluence_search", "email", "webex", "jira"}
    for key in internal_external:
        p = PROVIDERS[key]
        assert p.default_enabled is False
        assert p.side_effect == "external"

    # handbook is internal but local-FTS on AI-Assist's side
    hb = PROVIDERS["handbook"]
    assert hb.default_enabled is False
    assert hb.side_effect == "read"


# ── Health probes (shared shape: ai_assist.health_check) ───────────────────


@pytest.mark.parametrize("key", [
    "confluence", "confluence_search", "email", "webex", "jira", "handbook",
])
def test_internal_provider_health_ok_when_ai_assist_up(monkeypatch, key):
    from services.research_providers import PROVIDERS
    import services.ai_assist_client as aac

    async def _yes():
        return True

    monkeypatch.setattr(aac.ai_assist, "health_check", _yes)
    health = _run(PROVIDERS[key].health())
    assert health.ok is True


@pytest.mark.parametrize("key", [
    "confluence", "confluence_search", "email", "webex", "jira", "handbook",
])
def test_internal_provider_health_degrades_when_ai_assist_down(monkeypatch, key):
    from services.research_providers import PROVIDERS
    import services.ai_assist_client as aac

    async def _no():
        return False

    monkeypatch.setattr(aac.ai_assist, "health_check", _no)
    health = _run(PROVIDERS[key].health())
    assert health.ok is False
    assert health.detail == "ai_assist_down"


def test_internal_provider_health_handles_exception(monkeypatch):
    """A health_check that raises must surface as ok=False, not crash."""
    from services.research_providers import PROVIDERS
    import services.ai_assist_client as aac

    async def _boom():
        raise RuntimeError("transport gone")

    monkeypatch.setattr(aac.ai_assist, "health_check", _boom)
    health = _run(PROVIDERS["email"].health())
    assert health.ok is False
    assert "transport gone" in health.detail or "ai_assist_unreachable" in health.detail


# ── confluence (direct endpoint) ───────────────────────────────────────────


def test_confluence_yields_findings_from_research_endpoint(monkeypatch):
    from services.research_providers import PROVIDERS
    import services.ai_assist_client as aac

    async def fake_research_confluence(topic, **kw):
        return {
            "topic": topic,
            "summary": "Synthese-Text…",
            "findings": [
                {
                    "page_id": "p1", "title": "Auth Architecture",
                    "summary": "Service X uses OAuth2 PKCE.",
                    "url": "https://confluence/x/abc",
                    "confidence": 0.91,
                },
                {
                    "page_id": "p2", "title": "Refresh policy",
                    "summary": "90-day tokens.",
                    "confidence": 0.78,
                },
            ],
            "pages_analyzed": 2,
            "pdfs_analyzed": 0,
            "errors": [],
        }

    monkeypatch.setattr(aac.ai_assist, "research_confluence", fake_research_confluence)

    events = _run(_drain(PROVIDERS["confluence"].stream(
        "OAuth2 PKCE",
        provider_settings={"space_key": "TEAM"},
        cancel=asyncio.Event(),
        project_id="proj-1",
    )))
    findings = [e for e in events if e.kind == "finding"]
    assert len(findings) == 2
    assert findings[0].finding.source_ref == "confluence:page:p1"
    assert findings[0].finding.score == 0.91
    assert events[-1].kind == "done"


def test_confluence_falls_back_to_summary_when_no_findings(monkeypatch):
    """Endpoint returns synthesis-only (no per-page findings) →
    one finding emitted with the top-level summary."""
    from services.research_providers import PROVIDERS
    import services.ai_assist_client as aac

    async def fake(topic, **kw):
        return {
            "topic": topic, "summary": "High-level overview only.",
            "findings": [], "pages_analyzed": 5, "pdfs_analyzed": 0,
        }

    monkeypatch.setattr(aac.ai_assist, "research_confluence", fake)
    events = _run(_drain(PROVIDERS["confluence"].stream(
        "auth", provider_settings={}, cancel=asyncio.Event(), project_id="p",
    )))
    findings = [e for e in events if e.kind == "finding"]
    assert len(findings) == 1
    assert "Synthese" in findings[0].finding.title
    assert events[-1].kind == "done"


def test_confluence_connection_error_yields_error_done(monkeypatch):
    from services.research_providers import PROVIDERS
    import services.ai_assist_client as aac

    async def fake(topic, **kw):
        raise ConnectionError("ai-assist offline")

    monkeypatch.setattr(aac.ai_assist, "research_confluence", fake)
    events = _run(_drain(PROVIDERS["confluence"].stream(
        "x", provider_settings={}, cancel=asyncio.Event(), project_id="p",
    )))
    errors = [e for e in events if e.kind == "error"]
    assert errors and "ai-assist offline" in errors[0].error
    assert events[-1].kind == "done"


# ── confluence_search (tool route) ─────────────────────────────────────────


def test_confluence_search_parses_tool_results(monkeypatch):
    from services.research_providers import PROVIDERS
    import services.research_providers._streaming as st

    monkeypatch.setattr(st.ai_assist, "agent_stream", _fake_tool_stream([
        {"type": "tool_result", "data": {
            "name": "search_confluence", "success": True,
            "data": {"results": [
                {"id": "page-1", "title": "Auth Doc", "summary": "PKCE flow"},
                {"id": "page-2", "title": "Token policy", "body": "90-day refresh"},
            ]},
        }},
        {"type": "done", "data": {}},
    ]))

    events = _run(_drain(PROVIDERS["confluence_search"].stream(
        "PKCE",
        provider_settings={"max_results": 5, "include_body": True},
        cancel=asyncio.Event(),
        project_id="p",
    )))
    findings = [e for e in events if e.kind == "finding"]
    assert len(findings) == 2
    assert findings[0].finding.source_ref == "search_confluence:page-1"


# ── email ──────────────────────────────────────────────────────────────────


def test_email_parses_messages_with_sender_and_filter_dsl(monkeypatch):
    from services.research_providers import PROVIDERS
    import services.research_providers._streaming as st

    captured_args = {}

    def fake_stream(*, session_id, message, model=None, project_path=None, extra=None):
        captured_args["message"] = message

        async def agen():
            yield {"type": "tool_result", "data": {
                "name": "email_find", "success": True,
                "data": [{
                    "id": "msg-1", "subject": "Auth follow-up",
                    "body": "About OAuth2 PKCE", "from": "alice@example.com",
                    "date": "2026-05-12T08:00:00Z",
                }],
            }}
            yield {"type": "done", "data": {}}
        return agen()

    monkeypatch.setattr(st.ai_assist, "agent_stream", fake_stream)

    events = _run(_drain(PROVIDERS["email"].stream(
        "PKCE",
        provider_settings={"max_results": 5, "days_back": 30, "sender": "alice"},
        cancel=asyncio.Event(),
        project_id="p",
    )))
    findings = [e for e in events if e.kind == "finding"]
    assert len(findings) == 1
    assert findings[0].finding.source_ref == "email:msg-1"
    assert findings[0].finding.author == "alice@example.com"
    # Filter DSL rendered into the agent prompt:
    assert "seit:30t" in captured_args["message"]
    assert "from:alice" in captured_args["message"]


# ── webex ──────────────────────────────────────────────────────────────────


def test_webex_uses_search_all_rooms_when_no_rooms_set(monkeypatch):
    from services.research_providers import PROVIDERS
    import services.research_providers._streaming as st

    invocations = []

    def fake_stream(*, session_id, message, model=None, project_path=None, extra=None):
        invocations.append(message)

        async def agen():
            yield {"type": "tool_result", "data": {
                "name": "webex_search_all_rooms", "success": True,
                "data": {"messages": [
                    {"id": "m1", "text": "Discussion on PKCE rollout",
                     "personEmail": "bob@x", "roomId": "r1",
                     "created": "2026-05-10T12:00Z"},
                ]},
            }}
            yield {"type": "done", "data": {}}
        return agen()

    monkeypatch.setattr(st.ai_assist, "agent_stream", fake_stream)

    events = _run(_drain(PROVIDERS["webex"].stream(
        "PKCE",
        provider_settings={"max_results": 10},  # no "rooms" → cross-room
        cancel=asyncio.Event(),
        project_id="p",
    )))
    assert len(invocations) == 1
    assert "webex_search_all_rooms" in invocations[0]
    findings = [e for e in events if e.kind == "finding"]
    assert len(findings) == 1
    assert findings[0].finding.source_ref == "webex:m1"


def test_webex_iterates_rooms_when_configured(monkeypatch):
    from services.research_providers import PROVIDERS
    import services.research_providers._streaming as st

    invocations: list[str] = []

    def fake_stream(*, session_id, message, model=None, project_path=None, extra=None):
        invocations.append(message)

        async def agen():
            yield {"type": "tool_result", "data": {
                "name": "webex_messages", "success": True,
                "data": [{"id": f"m-{len(invocations)}", "text": "hi",
                          "personEmail": "x", "roomId": f"room-{len(invocations)}"}],
            }}
            yield {"type": "done", "data": {}}
        return agen()

    monkeypatch.setattr(st.ai_assist, "agent_stream", fake_stream)

    events = _run(_drain(PROVIDERS["webex"].stream(
        "test",
        provider_settings={"rooms": ["room-a", "room-b"], "max_results": 5},
        cancel=asyncio.Event(),
        project_id="p",
    )))
    # Two rooms → two stream invocations
    assert len(invocations) == 2
    assert all("webex_messages" in inv for inv in invocations)
    findings = [e for e in events if e.kind == "finding"]
    assert len(findings) == 2
    # Single trailing terminal (not one per room).
    assert events[-1].kind == "done"


# ── jira ───────────────────────────────────────────────────────────────────


def test_jira_parses_issues_with_key_prefix(monkeypatch):
    from services.research_providers import PROVIDERS
    import services.research_providers._streaming as st

    monkeypatch.setattr(st.ai_assist, "agent_stream", _fake_tool_stream([
        {"type": "tool_result", "data": {
            "name": "find_jira", "success": True,
            "data": {"issues": [
                {"key": "PROJ-1", "summary": "Auth ticket",
                 "description": "Investigate PKCE", "status": "Open",
                 "priority": "High", "assignee": "alice"},
                {"key": "PROJ-2", "summary": "Other",
                 "description": "...", "status": "Done"},
            ]},
        }},
        {"type": "done", "data": {}},
    ]))

    events = _run(_drain(PROVIDERS["jira"].stream(
        "auth",
        provider_settings={"default_project": "PROJ", "statuses": ["open"]},
        cancel=asyncio.Event(),
        project_id="p",
    )))
    findings = [e for e in events if e.kind == "finding"]
    assert len(findings) == 2
    assert findings[0].finding.source_ref == "jira:PROJ-1"
    assert findings[0].finding.title.startswith("PROJ-1:")
    assert findings[0].finding.raw_metadata["status"] == "Open"


# ── handbook ───────────────────────────────────────────────────────────────


def test_handbook_uses_research_execute_with_handbook_source(monkeypatch):
    from services.research_providers import PROVIDERS
    import services.ai_assist_client as aac

    captured_body = {}

    async def fake_post(path, body=None):
        captured_body["path"] = path
        captured_body["body"] = body
        return {
            "results": [
                {"id": "auth-service", "title": "Auth Service",
                 "content": "Configures OAuth2 PKCE…",
                 "service": "auth", "type": "service"},
            ]
        }

    monkeypatch.setattr(aac.ai_assist, "post", fake_post)

    events = _run(_drain(PROVIDERS["handbook"].stream(
        "PKCE",
        provider_settings={"max_results": 5},
        cancel=asyncio.Event(),
        project_id="p",
    )))
    assert captured_body["path"] == "/api/research/execute"
    assert captured_body["body"]["sources"] == ["handbook"]
    findings = [e for e in events if e.kind == "finding"]
    assert len(findings) == 1
    assert findings[0].finding.source_ref == "handbook:auth-service"
    assert findings[0].finding.raw_metadata["service"] == "auth"


def test_handbook_error_yields_error_done(monkeypatch):
    from services.research_providers import PROVIDERS
    import services.ai_assist_client as aac

    async def fake_post(path, body=None):
        raise RuntimeError("backend down")

    monkeypatch.setattr(aac.ai_assist, "post", fake_post)
    events = _run(_drain(PROVIDERS["handbook"].stream(
        "x", provider_settings={}, cancel=asyncio.Event(), project_id="p",
    )))
    errors = [e for e in events if e.kind == "error"]
    assert errors and "backend down" in errors[0].error
    assert events[-1].kind == "done"


# ── Cancel pre-set ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("key", [
    "confluence", "confluence_search", "email", "webex", "jira", "handbook",
])
def test_internal_provider_cancel_pre_set_short_circuits(monkeypatch, key):
    """Cancel before stream-start → immediate done(cancelled) with no
    network call. We monkeypatch every possible network entrypoint to a
    "must not be called" sentinel so any provider that misses the
    cancel check would fail loudly."""
    from services.research_providers import PROVIDERS
    import services.ai_assist_client as aac
    import services.research_providers._streaming as st

    network_called = {"hit": False}

    async def must_not_be_called(*a, **kw):
        network_called["hit"] = True
        return {}

    def must_not_stream(*a, **kw):
        network_called["hit"] = True

        async def empty():
            if False:
                yield {}
        return empty()

    monkeypatch.setattr(aac.ai_assist, "research_confluence", must_not_be_called)
    monkeypatch.setattr(aac.ai_assist, "post", must_not_be_called)
    monkeypatch.setattr(st.ai_assist, "agent_stream", must_not_stream)

    cancel = asyncio.Event()
    cancel.set()
    events = _run(_drain(PROVIDERS[key].stream(
        "anything",
        provider_settings={},
        cancel=cancel,
        project_id="p",
    )))
    assert network_called["hit"] is False, (
        f"{key}: pre-set cancel must short-circuit before any network call"
    )
    assert events[-1].kind == "done"
    assert events[-1].status_text == "cancelled"
