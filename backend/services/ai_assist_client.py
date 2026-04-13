import json
import logging
from datetime import datetime, timezone
from typing import AsyncIterator

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import async_session
from models.cache import OfflineCache

logger = logging.getLogger("projecthub.ai_assist")


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

    # --- Cache helpers ---

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

    # --- Core HTTP methods ---

    async def get(self, path: str, params: dict | None = None, cache_key: str | None = None, cache_type: str = "generic") -> dict | list | None:
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

    async def stream_post(self, path: str, body: dict) -> AsyncIterator[str]:
        """Stream SSE from AI-Assist (for chat/LLM)."""
        client = await self._ensure_client()
        # Use a longer timeout for LLM streaming
        stream_client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(connect=5.0, read=float(settings.ai_assist_llm_timeout), write=10.0, pool=5.0),
        )
        try:
            async with stream_client.stream("POST", path, json=body) as resp:
                self.is_connected = True
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        yield line + "\n\n"
        except (httpx.ConnectError, httpx.TimeoutException):
            self.is_connected = False
            yield 'data: {"error": "AI-Assist nicht erreichbar", "done": true}\n\n'
        finally:
            await stream_client.aclose()

    # --- Health ---

    async def health_check(self) -> bool:
        client = await self._ensure_client()
        try:
            resp = await client.get("/api/health", timeout=5.0)
            self.is_connected = resp.status_code == 200
        except Exception:
            self.is_connected = False
        return self.is_connected

    # --- Convenience methods ---

    async def get_jenkins_jobs(self, path_name: str | None = None) -> dict | None:
        params = {"path_name": path_name} if path_name else None
        return await self.get(
            "/api/jenkins/jobs",
            params=params,
            cache_key=f"jenkins:jobs:{path_name or 'default'}",
            cache_type="jenkins_status",
        )

    async def get_github_prs(self, owner: str, repo: str) -> list | None:
        # AI-Assist doesn't have a direct "list PRs for repo" endpoint,
        # so we use the repos endpoint to get repo info with open_issues_count
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

    async def search_emails(self, query: str = "", folder: str = "inbox", limit: int = 20) -> dict | None:
        return await self.post("/api/email/search", {
            "query": query, "folder": folder, "limit": limit,
        })

    async def get_webex_rooms(self) -> dict | None:
        return await self.get("/api/webex/rooms", cache_key="webex:rooms", cache_type="webex_rooms")

    async def get_webex_messages(self, room_id: str, limit: int = 50) -> dict | None:
        return await self.get(f"/api/webex/rooms/{room_id}/messages", params={"limit": limit})

    async def get_email_todos(self, status: str = "new") -> dict | None:
        return await self.get("/api/email/todos", params={"status": status})


# Singleton
ai_assist = AiAssistClient()
