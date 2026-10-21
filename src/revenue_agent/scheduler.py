from __future__ import annotations

from dataclasses import dataclass
from threading import Event, Thread
from traceback import print_exc
from typing import Callable


@dataclass(frozen=True)
class ScheduleConfig:
    interval_seconds: float = 1800.0

    def __post_init__(self) -> None:
        if self.interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")


class AgentScheduler:
    """Small stoppable scheduler; the callback owns one complete agent cycle."""

    def __init__(self, callback: Callable[[], object], config: ScheduleConfig = ScheduleConfig()) -> None:
        self.callback = callback
        self.config = config
        self._stop = Event()
        self._thread: Thread | None = None

    def run_once(self) -> object:
        return self.callback()

    def start(self, *, run_immediately: bool = True) -> None:
        if self._thread and self._thread.is_alive():
            raise RuntimeError("scheduler is already running")
        self._stop.clear()
        self._thread = Thread(target=self._run, args=(run_immediately,), name="revenue-agent-scheduler", daemon=True)
        self._thread.start()

    def stop(self, timeout: float | None = None) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)

    def _run(self, run_immediately: bool) -> None:
        if run_immediately and not self._stop.is_set():
            self._safe_callback()
        # The rest interval starts after a cycle completes, so a slow callback
        # can never accumulate catch-up debt or collapse into back-to-back runs.
        while not self._stop.wait(self.config.interval_seconds):
            self._safe_callback()

    def _safe_callback(self) -> None:
        try:
            self.callback()
        except Exception:
            print_exc()
