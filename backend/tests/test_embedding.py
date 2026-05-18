"""Tests for ``services/embedding/`` (T2.2).

Two surfaces under test:

* ``LiteLLMEmbedder`` — HTTP client against AI-Assist's /api/embed,
  with httpx.MockTransport stubbing the upstream. Covers happy path,
  batch splitting (>MAX_BATCH), dim-learning, dim-drift detection,
  upstream 5xx → EmbeddingError, empty input fast-path.

* ``get_default_embedder()`` — settings-gated lazy singleton with reset
  hook. Covers OFF-default, cache, reset-clears-cache.
"""
from __future__ import annotations

import httpx
import pytest

# conftest pins PROJECTHUB_DB_PATH; same backend sys.path setup applies.
from services.embedding import (
    LiteLLMEmbedder,
    get_default_embedder,
    reset_default_embedder,
)
from services.embedding.litellm_router import MAX_BATCH
from services.embedding.protocol import Embedder, EmbeddingError


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_default_embedder()
    yield
    reset_default_embedder()


def _make_embedder(handler, *, model: str = "test-model") -> LiteLLMEmbedder:
    return LiteLLMEmbedder(
        base_url="http://test-ai-assist",
        model=model,
        transport=httpx.MockTransport(handler),
    )


# ── LiteLLMEmbedder: happy path ──────────────────────────────────────


@pytest.mark.asyncio
async def test_embedder_happy_path_returns_vectors() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen["body"] = json.loads(request.content)
        assert request.url.path == "/api/embed"
        return httpx.Response(
            200,
            json={
                "embeddings": [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]],
                "model": "test-model",
                "dim": 3,
                "usage": {"tokens": 4},
            },
        )

    emb = _make_embedder(handler)
    out = await emb.embed(["foo", "bar"])
    assert out == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
    assert emb.dim == 3
    assert seen["body"] == {"texts": ["foo", "bar"], "model": "test-model"}


@pytest.mark.asyncio
async def test_embed_one_short_circuits_on_empty() -> None:
    """``embed_one`` MUST NOT hit the network when text is empty."""
    calls = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        calls[0] += 1
        return httpx.Response(500)

    emb = _make_embedder(handler)
    out = await emb.embed_one("")
    assert out == []
    assert calls[0] == 0


@pytest.mark.asyncio
async def test_embed_empty_list_returns_empty_without_request() -> None:
    """``embed([])`` is a defensive no-op — bridge enforces minItems=1."""
    calls = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        calls[0] += 1
        return httpx.Response(500)

    emb = _make_embedder(handler)
    out = await emb.embed([])
    assert out == []
    assert calls[0] == 0


# ── batching ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_embed_splits_into_chunks_over_max_batch() -> None:
    """Inputs larger than MAX_BATCH must split into multiple chunks while
    preserving order — same vector at the same index in the output."""
    call_log: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(request.content)
        call_log.append(len(body["texts"]))
        # echo the text index back as the embedding (1-d)
        # so order errors are visually obvious
        return httpx.Response(
            200,
            json={
                "embeddings": [[float(int(t))] for t in body["texts"]],
                "model": "test-model",
                "dim": 1,
                "usage": {"tokens": 0},
            },
        )

    emb = _make_embedder(handler)
    inputs = [str(i) for i in range(MAX_BATCH + 5)]
    out = await emb.embed(inputs)
    # MAX_BATCH (chunk 1) + 5 (chunk 2) = MAX_BATCH+5 vectors total
    assert len(out) == MAX_BATCH + 5
    assert call_log == [MAX_BATCH, 5]
    # Order preserved: out[i][0] == i
    for i, v in enumerate(out):
        assert v == [float(i)]


# ── dim learning + drift ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_embed_learns_dim_on_first_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "embeddings": [[1.0, 2.0, 3.0, 4.0]],
                "model": "test-model",
                "dim": 4,
                "usage": {"tokens": 1},
            },
        )

    emb = _make_embedder(handler)
    assert emb.dim == 0
    await emb.embed(["x"])
    assert emb.dim == 4


@pytest.mark.asyncio
async def test_dim_drift_raises_embedding_error() -> None:
    """Once dim is learned, a different-dim response means an upstream
    model swap — we surface that loudly so cached embeddings get
    invalidated."""
    call = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        call[0] += 1
        if call[0] == 1:
            return httpx.Response(
                200,
                json={
                    "embeddings": [[1.0, 2.0]],
                    "model": "test-model",
                    "dim": 2,
                    "usage": {"tokens": 1},
                },
            )
        return httpx.Response(
            200,
            json={
                "embeddings": [[1.0, 2.0, 3.0]],
                "model": "test-model",
                "dim": 3,
                "usage": {"tokens": 1},
            },
        )

    emb = _make_embedder(handler)
    await emb.embed(["a"])
    with pytest.raises(EmbeddingError, match="dim drift"):
        await emb.embed(["b"])


# ── error paths ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upstream_5xx_raises_embedding_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "down"})

    emb = _make_embedder(handler)
    with pytest.raises(EmbeddingError, match="503"):
        await emb.embed(["x"])


@pytest.mark.asyncio
async def test_upstream_unreachable_raises_embedding_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    emb = _make_embedder(handler)
    with pytest.raises(EmbeddingError, match="unreachable"):
        await emb.embed(["x"])


@pytest.mark.asyncio
async def test_mismatched_embedding_count_raises() -> None:
    """If upstream returns fewer vectors than we asked for, that's a
    bug we can't paper over."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "embeddings": [[0.1]],  # 1 vector
                "model": "test",
                "dim": 1,
            },
        )

    emb = _make_embedder(handler)
    with pytest.raises(EmbeddingError, match="for 2 texts"):
        await emb.embed(["a", "b"])  # asked for 2


# ── default singleton + config gate ─────────────────────────────────


def test_default_embedder_returns_none_when_disabled(monkeypatch) -> None:
    """When ``brain_embedding_enabled = False`` (default), the accessor
    returns None — Brain code can treat ``None`` as "fall back to FTS5"."""
    from config import settings

    monkeypatch.setattr(settings, "brain_embedding_enabled", False)
    assert get_default_embedder() is None


def test_default_embedder_caches(monkeypatch) -> None:
    from config import settings

    monkeypatch.setattr(settings, "brain_embedding_enabled", True)
    a = get_default_embedder()
    b = get_default_embedder()
    assert a is not None
    assert isinstance(a, Embedder)
    assert a is b


def test_reset_clears_singleton(monkeypatch) -> None:
    from config import settings

    monkeypatch.setattr(settings, "brain_embedding_enabled", True)
    a = get_default_embedder()
    reset_default_embedder()
    b = get_default_embedder()
    assert a is not None and b is not None and a is not b
