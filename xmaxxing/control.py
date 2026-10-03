from __future__ import annotations

import threading
from datetime import datetime, timedelta


class RunControl:
    """Wall-clock budget plus an external stop signal.

    Every long-running loop takes one of these and polls `should_stop()`. Two
    independent reasons to stop, deliberately kept separate:

    - the time budget (`minutes`), which is how `run --minutes N` has always worked
    - an operator pressing stop in the dashboard, which must interrupt a run that
      still has time left

    Neither blocks, so a poll is cheap enough to sit in the innermost scraping
    loop. Stop latency is bounded by whatever sleep the loop is currently in,
    not by this class.
    """

    def __init__(self, minutes: float | None = None, stop_event: threading.Event | None = None) -> None:
        self.started = datetime.now()
        self.minutes = float(minutes) if minutes else None
        self.stop_event = stop_event if stop_event is not None else threading.Event()
        self._deadline: datetime | None = None
        if self.minutes is not None:
            self._deadline = self.started + timedelta(minutes=self.minutes)

    @property
    def deadline(self) -> datetime | None:
        return self._deadline

    @property
    def stop_requested(self) -> bool:
        return self.stop_event.is_set()

    def request_stop(self) -> None:
        self.stop_event.set()

    def should_stop(self) -> bool:
        if self.stop_event.is_set():
            return True
        if self._deadline is not None and datetime.now() >= self._deadline:
            return True
        return False

    def elapsed(self) -> float:
        return (datetime.now() - self.started).total_seconds()

    def remaining(self) -> float | None:
        """Seconds left in the budget, or None when the run is unbounded."""
        if self._deadline is None:
            return None
        return max(0.0, (self._deadline - datetime.now()).total_seconds())

    def state(self) -> str:
        if self.stop_event.is_set():
            return "stopping"
        if self._deadline is not None and datetime.now() >= self._deadline:
            return "finished"
        return "running"