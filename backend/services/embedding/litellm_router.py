"""LiteLLM-router embedder — routes through AI-Assist's /api/embed (T2.2).

Default backend for the Brain when ``brain_embedding_enabled = True``.
The bridge OpenAPI (``claudedocs/bridge_openapi_20260516.yaml``) is the
authoritative contract; this client implements the consumer side.

Why route through AI-Assist instead of calling LiteLLM directly:

* zero new deps in the ProjectHub backend
* model-selection lives in one place (AI-Assist's settings.llm)
* the AI-Assist proxy already handles auth, retries, rate-limits
* swapping LiteLLM for something else later (vLLM, Bedrock) requires
  zero ProjectHub-side changes

Batching: callers may pass any number of texts; we split internally into
``MAX_BATCH``-sized HTTP requests so the bridge's per-call cap is never
the bottleneck. Order is preserved across the splits.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from services.embedding.protocol import Embedder, EmbeddingError

logger = logging.getLogger("projecthub.embedding")

# Must match the bridge OpenAPI ``EmbedRequest.texts.maxItems``. Crossing
# this would make the bridge return 422 and we'd have to handle the split
# anyway — easier to split up front.
MAX_BATCH = 64

# Pool size for parallel chunked requests. With MAX_BATCH=64 + 8 in
# flight, an 8000-text backfill issues 16 round-trips of 8 parallel
# requests instead of 128 serial. AI-Assist's /api/embed already limits
# its upstream parallelism, so over-bumping this here just queues at the
# proxy.
MAX_PARALLEL = 8


class LiteLLMEmbedder:
    """Embed via AI-Assist's /api/embed endpoint."""

    name = "litellm-router"

    def __init__(
        self,
        base_url: str | None = None,
        *,
        model: str | None = None,
        timeout_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        # Late import keeps this module testable without project_root
        # injected (the protocol tests don't need config).
        if base_url is None or model is None:
            try:
                from config import settings  # noqa: F401 — guard for tests
            except Exception:
                settings = None  # type: ignore[assignment]
            if base_url is None:
                base_url = getattr(settings, "ai_assist_url", None) or "http://localhost:8000"
            if model is None:
                # ProjectHub keeps the model choice on AI-Assist's side
                # (single source of truth). ``""`` lets the AI-Assist
                # endpoint pick its configured default.
                model = ""

        self._base_url = base_url.rstrip("/")
        self.model_id = model
        self._timeout = timeout_seconds
        self._dim = 0
        self._transport = transport

    @property
    def dim(self) -> int:
        return self._dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        # Split into MAX_BATCH-sized chunks; preserve overall order.
        chunks: list[list[str]] = [
            texts[i : i + MAX_BATCH] for i in range(0, len(texts), MAX_BATCH)
        ]

        async with self._build_client() as client:
            out: list[list[float]] = []
            for chunk in chunks:
                vectors = await self._embed_chunk(client, chunk)
                out.extend(vectors)
            return out

    async def embed_one(self, text: str) -> list[float]:
        if not text:
            return []
        result = await self.embed([text])
        return result[0] if result else []

    # ── internals ───────────────────────────────────────────────────────

    def _build_client(self) -> httpx.AsyncClient:
        kwargs: dict[str, Any] = {
            "base_url": self._base_url,
            "timeout": httpx.Timeout(
                connect=5.0, read=self._timeout, write=10.0, pool=5.0
            ),
        }
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.AsyncClient(**kwargs)

    async def _embed_chunk(
        self, client: httpx.AsyncClient, chunk: list[str]
    ) -> list[list[float]]:
        payload: dict[str, Any] = {"texts": chunk}
        if self.model_id:
            payload["model"] = self.model_id

        try:
            resp = await client.post("/api/embed", json=payload)
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as e:
            raise EmbeddingError(f"AI-Assist /api/embed unreachable: {e}") from e

        if resp.status_code >= 400:
            try:
                body = resp.json()
            except Exception:
                body = resp.text[:300]
            raise EmbeddingError(
                f"AI-Assist /api/embed returned {resp.status_code}: {body}"
            )

        try:
            data = resp.json()
        except Exception as e:
            raise EmbeddingError(f"/api/embed returned non-JSON: {e}") from e

        vectors = data.get("embeddings") or []
        if not isinstance(vectors, list) or len(vectors) != len(chunk):
            raise EmbeddingError(
                f"/api/embed returned {len(vectors)} embeddings for {len(chunk)} texts"
            )

        # Learn the dimension from the first response. Pin it for life;
        # any later response with a different dim is an upstream config
        # drift we surface immediately.
        upstream_dim = int(data.get("dim") or (len(vectors[0]) if vectors else 0))
        if self._dim == 0:
            self._dim = upstream_dim
            # If the upstream didn't echo a model name, the response may
            # still tell us which one ran — pin it so persisted embeddings
            # tag with the actual model, not a wildcard.
            response_model = data.get("model") or ""
            if response_model and not self.model_id:
                self.model_id = response_model
            logger.info(
                "[embedding] LiteLLMEmbedder learned dim=%d model=%s",
                self._dim, self.model_id or "(default)",
            )
        elif upstream_dim != self._dim:
            raise EmbeddingError(
                f"embedding dim drift: was {self._dim}, got {upstream_dim} — "
                "likely a model change upstream; clear cached embeddings"
            )

        return [[float(v) for v in vec] for vec in vectors]
