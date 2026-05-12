"""Lean Jira REST client.

Credentials policy: env-vars only. ProjectHub reads
``PROJECTHUB_JIRA_BASE_URL``, ``PROJECTHUB_JIRA_EMAIL``,
``PROJECTHUB_JIRA_API_TOKEN`` (see ``config.py``). The earlier design
that fetched ``{base_url, email, api_token}`` from AI-Assist via
``GET /api/config/jira`` was never implemented on the AI-Assist side
and was removed after the Engine-v2 migration (2026-05-12).

Auth: Jira Cloud API tokens (email + token as Basic Auth).
Reference: https://developer.atlassian.com/cloud/jira/platform/rest/v3/
"""

import base64
import logging
from typing import Any

import httpx

from config import settings

logger = logging.getLogger("projecthub.jira")


class JiraClient:
    """Async Jira REST client with env-var-sourced credentials.

    Usage:
        await jira_client.ensure_credentials()
        issues = await jira_client.search(jql="project = FOO")
    """

    def __init__(
        self,
        base_url: str | None = None,
        email: str | None = None,
        api_token: str | None = None,
        timeout: int | None = None,
    ):
        self._base_url = (base_url or settings.jira_base_url).rstrip("/")
        self._email = email or settings.jira_email
        self._api_token = api_token or settings.jira_api_token
        self._timeout = timeout or settings.jira_timeout
        self._client: httpx.AsyncClient | None = None
        self._creds_source: str = "env" if (self._base_url and self._api_token) else "unset"

    async def ensure_credentials(self, force_refresh: bool = False) -> None:
        """No-op kept for API compatibility.

        Historically this pulled creds from AI-Assist; that endpoint
        was retired in the v2 migration. Credentials now come from
        env-vars at process start (see ``__init__``); call sites can
        keep awaiting this method without effect.
        """
        return

    # --- Lifecycle ---

    @property
    def configured(self) -> bool:
        return bool(self._base_url and self._email and self._api_token)

    def _auth_header(self) -> dict[str, str]:
        token = f"{self._email}:{self._api_token}".encode("utf-8")
        return {"Authorization": "Basic " + base64.b64encode(token).decode("ascii")}

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(connect=5.0, read=self._timeout, write=10.0, pool=5.0),
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    **self._auth_header(),
                },
            )
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # --- API surface used by the adapter ---

    async def search(
        self,
        jql: str,
        fields: list[str] | None = None,
        start_at: int = 0,
        max_results: int = 50,
    ) -> list[dict[str, Any]]:
        """Issue search via JQL. Returns a list of issues (single page).

        Callers should paginate if they expect more than max_results.
        """
        await self.ensure_credentials()
        if not self.configured:
            raise RuntimeError(
                "Jira nicht konfiguriert — PROJECTHUB_JIRA_BASE_URL, "
                "PROJECTHUB_JIRA_EMAIL und PROJECTHUB_JIRA_API_TOKEN setzen"
            )

        client = await self._get_client()
        body = {
            "jql": jql,
            "startAt": start_at,
            "maxResults": max_results,
        }
        if fields:
            body["fields"] = fields

        resp = await client.post("/rest/api/3/search", json=body)
        resp.raise_for_status()
        data = resp.json()
        return data.get("issues", []) or []

    async def get_issue(self, key: str, fields: list[str] | None = None) -> dict[str, Any] | None:
        await self.ensure_credentials()
        if not self.configured:
            return None
        client = await self._get_client()
        params = {}
        if fields:
            params["fields"] = ",".join(fields)
        try:
            resp = await client.get(f"/rest/api/3/issue/{key}", params=params)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            logger.warning("Jira HTTP %s on issue %s", e.response.status_code, key)
            raise


# Singleton — re-instantiate if settings change at runtime
jira_client = JiraClient()


def _text_from_adf(adf: Any) -> str:
    """Extract plaintext from Atlassian Document Format (ADF).

    Jira Cloud returns descriptions/comments as ADF. We only need enough text
    for the LLM prompt — do a depth-first walk over `content` arrays.
    """
    if adf is None:
        return ""
    if isinstance(adf, str):
        return adf
    if isinstance(adf, dict):
        out: list[str] = []
        if adf.get("type") == "text" and isinstance(adf.get("text"), str):
            out.append(adf["text"])
        for child in adf.get("content", []) or []:
            out.append(_text_from_adf(child))
        return " ".join(x for x in out if x)
    if isinstance(adf, list):
        return " ".join(_text_from_adf(x) for x in adf)
    return ""
