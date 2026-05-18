"""Tests for the six Tier-2 internal providers — sprint #2 (P5).

log_servers / code_graph / iq / github / jenkins / mq.

Pattern mirrors test_research_providers_internal: each provider gets
registry + health (ok / down) + a happy-path with monkeypatched agent
stream + a cancel-pre-set check. Plus the provider-specific guards:

    * jenkins: assert jenkins_trigger_build stays on the blacklist
      (defence-in-depth check — separate from the global blacklist
      test in test_streaming_bridge)
    * iq: missing app_id surfaces as a clean error event, not a crash
    * mq: trigger-role queues are filtered out (read-only Auto-Mode)
"""
from __future__ import annotations

import asyncio
import os
import secrets
import tempfile

import pytest

_TEST_DB_PATH = os.path.join(
    tempfile.gettempdir(),
    f"projecthub_pytest_internal2_{secrets.token_hex(4)}.db",
)
os.environ["PROJECTHUB_DB_PATH"] = _TEST_DB_PATH


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


async def _drain(agen):
    out = []
    async for event in agen:
        out.append(event)
    return out


def _fake_tool_stream(events: list[dict]):
    def fake_stream(*, session_id, message, model=None, project_path=None, extra=None):
        async def agen():
            for e in events:
                await asyncio.sleep(0)
                yield e
        return agen()
    return fake_stream


# ── Registry shape ─────────────────────────────────────────────────────────


def test_registry_now_has_all_sixteen_providers():
    from services.research_providers import PROVIDERS

    expected = {
        # local
        "kb_fts", "project_documents", "project_notes", "chat_history",
        # internal — sprint 1
        "confluence", "confluence_search", "email", "webex", "jira", "handbook",
        # internal — sprint 2
        "log_servers", "code_graph", "iq", "github", "jenkins", "mq",
    }
    assert set(PROVIDERS.keys()) == expected


@pytest.mark.parametrize("key,expected_side_effect", [
    ("log_servers", "read"),    # local-cached logs, read-only
    ("code_graph", "read"),     # local source-tree, read-only
    ("iq", "external"),
    ("github", "external"),
    ("jenkins", "external"),
    ("mq", "external"),
])
def test_sprint2_provider_side_effects(key, expected_side_effect):
    from services.research_providers import PROVIDERS

    p = PROVIDERS[key]
    assert p.default_enabled is False
    assert p.side_effect == expected_side_effect


# ── Health probes ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("key", [
    "log_servers", "code_graph", "iq", "github", "jenkins", "mq",
])
def test_sprint2_provider_health_ok_when_ai_assist_up(monkeypatch, key):
    from services.research_providers import PROVIDERS
    import services.ai_assist_client as aac

    async def _yes():
        return True

    monkeypatch.setattr(aac.ai_assist, "health_check", _yes)
    health = _run(PROVIDERS[key].health())
    assert health.ok is True


# ── log_servers ────────────────────────────────────────────────────────────


def test_log_servers_parses_grep_matches(monkeypatch):
    from services.research_providers import PROVIDERS
    import services.research_providers._streaming as st

    monkeypatch.setattr(st.ai_assist, "agent_stream", _fake_tool_stream([
        {"type": "tool_result", "data": {
            "name": "log_grep", "success": True,
            "data": {"matches": [
                {"log_id": "wlp-prod-1", "line_number": 4321,
                 "context": "ERROR Auth failure for user X",
                 "level": "ERROR", "timestamp": "2026-05-12T08:00Z"},
            ]},
        }},
        {"type": "done", "data": {}},
    ]))
    events = _run(_drain(PROVIDERS["log_servers"].stream(
        "auth failure",
        provider_settings={"max_results": 10, "context_lines": 2},
        cancel=asyncio.Event(),
        project_id="p",
    )))
    findings = [e for e in events if e.kind == "finding"]
    assert len(findings) == 1
    assert findings[0].finding.source_ref == "log:wlp-prod-1#L4321"
    assert findings[0].finding.raw_metadata["level"] == "ERROR"


