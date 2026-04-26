from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import queue
from threading import RLock
from typing import Any


@dataclass(frozen=True)
class Event:
    id: int
    type: str
    payload: dict[str, Any]
    created_at: str

    def to_sse(self) -> str:
        body = {
            "id": self.id,
            "type": self.type,
            "payload": self.payload,
            "createdAt": self.created_at,
        }
        return f"id: {self.id}\nevent: {self.type}\ndata: {json.dumps(body, default=str)}\n\n"


class EventBus:
    def __init__(self) -> None:
        self._lock = RLock()
        self._next_id = 1
        self._subscribers: set[queue.Queue[Event]] = set()

    def publish(self, event_type: str, payload: dict[str, Any] | None = None) -> Event:
        with self._lock:
            event = Event(
                id=self._next_id,
                type=event_type,
                payload=payload or {},
                created_at=datetime.now(UTC).isoformat(),
            )
            self._next_id += 1
            subscribers = tuple(self._subscribers)

        for subscriber in subscribers:
            try:
                subscriber.put_nowait(event)
            except queue.Full:
                try:
                    subscriber.get_nowait()
                except queue.Empty:
                    pass
                subscriber.put_nowait(event)
        return event

    def subscribe(self) -> queue.Queue[Event]:
        subscriber: queue.Queue[Event] = queue.Queue(maxsize=100)
        with self._lock:
            self._subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: queue.Queue[Event]) -> None:
        with self._lock:
            self._subscribers.discard(subscriber)
