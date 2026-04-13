from fastapi import APIRouter
from services.ai_assist_client import ai_assist
from services.sse_hub import sse_hub

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("/ai-assist-status")
async def ai_assist_status():
    """Check if AI-Assist backend is reachable."""
    connected = await ai_assist.health_check()
    return {
        "connected": connected,
        "base_url": ai_assist.base_url,
        "sse_subscribers": sse_hub.subscriber_count,
    }
