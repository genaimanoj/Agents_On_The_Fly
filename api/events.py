"""Event bus for real-time SSE streaming between orchestrator and UI."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class AgentEvent:
    event_type: str
    data: dict[str, Any]
    research_id: str = ""
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_sse(self) -> str:
        payload = {**self.data, "_ts": self.timestamp}
        return f"event: {self.event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


class EventBus:
    """Pub/sub for research events. Each research_id has its own set of subscribers."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue]] = {}

    def subscribe(self, research_id: str) -> asyncio.Queue:
        q: asyncio.Queue[AgentEvent] = asyncio.Queue(maxsize=500)
        self._subscribers.setdefault(research_id, []).append(q)
        return q

    def unsubscribe(self, research_id: str, q: asyncio.Queue) -> None:
        subs = self._subscribers.get(research_id, [])
        if q in subs:
            subs.remove(q)
        if not subs:
            self._subscribers.pop(research_id, None)

    async def publish(self, event: AgentEvent) -> None:
        for q in self._subscribers.get(event.research_id, []):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass  # drop oldest if full

    async def emit(
        self,
        research_id: str,
        event_type: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        await self.publish(
            AgentEvent(
                event_type=event_type,
                data=data or {},
                research_id=research_id,
            )
        )
