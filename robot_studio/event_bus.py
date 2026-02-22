"""
Thread-safe event bus for real-time event publishing and subscribing.

Brain components publish events here; the WebSocket endpoint subscribes
and forwards them to connected browser clients.
"""

import threading
import time
from collections import deque
from typing import Callable, Dict, List, Optional


class Event:
    __slots__ = ("type", "data", "timestamp")

    def __init__(self, event_type: str, data: Optional[Dict] = None):
        self.type = event_type
        self.data = data or {}
        self.timestamp = time.time()

    def to_dict(self) -> Dict:
        return {
            "type": self.type,
            "data": self.data,
            "timestamp": self.timestamp,
        }


class EventBus:
    """Thread-safe publish/subscribe with a bounded history ring buffer."""

    def __init__(self, max_history: int = 1000):
        self._lock = threading.Lock()
        self._subscribers: List[Callable] = []
        self._history: deque = deque(maxlen=max_history)

    def publish(self, event_type: str, data: Optional[Dict] = None):
        event = Event(event_type, data)
        with self._lock:
            self._history.append(event)
            subs = list(self._subscribers)
        for callback in subs:
            try:
                callback(event)
            except Exception:
                pass

    def subscribe(self, callback: Callable):
        with self._lock:
            self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable):
        with self._lock:
            try:
                self._subscribers.remove(callback)
            except ValueError:
                pass

    def get_history(self, last_n: int = 100) -> List[Dict]:
        with self._lock:
            items = list(self._history)
        return [e.to_dict() for e in items[-last_n:]]

    def clear(self):
        with self._lock:
            self._history.clear()
