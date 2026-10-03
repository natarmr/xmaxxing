from __future__ import annotations

import json
import queue
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


class EventBus:
    """Fan-out of run progress to any number of subscribers.

    Used by the dashboard: a scraping worker publishes, an SSE request reads.
    Publishing must never block the worker and must never raise because nobody
    is listening, so `publish` is best-effort per subscriber.

    Subscriber queues are bounded and drop their oldest event when full. A slow
    or stalled browser tab therefore costs a bounded amount of memory instead of
    growing without limit, and the UI still gets the newest state - which is the
    part that matters for a live view.
    """

    def __init__(self, maxsize: int = 256) -> None:
        self._maxsize = maxsize
        self._subscribers: list[queue.Queue] = []
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        channel: queue.Queue = queue.Queue(maxsize=self._maxsize)
        with self._lock:
            self._subscribers.append(channel)
        return channel

    def unsubscribe(self, channel: queue.Queue) -> None:
        with self._lock:
            if channel in self._subscribers:
                self._subscribers.remove(channel)

    @contextmanager
    def subscription(self) -> Iterator[queue.Queue]:
        channel = self.subscribe()
        try:
            yield channel
        finally:
            self.unsubscribe(channel)

    def publish(self, event: dict[str, Any]) -> None:
        with self._lock:
            targets = list(self._subscribers)
        for channel in targets:
            try:
                channel.put_nowait(event)
            except queue.Full:
                # Drop the oldest so the freshest state still lands.
                try:
                    channel.get_nowait()
                    channel.put_nowait(event)
                except (queue.Empty, queue.Full):
                    pass

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    @staticmethod
    def format_sse(event: dict[str, Any]) -> bytes:
        """Encode one event for a Server-Sent Events stream."""
        return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n".encode("utf-8")