"""In-process async event bus + WebSocket connection manager.

Every part of the backend pushes :class:`ProgressEvent` objects through
``bus.publish(...)``; the WebSocket router fans them out to all connected
clients. Also keeps a small ring buffer so a freshly connected client can
catch up on the most recent events.
"""
from __future__ import annotations

import asyncio
from collections import deque
from typing import Any, Deque

from .models import ProgressEvent


class EventBus:
    def __init__(self, history: int = 200):
        self._subscribers: set[asyncio.Queue] = set()
        self._history: Deque[ProgressEvent] = deque(maxlen=history)
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def subscriber_count(self) -> int:
        """How many WS clients are listening — lets periodic producers (stats
        broadcaster) skip work entirely when nobody is watching."""
        return len(self._subscribers)

    def recent(self) -> list[ProgressEvent]:
        return list(self._history)

    async def publish(self, event: ProgressEvent) -> None:
        self._history.append(event)
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # drop oldest for slow clients
                try:
                    q.get_nowait()
                    q.put_nowait(event)
                except Exception:
                    pass

    def publish_threadsafe(self, event: ProgressEvent) -> None:
        """Publish from a worker thread (provider running in executor)."""
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self.publish(event), self._loop)
        else:  # no loop yet — just record history
            self._history.append(event)


bus = EventBus()
