"""Shared LLM helpers for the synapse pipeline.

A thin wrapper over ``ai_assist.agent_call`` tailored to what the synapse
pipeline (entity extraction, synthesis, validation) needs:

* robust JSON extraction from a chat-completion response,
* optional ``model`` routing — used by the validation critic fan-out to
  spread samples across GPT-OSS / Qwen / DeepSeek-R1,
* token-usage accounting so a generation run can report its cost.

Spike note (2026-05-14): the backend has no local ML stack, so there is
no MiniCheck-class NLI model — the validation pipeline's grounding tier
is "LLM-as-NLI" (a cheap, narrowly-prompted call through this helper).
"""

from __future__ import annotations

import json
import logging
import re
import secrets
from dataclasses import dataclass
from typing import Any

from services.ai_assist_client import ai_assist

logger = logging.getLogger("projecthub.synapse")

_JSON_OBJ_RE = re.compile(r"\{[\s\S]*\}")
_JSON_ARR_RE = re.compile(r"\[[\s\S]*\]")


def gen_id() -> str:
    return secrets.token_hex(8)


def extract_json(text: str) -> Any | None:
    """Best-effort parse of a JSON object/array from an LLM response.

    Handles ```json fences and leading/trailing prose by falling back to
    the largest ``{...}`` / ``[...]`` block. Returns ``None`` if nothing
    parses — callers treat that as a failed call.
    """
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.IGNORECASE).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    for rx in (_JSON_OBJ_RE, _JSON_ARR_RE):
        m = rx.search(t)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                continue
    return None


@dataclass
class LLMResult:
    """Outcome of one synapse LLM call."""

    parsed: Any | None
    raw: str
    model: str
    usage: dict
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.parsed is not None and self.error is None


def _usage_tokens(usage: dict) -> int:
    """Pull a total-token count out of whatever shape the usage dict has."""
    if not isinstance(usage, dict):
        return 0
    for key in ("total_tokens", "total", "tokens"):
        v = usage.get(key)
        if isinstance(v, (int, float)):
            return int(v)
    prompt = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
    completion = usage.get("completion_tokens") or usage.get("output_tokens") or 0
    try:
        return int(prompt) + int(completion)
    except (TypeError, ValueError):
        return 0


def merge_usage(acc: dict, usage: dict) -> dict:
    """Accumulate per-call usage into a running total for a generation run."""
    acc["calls"] = acc.get("calls", 0) + 1
    acc["total_tokens"] = acc.get("total_tokens", 0) + _usage_tokens(usage)
    return acc


async def call_json(
    message: str,
    *,
    model: str | None = None,
    session_prefix: str = "synapse",
) -> LLMResult:
    """One-shot LLM call expecting a JSON response. Never raises.

    ``model=None`` lets the AI-Assist engine pick its default; pass an
    explicit model name for the heterogeneous critic fan-out.
    """
    session_id = f"projecthub-{session_prefix}-{gen_id()}"
    try:
        result = await ai_assist.agent_call(
            session_id=session_id,
            message=message,
            model=model,
            auto_detect=False,  # pure extraction/synthesis — no domain tools
        )
    except Exception as e:  # defensive — agent_call shouldn't raise, but never trust
        logger.warning("synapse LLM call failed: %s", e)
        return LLMResult(parsed=None, raw="", model=model or "", usage={}, error=str(e))

    if not result or not isinstance(result, dict):
        return LLMResult(
            parsed=None, raw="", model=model or "", usage={},
            error="ai_assist_unreachable",
        )

    raw = result.get("response") or ""
    parsed = extract_json(raw)
    return LLMResult(
        parsed=parsed,
        raw=raw,
        model=result.get("model") or model or "",
        usage=result.get("usage") or {},
        error=result.get("error") if parsed is None else None,
    )
