from fastapi import APIRouter, Query
from sse_starlette.sse import EventSourceResponse

from services.sse_hub import sse_hub

router = APIRouter(tags=["events"])


@router.get("/api/events")
async def event_stream(filter: str | None = Query(None)):
    """SSE stream for live updates. Optional ?filter=builds,pulls to filter event types."""
    filter_types = set(filter.split(",")) if filter else None

    async def generate():
        async for event in sse_hub.subscribe(filter_types):
            yield event

    return EventSourceResponse(generate())
