from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from .domain import Event


class EventSource(Protocol):
    def fetch(self) -> Iterable[Event]:
        ...


class StaticEventSource:
    """Test and replay source; production adapters can wrap RSS/API clients."""

    def __init__(self, events: Iterable[Event]) -> None:
        self._events = tuple(events)

    def fetch(self) -> Iterable[Event]:
        return self._events
