import logging

import httpx
from fastapi import APIRouter

from services.ai_assist_client import ai_assist
from services.jira_client import jira_client
from services.sse_hub import sse_hub

router = APIRouter(prefix="/api/settings", tags=["settings"])
logger = logging.getLogger("projecthub.settings")


@router.get("/ai-assist-status")
async def ai_assist_status():
    """Check if AI-Assist backend is reachable."""
    connected = await ai_assist.health_check()
    return {
        "connected": connected,
        "base_url": ai_assist.base_url,
        "sse_subscribers": sse_hub.subscriber_count,
    }


@router.get("/jira-status")
async def jira_status():
    """Check if Jira is reachable with AI-Assist-sourced credentials."""
    await jira_client.ensure_credentials(force_refresh=True)
    if not jira_client.configured:
        return {
            "configured": False,
            "connected": False,
            "base_url": "",
            "source": jira_client._creds_source,
            "error": "Nicht konfiguriert — AI-Assist liefert keine Jira-Credentials",
        }
    try:
        client = await jira_client._get_client()
        resp = await client.get("/rest/api/3/myself")
        resp.raise_for_status()
        me = resp.json()
        return {
            "configured": True,
            "connected": True,
            "base_url": jira_client._base_url,
            "source": jira_client._creds_source,
            "account": me.get("emailAddress") or me.get("displayName") or "",
        }
    except httpx.HTTPStatusError as e:
        return {
            "configured": True,
            "connected": False,
            "base_url": jira_client._base_url,
            "source": jira_client._creds_source,
            "error": f"HTTP {e.response.status_code}",
        }
    except Exception as e:
        logger.warning("Jira connectivity check failed: %s", e)
        return {
            "configured": True,
            "connected": False,
            "base_url": jira_client._base_url,
            "source": jira_client._creds_source,
            "error": str(e)[:200],
        }
