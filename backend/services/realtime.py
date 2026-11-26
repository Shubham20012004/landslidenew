"""In-process publish/subscribe hub for Server-Sent Events.

Publishing is thread-safe (ingestion runs in FastAPI's worker threads); each
subscriber gets a bounded queue. A subscriber that cannot keep up is
disconnected (its browser reconnects and resynchronises) instead of letting
memory grow without bound.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import logging
import threading
from typing import Any

log = logging.getLogger("landslide.realtime")


def sse_format(event: str, data: Any, event_id: int | None = None) -> str:
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    payload = json.dumps(data, separators=(",", ":"), allow_nan=False)
    lines.append(f"data: {payload}")
    return "\n".join(lines) + "\n\n"


class Hub:
    QUEUE_SIZE = 256

    def __init__(self) -> None:
        self._subs: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ids = itertools.count(1)
        self._id_lock = threading.Lock()
        self.last_id = 0

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self.QUEUE_SIZE)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def is_subscribed(self, q: asyncio.Queue) -> bool:
        return q in self._subs

    def publish(self, event: str, data: Any) -> None:
        with self._id_lock:
            event_id = next(self._ids)
            self.last_id = event_id
        message = sse_format(event, data, event_id)
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._fanout(message)
        else:
            loop.call_soon_threadsafe(self._fanout, message)

    def _fanout(self, message: str) -> None:
        for q in list(self._subs):
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                log.warning("dropping slow SSE subscriber")
                self._subs.discard(q)  # the stream notices and closes; the browser reconnects
