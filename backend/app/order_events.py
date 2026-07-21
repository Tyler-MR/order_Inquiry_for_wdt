from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any


class OrderEventBus:
    """进程内订单更新广播；SSE 客户端只接收轻量更新通知。"""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    async def publish(self, event: dict[str, Any]) -> None:
        for queue in tuple(self._subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            await queue.put(event)

    async def stream(self) -> AsyncIterator[str]:
        queue = self.subscribe()
        try:
            yield "event: connected\ndata: {\"status\":\"connected\"}\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30)
                    event_name = event.get("event", "orders.updated")
                    payload = json.dumps(event, ensure_ascii=False)
                    yield f"event: {event_name}\ndata: {payload}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            self.unsubscribe(queue)