def test_log_servers_iterates_configured_log_ids(monkeypatch):
    from services.research_providers import PROVIDERS
    import services.research_providers._streaming as st

    invocations: list[str] = []

    def fake_stream(*, session_id, message, model=None, project_path=None, extra=None):
        invocations.append(message)

        async def agen():
            yield {"type": "tool_result", "data": {
                "name": "log_grep", "success": True,
                "data": {"matches": []},
            }}
            yield {"type": "done", "data": {}}
        return agen()

    monkeypatch.setattr(st.ai_assist, "agent_stream", fake_stream)
    events = _run(_drain(PROVIDERS["log_servers"].stream(
        "x",
        provider_settings={"log_ids": ["wlp-1", "wlp-2"]},
        cancel=asyncio.Event(),
        project_id="p",
    )))
    assert len(invocations) == 2
    # One terminal at the very end (not one per log).
    assert events[-1].kind == "done"


# ── code_graph ─────────────────────────────────────────────────────────────


def test_code_graph_parses_symbol_hits(monkeypatch):
    from services.research_providers import PROVIDERS
    import services.research_providers._streaming as st

    monkeypatch.setattr(st.ai_assist, "agent_stream", _fake_tool_stream([
        {"type": "tool_result", "data": {
            "name": "graph_search", "success": True,
            "data": {"results": [
                {"id": "com.x.Auth#login",
                 "name": "login", "type": "method",
                 "language": "java", "file": "src/auth/Auth.java",
                 "line": 42,
                 "signature": "public AuthResult login(String user, String pwd)"},
            ]},
        }},
        {"type": "done", "data": {}},
    ]))
    events = _run(_drain(PROVIDERS["code_graph"].stream(
        "login method",
        provider_settings={"language": "java", "type": "method"},
        cancel=asyncio.Event(),
        project_id="p",
    )))
    findings = [e for e in events if e.kind == "finding"]
    assert len(findings) == 1
    assert findings[0].finding.source_ref == "code:com.x.Auth#login"
    assert "method:" in findings[0].finding.title.lower()
    assert findings[0].finding.raw_metadata["file"] == "src/auth/Auth.java"


# ── iq ─────────────────────────────────────────────────────────────────────


def test_iq_missing_app_id_yields_config_missing(monkeypatch):
    """No app_id → clean error event, no agent_stream call."""
    from services.research_providers import PROVIDERS
    import services.research_providers._streaming as st

    network_hit = {"flag": False}

    def must_not_be_called(*a, **kw):
        network_hit["flag"] = True

        async def empty():
            if False:
                yield {}
        return empty()

    monkeypatch.setattr(st.ai_assist, "agent_stream", must_not_be_called)

    events = _run(_drain(PROVIDERS["iq"].stream(
        "any query",
        provider_settings={},  # no app_id
        cancel=asyncio.Event(),
        project_id="p",
    )))
    assert network_hit["flag"] is False
    errors = [e for e in events if e.kind == "error"]
    assert errors and "app_id" in errors[0].error
    assert events[-1].kind == "done"
    assert events[-1].status_text == "config_missing"


def test_iq_parses_violations_when_app_id_set(monkeypatch):
    from services.research_providers import PROVIDERS
    import services.research_providers._streaming as st

    monkeypatch.setattr(st.ai_assist, "agent_stream", _fake_tool_stream([
        {"type": "tool_result", "data": {
            "name": "iq_findings", "success": True,
            "data": {"violations": [
                {"id": "vio-1", "component": "log4j:2.14",
                 "policy": "Security-High", "severity": "critical",
                 "summary": "CVE-2021-44228 (log4shell)",
                 "remediation": {"version": "2.17.1"}},
            ]},
        }},
        {"type": "done", "data": {}},
    ]))

    events = _run(_drain(PROVIDERS["iq"].stream(
        "log4shell",
        provider_settings={"app_id": "my-app-id", "organization_id": "org-1"},
        cancel=asyncio.Event(),
        project_id="p",
    )))
    findings = [e for e in events if e.kind == "finding"]
    assert len(findings) == 1
    assert findings[0].finding.source_ref == "iq:vio-1"
    assert findings[0].finding.raw_metadata["severity"] == "critical"
    assert findings[0].finding.raw_metadata["remediation"] == {"version": "2.17.1"}


