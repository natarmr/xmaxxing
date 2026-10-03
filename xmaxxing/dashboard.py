from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import outreach, sender, storage
from .control import RunControl
from .events import EventBus
from .runner import RunOptions, Runner

MAX_SNAPSHOT_ROWS = 400


@dataclass
class RunState:
    status: str = "idle"          # idle | running | stopping | error
    phase: str = ""
    counts: dict[str, int] = field(default_factory=dict)
    message: str = ""
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    send: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "phase": self.phase,
            "counts": dict(self.counts),
            "message": self.message,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "send": dict(self.send),
        }


class DashboardState:
    """Owns the one run that may be in flight, plus a view of the output files.

    One run at a time is enforced here rather than in the browser: Playwright's
    persistent context locks the profile directory, so a second concurrent launch
    would fail in a confusing way. The Start button is disabled too, but the
    server cannot trust the page.
    """

    def __init__(self, config, paths: dict[str, Path]) -> None:
        self.config = config
        self.paths = paths
        self.bus = EventBus()
        self.token = secrets.token_urlsafe(32)
        self.run = RunState()
        self._lock = threading.Lock()
        self._control: RunControl | None = None
        self._thread: threading.Thread | None = None
        self._cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}

    # -- run control -----------------------------------------------------

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, overrides: dict[str, Any] | None = None) -> tuple[bool, str]:
        with self._lock:
            if self.busy:
                return False, "a run is already in flight"
            overrides = overrides or {}
            minutes = overrides.get("minutes")
            options = RunOptions(
                source=str(overrides.get("source") or self.config.get("run", "source", "search")),
                minutes=float(minutes) if minutes else None,
                dry_run=bool(overrides.get("dry_run", False)),
                no_resolve=bool(overrides.get("no_resolve", False)),
                reply_links=int(overrides.get("reply_links") or 0),
                seed_legacy=True,
                profile=overrides.get("profile"),
                auth=overrides.get("auth"),
            )
            if options.minutes is None:
                options.minutes = float(self.config.get("run", "minutes", 30) or 30)
            control = RunControl(minutes=options.minutes)
            self._control = control
            self.run = RunState(
                status="running",
                phase="starting",
                started_at=_stamp(),
                message=f"running for up to {options.minutes:g} min",
            )
            self.bus.publish({"type": "state", **self.run.as_dict()})
            thread = threading.Thread(
                target=self._work, args=(options, control), name="xmaxxing-run", daemon=True
            )
            self._thread = thread
            thread.start()
            return True, "started"

    def stop(self) -> tuple[bool, str]:
        control = self._control
        if control is None or not self.busy:
            return False, "nothing is running"
        control.request_stop()
        self.run.status = "stopping"
        self.run.message = "stopping - finishing the current step"
        self.bus.publish({"type": "state", **self.run.as_dict()})
        return True, "stop requested"

    def _work(self, options: RunOptions, control: RunControl) -> None:
        runner = Runner(self.config, self.paths, bus=self.bus)
        try:
            result = runner.run(options, control)
            self.run.counts = dict(result.counts)
            self.run.send = dict(result.send)
            self.run.finished_at = _stamp()
            if result.error:
                self.run.status = "error"
                self.run.error = result.error
            else:
                self.run.status = "idle"
                self.run.phase = "done"
                self.run.message = (
                    "stopped early on request" if result.stopped else "run complete"
                )
        except Exception as error:  # keep the dashboard alive whatever happens
            self.run.status = "error"
            self.run.error = f"{type(error).__name__}: {error}"
            self.run.finished_at = _stamp()
            self.bus.publish({"type": "error", "error": self.run.error})
        finally:
            self._invalidate()
            self.bus.publish({"type": "state", **self.run.as_dict()})
            self.bus.publish({"type": "done", "run": self.run.as_dict()})

    def _on_event(self, event: dict[str, Any]) -> None:
        if event.get("type") == "state":
            self.run.phase = str(event.get("phase") or self.run.phase)
        elif event.get("type") == "progress":
            self.run.counts = dict(event.get("counts") or {})

    def subscribe(self):
        channel = self.bus.subscribe()
        # Bring a new tab up to date before streaming.
        channel.put_nowait({"type": "state", **self.run.as_dict()})
        return channel

    def unsubscribe(self, channel) -> None:
        self.bus.unsubscribe(channel)

    # -- output files ----------------------------------------------------

    def _invalidate(self) -> None:
        with self._lock:
            self._cache.clear()

    def rows(self, name: str, limit: int = MAX_SNAPSHOT_ROWS) -> list[dict[str, Any]]:
        path = self.paths.get(name)
        if path is None or not Path(path).exists():
            return []
        try:
            stamp = Path(path).stat().st_mtime
        except OSError:
            return []
        cached = self._cache.get(name)
        if cached and cached[0] == stamp:
            return cached[1][-limit:]
        rows = list(storage.read_jsonl(path))
        with self._lock:
            self._cache[name] = (stamp, rows)
        return rows[-limit:]

    def policy(self) -> dict[str, Any]:
        resolved = outreach.resolved_count(self.paths["actions"])
        policy = sender.SendPolicy.from_config(self.config)
        allowed, reason = policy.allows("reply", resolved_before=resolved, sent_this_run=0)
        return {
            "mode": policy.mode,
            "auto_after": policy.auto_after,
            "max_sends": policy.max_sends,
            "resolved_before": resolved,
            "pending": outreach.pending_count(self.paths["actions"]),
            "would_send": allowed,
            "reason": reason,
        }

    def snapshot(self) -> dict[str, Any]:
        # Keys must match `cli._paths` exactly, not the config's file names.
        return {
            "run": self.run.as_dict(),
            "busy": self.busy,
            "policy": self.policy(),
            "jobs": self.rows("jobs_jsonl"),
            "hackathons": self.rows("hack_jsonl"),
            "actions": self.rows("actions"),
            "rejected": self.rows("rejected", limit=200),
            "reply_links": self.rows("reply_links", limit=200),
        }

    def resolve(self, key: str, status: str, note: str = "", sent_url: str = "") -> tuple[bool, str]:
        if status not in outreach.STATUSES:
            return False, f"unknown status {status!r}"
        ok = outreach.resolve_action(key, status, self.paths["actions"], note=note, sent_url=sent_url)
        self._invalidate()
        if ok:
            self.bus.publish({"type": "resolved", "key": key, "status": status})
        return ok, "resolved" if ok else "no matching pending action"


def _stamp() -> str:
    from datetime import datetime

    return datetime.now().strftime("%H:%M:%S")