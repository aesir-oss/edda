"""Server-sent events: what changed in the vault, pushed to every open shell."""

import asyncio
import threading
from collections.abc import AsyncIterator
from typing import Any

from fastapi.sse import ServerSentEvent

KEEPALIVE_SECONDS = 15.0

_CLOSED = object()


class Events:
    """Fans events out to subscribers. ``publish`` is safe from any thread; subscribers read on their event loop."""

    def __init__(self) -> None:
        """Starts with no subscribers."""
        self._lock = threading.Lock()
        self._subscribers: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue[object]]] = []
        self._closed = False

    def subscribe(self) -> asyncio.Queue[object]:
        """A queue on the running event loop that receives every event published from now on."""
        queue: asyncio.Queue[object] = asyncio.Queue()
        with self._lock:
            if self._closed:
                queue.put_nowait(_CLOSED)
            else:
                self._subscribers.append((asyncio.get_running_loop(), queue))
        return queue

    def unsubscribe(self, queue: asyncio.Queue[object]) -> None:
        """Stops sending events to ``queue``."""
        with self._lock:
            self._subscribers = [(loop, q) for loop, q in self._subscribers if q is not queue]

    def publish(self, event: str, data: dict[str, Any]) -> None:
        """Sends ``event`` with ``data`` to every subscriber."""
        self._send(ServerSentEvent(event=event, data=data))

    def close(self) -> None:
        """Ends every open stream, and any opened later."""
        with self._lock:
            self._closed = True
        self._send(_CLOSED)

    def _send(self, item: object) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for loop, queue in subscribers:
            if not loop.is_closed():
                loop.call_soon_threadsafe(queue.put_nowait, item)


async def stream(events: Events, keepalive: float = KEEPALIVE_SECONDS) -> AsyncIterator[ServerSentEvent]:
    """A ``ready`` event, then every published event, with a comment line every ``keepalive`` seconds of quiet."""
    queue = events.subscribe()
    try:
        yield ServerSentEvent(event="ready", data={})
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), keepalive)
            except TimeoutError:
                yield ServerSentEvent(comment="keepalive")
                continue
            if item is _CLOSED:
                return
            assert isinstance(item, ServerSentEvent)
            yield item
    finally:
        events.unsubscribe(queue)