# ── github ─────────────────────────────────────────────────────────────────


def test_github_uses_list_prs_when_default_repo_set(monkeypatch):
    from services.research_providers import PROVIDERS
    import services.research_providers._streaming as st

    captured_msg: list[str] = []

    def fake_stream(*, session_id, message, model=None, project_path=None, extra=None):
        captured_msg.append(message)

        async def agen():
            yield {"type": "tool_result", "data": {
                "name": "github_list_prs", "success": True,
                "data": {"prs": [
                    {"number": 42, "title": "Add PKCE flow",
                     "body": "Implements PKCE for service X",
                     "html_url": "https://github/x/pull/42",
                     "state": "open", "repo": "x/service-x",
                     "user": "alice"},
                ]},
            }}
            yield {"type": "done", "data": {}}
        return agen()

    monkeypatch.setattr(st.ai_assist, "agent_stream", fake_stream)

    events = _run(_drain(PROVIDERS["github"].stream(
        "PKCE",
        provider_settings={"default_repo": "x/service-x", "pr_state": "open"},
        cancel=asyncio.Event(),
        project_id="p",
    )))
    assert captured_msg and "github_list_prs" in captured_msg[0]
    findings = [e for e in events if e.kind == "finding"]
    assert len(findings) == 1
    assert findings[0].finding.source_ref == "github:pr:x/service-x#42"


def test_github_uses_search_repos_when_no_default_repo(monkeypatch):
    from services.research_providers import PROVIDERS
    import services.research_providers._streaming as st

    captured_msg: list[str] = []

    def fake_stream(*, session_id, message, model=None, project_path=None, extra=None):
        captured_msg.append(message)

        async def agen():
            yield {"type": "tool_result", "data": {
                "name": "github_search_repos", "success": True,
                "data": {"results": [
                    {"full_name": "x/auth-service", "description": "Auth gateway",
                     "html_url": "https://github/x/auth-service",
                     "stargazers_count": 7, "language": "Java"},
                ]},
            }}
            yield {"type": "done", "data": {}}
        return agen()

    monkeypatch.setattr(st.ai_assist, "agent_stream", fake_stream)

    events = _run(_drain(PROVIDERS["github"].stream(
        "auth",
        provider_settings={"default_org": "x"},
        cancel=asyncio.Event(),
        project_id="p",
    )))
    assert captured_msg and "github_search_repos" in captured_msg[0]
    findings = [e for e in events if e.kind == "finding"]
    assert len(findings) == 1
    assert findings[0].finding.source_ref == "github:repo:x/auth-service"


# ── jenkins (read-only assertion + happy path) ────────────────────────────


def test_jenkins_trigger_build_remains_blacklisted():
    """Defence-in-depth: a regression that removes jenkins_trigger_build
    from the blacklist would make the construction-time assertion in
    JenkinsProvider.__init__ fail loud."""
    from services.research_providers._streaming import _TOOL_BLACKLIST

    assert "jenkins_trigger_build" in _TOOL_BLACKLIST


def test_jenkins_job_status_happy_path(monkeypatch):
    from services.research_providers import PROVIDERS
    import services.research_providers._streaming as st

    monkeypatch.setattr(st.ai_assist, "agent_stream", _fake_tool_stream([
        {"type": "tool_result", "data": {
            "name": "jenkins_job_status", "success": True,
            "data": {
                "name": "service-x",
                "color": "blue",
                "last_build": {"number": 42, "result": "SUCCESS"},
                "in_queue": False,
                "url": "https://jenkins/job/service-x",
            },
        }},
        {"type": "done", "data": {}},
    ]))

    events = _run(_drain(PROVIDERS["jenkins"].stream(
        "service-x",  # used as job name
        provider_settings={},
        cancel=asyncio.Event(),
        project_id="p",
    )))
    findings = [e for e in events if e.kind == "finding"]
    assert len(findings) == 1
    assert findings[0].finding.source_ref == "jenkins:job:service-x"
    assert "service-x" in findings[0].finding.title


