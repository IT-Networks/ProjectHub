"""Tests for ``services/retrieval/contextual.py`` (T2.4).

The LLM is injected (``AIAssistProtocol``-stub) so these tests are
deterministic without standing up ``ai_assist``. Three things matter:

* Snippet generation produces a single, cleaned, capped sentence.
* Failure paths return ``""`` (NEVER raise).
* The backfill iterates and skips items that already have a snippet.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

import pytest

import os
# Run unit tests against the same throwaway DB conftest already pins —
# the contextual module itself doesn't open a DB connection except in the
# backfill helper, which we exercise via the in-process FastAPI app.
from services.retrieval import contextual
from services.retrieval.contextual import (
    BackfillStats,
    _clean_snippet,
    generate_context,
)


# ── Fake AI-Assist client ────────────────────────────────────────────────


@dataclass
class _FakeAIAssist:
    response: str = "A clean sentence about this item."
    raise_on_call: BaseException | None = None
    calls: list[dict] = field(default_factory=list)

    async def agent_call(
        self, *, session_id, message, model=None, auto_detect=False,
        project_path=None,
    ):
        self.calls.append({
            "session_id": session_id, "message": message, "model": model,
        })
        if self.raise_on_call is not None:
            raise self.raise_on_call
        return {
            "response": self.response,
            "model": model or "test-model",
            "usage": {"total_tokens": 50},
            "error": None,
        }


# ── _clean_snippet ───────────────────────────────────────────────────────


def test_clean_snippet_collapses_multiline_to_one() -> None:
    out = _clean_snippet("First line.\n\nSecond line.")
    assert out == "First line. Second line."


def test_clean_snippet_strips_code_fence() -> None:
    raw = "```\nClean inside fence.\n```"
    assert _clean_snippet(raw) == "Clean inside fence."


def test_clean_snippet_strips_label_prefix() -> None:
    assert _clean_snippet("Antwort: actual sentence here") == "actual sentence here"
    assert _clean_snippet("Context: another") == "another"
    assert _clean_snippet("kontext: dritter") == "dritter"


def test_clean_snippet_caps_at_200_chars_with_ellipsis() -> None:
    out = _clean_snippet("x " * 300)
    assert len(out) <= 200
    assert out.endswith("…")


def test_clean_snippet_strips_wrapping_quotes() -> None:
    assert _clean_snippet('"wrapped"') == "wrapped"
    assert _clean_snippet("„wrapped“") == "wrapped"


def test_clean_snippet_empty_in_empty_out() -> None:
    assert _clean_snippet("") == ""
    assert _clean_snippet("   ") == ""


# ── generate_context ────────────────────────────────────────────────────


def _make_item(title="Test", content_plain="some body", tags=None, category="reference"):
    from models.knowledge import KnowledgeItem

    return KnowledgeItem(
        id="abcd1234abcd1234",
        project_id="proj1",
        title=title,
        content="",
        content_plain=content_plain,
        category=category,
        source_type="manual",
        tags=json.dumps(tags or []),
        confidence="medium",
        extra_data="{}",
    )


def _make_project(name="My Project"):
    from models.project import Project

    return Project(id="proj1", name=name)


@pytest.mark.asyncio
async def test_generate_context_happy_path_returns_cleaned_sentence() -> None:
    ai = _FakeAIAssist(response="One concise sentence about LiteLLM.")
    item = _make_item(title="LiteLLM Setup", content_plain="The LiteLLM proxy on port 8080.")
    project = _make_project("AI-Assist")

    out = await generate_context(item, project, ai_assist=ai)
    assert out == "One concise sentence about LiteLLM."
    assert len(ai.calls) == 1
    # Prompt mentions all the framing pieces
    msg = ai.calls[0]["message"]
    assert "AI-Assist" in msg
    assert "LiteLLM Setup" in msg
    assert "reference" in msg


@pytest.mark.asyncio
async def test_generate_context_returns_empty_when_body_and_title_blank() -> None:
    ai = _FakeAIAssist()  # would respond with text, but we should short-circuit
    item = _make_item(title="", content_plain="")
    out = await generate_context(item, _make_project(), ai_assist=ai)
    assert out == ""
    assert ai.calls == []


@pytest.mark.asyncio
async def test_generate_context_returns_empty_on_llm_exception() -> None:
    ai = _FakeAIAssist(raise_on_call=RuntimeError("LLM down"))
    item = _make_item()
    out = await generate_context(item, _make_project(), ai_assist=ai)
    assert out == ""


@pytest.mark.asyncio
async def test_generate_context_returns_empty_on_garbage_response() -> None:
    ai = _FakeAIAssist(response="```\n\n```")  # only fences, no content
    item = _make_item()
    out = await generate_context(item, _make_project(), ai_assist=ai)
    assert out == ""


@pytest.mark.asyncio
async def test_generate_context_tags_propagate_into_prompt() -> None:
    ai = _FakeAIAssist(response="ok")
    item = _make_item(tags=["llm", "infra", "production"])
    await generate_context(item, _make_project(), ai_assist=ai)
    msg = ai.calls[0]["message"]
    assert "llm" in msg and "infra" in msg and "production" in msg


@pytest.mark.asyncio
async def test_generate_context_handles_corrupt_tags_json() -> None:
    """A bad tags JSON shouldn't blow up the snippet flow."""
    ai = _FakeAIAssist(response="snippet")
    item = _make_item()
    item.tags = "{not json"
    out = await generate_context(item, _make_project(), ai_assist=ai)
    assert out == "snippet"


@pytest.mark.asyncio
async def test_generate_context_caps_response_length() -> None:
    """An LLM that overshoots gets clipped to 200 chars."""
    ai = _FakeAIAssist(response="A " * 300)
    item = _make_item()
    out = await generate_context(item, _make_project(), ai_assist=ai)
    assert len(out) <= 200
