"""In-process SSE broadcaster for LaCite's live session.

MCP calls run in FastAPI's threadpool; SSE subscribers are async generators on
the server event loop. ``publish`` bridges the two with
``loop.call_soon_threadsafe``. Its own copy of the pattern the other Add-ons
use (P3: a Bundle never imports from another one), plus ``close()``: on
``deactivate`` every open stream ends instead of waiting forever on a
broadcaster nobody publishes to any more.
"""

from __future__ import annotations

import asyncio
from typing import Optional

_CLOSED = object()


class EventBroadcaster:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._closed = False

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Remember the server loop so a worker thread can hand events to it."""
        self._loop = loop

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    async def subscribe(self):
        """Async generator of events for one SSE connection; ends on ``close()``
        (at once when the broadcaster is already closed)."""
        if self._closed:
            return
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(queue)
        try:
            while True:
                event = await queue.get()
                if event is _CLOSED:
                    return
                yield event
        finally:
            self._subscribers.discard(queue)

    def publish(self, event: object) -> None:
        """Fan out an event. Safe to call from any thread; never raises."""
        loop = self._loop
        for queue in list(self._subscribers):
            try:
                if loop is not None and loop.is_running():
                    loop.call_soon_threadsafe(queue.put_nowait, event)
                else:
                    queue.put_nowait(event)
            except Exception:  # a closed loop or a full queue: drop, never fail the caller
                pass

    def close(self) -> None:
        """End every open stream (``deactivate``) and every later one."""
        self._closed = True
        self.publish(_CLOSED)
        self._loop = None