def test_jenkins_build_info_when_build_number_set(monkeypatch):
    from services.research_providers import PROVIDERS
    import services.research_providers._streaming as st

    monkeypatch.setattr(st.ai_assist, "agent_stream", _fake_tool_stream([
        {"type": "tool_result", "data": {
            "name": "jenkins_build_info", "success": True,
            "data": {
                "job": "service-x", "number": 42, "result": "FAILURE",
                "console": "Compile error in Auth.java:42",
                "duration": 120000, "url": "https://jenkins/job/service-x/42",
            },
        }},
        {"type": "done", "data": {}},
    ]))

    events = _run(_drain(PROVIDERS["jenkins"].stream(
        "",
        provider_settings={"job": "service-x", "build": 42},
        cancel=asyncio.Event(),
        project_id="p",
    )))
    findings = [e for e in events if e.kind == "finding"]
    assert len(findings) == 1
    assert findings[0].finding.source_ref == "jenkins:build:service-x#42"
    assert "FAILURE" in findings[0].finding.title


def test_jenkins_missing_job_yields_config_missing(monkeypatch):
    from services.research_providers import PROVIDERS
    import services.research_providers._streaming as st

    network_hit = {"flag": False}

    def must_not_be_called(**_):
        network_hit["flag"] = True

        async def empty():
            if False:
                yield {}
        return empty()

    monkeypatch.setattr(st.ai_assist, "agent_stream", must_not_be_called)

    events = _run(_drain(PROVIDERS["jenkins"].stream(
        "",  # empty query
        provider_settings={},
        cancel=asyncio.Event(),
        project_id="p",
    )))
    assert network_hit["flag"] is False
    errors = [e for e in events if e.kind == "error"]
    assert errors and "Job" in errors[0].error
    assert events[-1].status_text == "config_missing"


# ── mq (filters trigger queues) ────────────────────────────────────────────


def test_mq_filters_out_trigger_queues(monkeypatch):
    """Read-only Auto-Mode: trigger-role queues must not surface."""
    from services.research_providers import PROVIDERS
    import services.research_providers._streaming as st

    monkeypatch.setattr(st.ai_assist, "agent_stream", _fake_tool_stream([
        {"type": "tool_result", "data": {
            "name": "mq_list_queues", "success": True,
            "data": {"queues": [
                {"id": "q1", "name": "q1", "url": "http://x/q1",
                 "service": "auth", "role": "read"},
                {"id": "q2", "name": "q2", "url": "http://x/q2",
                 "service": "billing", "role": "trigger"},     # filtered
                {"id": "q3", "name": "q3", "url": "http://x/q3",
                 "service": "audit", "role": "both"},
            ]},
        }},
        {"type": "done", "data": {}},
    ]))

    events = _run(_drain(PROVIDERS["mq"].stream(
        "any",
        provider_settings={},
        cancel=asyncio.Event(),
        project_id="p",
    )))
    findings = [e for e in events if e.kind == "finding"]
    refs = [f.finding.source_ref for f in findings]
    assert "mq:q1" in refs
    assert "mq:q3" in refs
    assert "mq:q2" not in refs, "trigger-role queue leaked through"


# ── Cancel pre-set across the six sprint-2 providers ──────────────────────


@pytest.mark.parametrize("key,settings", [
    ("log_servers", {}),
    ("code_graph", {}),
    ("iq", {"app_id": "x"}),     # don't trip the config-missing branch
    ("github", {}),
    ("jenkins", {"job": "x"}),    # don't trip the config-missing branch
    ("mq", {}),
])
def test_sprint2_provider_cancel_pre_set_short_circuits(monkeypatch, key, settings):
    from services.research_providers import PROVIDERS
    import services.research_providers._streaming as st

    hit = {"net": False}

    def must_not_be_called(**_):
        hit["net"] = True

        async def empty():
            if False:
                yield {}
        return empty()

    monkeypatch.setattr(st.ai_assist, "agent_stream", must_not_be_called)

    cancel = asyncio.Event()
    cancel.set()
    events = _run(_drain(PROVIDERS[key].stream(
        "q", provider_settings=settings, cancel=cancel, project_id="p",
    )))
    assert hit["net"] is False, f"{key}: pre-set cancel must short-circuit"
    assert events[-1].kind == "done"
    assert events[-1].status_text == "cancelled"
