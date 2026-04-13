import asyncio
import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger("projecthub.sse")


class SSEHub:
    """Central event distribution for SSE clients."""

    def __init__(self):
        self._subscribers: list[asyncio.Queue] = []

    async def emit(self, event_type: str, data: dict | list):
        event = {
            "type": event_type,
            "data": data,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        dead = []
        for queue in self._subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                dead.append(queue)
        for q in dead:
            self._subscribers.remove(q)

    async def subscribe(self, filter_types: set[str] | None = None):
        """Async generator yielding SSE-formatted strings."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subscribers.append(queue)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    if filter_types and event["type"] not in filter_types:
                        continue
                    yield f"event: {event['type']}\ndata: {json.dumps(event['data'])}\n\n"
                except asyncio.TimeoutError:
                    yield f"event: keepalive\ndata: {{}}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            if queue in self._subscribers:
                self._subscribers.remove(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


# Singleton
sse_hub = SSEHub()
