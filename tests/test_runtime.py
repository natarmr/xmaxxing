from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from xmaxxing import config as config_module, control, events, sources

EXAMPLE_CONFIG = Path(__file__).resolve().parent.parent / "config.example.toml"


# --- RunControl ---------------------------------------------------------


def test_run_control_starts_running_and_finishes_on_request() -> None:
    run = control.RunControl(minutes=30)
    assert run.should_stop() is False
    assert run.state() == "running"
    run.request_stop()
    assert run.should_stop() is True
    assert run.stop_requested is True
    assert run.state() == "stopping"


def test_run_control_expires_on_its_own_budget() -> None:
    run = control.RunControl(minutes=0.001)  # 60ms
    time.sleep(0.15)
    assert run.should_stop() is True
    assert run.remaining() == 0.0


def test_unbounded_run_never_times_out() -> None:
    run = control.RunControl(minutes=None)
    assert run.remaining() is None
    assert run.should_stop() is False


def test_external_stop_event_is_shared() -> None:
    event = threading.Event()
    run = control.RunControl(minutes=None, stop_event=event)
    assert run.should_stop() is False
    event.set()
    assert run.should_stop() is True


# --- EventBus ------------------------------------------------------------


def test_event_bus_delivers_to_every_subscriber() -> None:
    bus = events.EventBus()
    first = bus.subscribe()
    second = bus.subscribe()
    bus.publish({"type": "progress", "kept": 3})
    assert first.get_nowait()["kept"] == 3
    assert second.get_nowait()["kept"] == 3
    assert bus.subscriber_count() == 2


def test_event_bus_drops_oldest_rather_than_growing() -> None:
    bus = events.EventBus(maxsize=3)
    channel = bus.subscribe()
    for index in range(10):
        bus.publish({"type": "progress", "n": index})
    assert channel.qsize() == 3, channel.qsize()
    latest = [channel.get_nowait() for _ in range(3)]
    assert [event["n"] for event in latest] == [7, 8, 9], latest


def test_unsubscribe_stops_delivery() -> None:
    bus = events.EventBus()
    channel = bus.subscribe()
    bus.unsubscribe(channel)
    bus.publish({"type": "progress"})
    assert channel.empty()
    assert bus.subscriber_count() == 0
    bus.unsubscribe(channel)  # idempotent


def test_subscription_context_manager_cleans_up() -> None:
    bus = events.EventBus()
    with bus.subscription() as channel:
        bus.publish({"type": "state", "state": "running"})
        assert not channel.empty()
    assert bus.subscriber_count() == 0


def test_publishing_with_no_subscribers_is_harmless() -> None:
    events.EventBus().publish({"type": "state", "state": "running"})


def test_sse_encoding_is_one_data_line() -> None:
    payload = events.EventBus.format_sse({"type": "action", "key": "a/1"})
    text = payload.decode("utf-8")
    assert text.startswith("data: ") and text.endswith("\n\n")
    assert text.count("\n") == 2, repr(text)


# --- cancellation reaches the scrape loops -------------------------------


class _StubPage:
    """Enough of a Playwright page that iter_search can be driven without a browser."""

    def __init__(self) -> None:
        self.url = "https://x.com/search?q=test"
        self.visited: list[str] = []
        self.mouse = self

    def wait_for_timeout(self, ms: int) -> None:
        time.sleep(min(ms, 1) / 1000.0)

    def reload(self, **kwargs) -> None:
        pass

    def wheel(self, *_args) -> None:
        pass


def test_iter_search_stops_before_opening_a_page_when_cancelled() -> None:
    config = config_module.load(EXAMPLE_CONFIG)
    run = control.RunControl(minutes=None)
    run.request_stop()
    page = _StubPage()
    stats = sources.iter_search(page, config, {"tweet": "article"}, lambda _record: True, control=run)
    assert stats["queries"] == 0, stats
    assert stats["pages"] == 0, stats


def test_iter_home_stops_immediately_when_cancelled() -> None:
    config = config_module.load(EXAMPLE_CONFIG)
    run = control.RunControl(minutes=None)
    run.request_stop()
    stats = sources.iter_home(_StubPage(), config, {"tweet": "article"}, lambda _record: True, control=run)
    assert stats["pages"] == 0, stats


def test_iter_search_without_control_still_runs(monkeypatch) -> None:
    """Back-compat: `control=None` must not mean 'stop immediately'."""
    config = config_module.load(EXAMPLE_CONFIG)
    config.data["queries"]["items"] = [{"name": "stub", "q": "hiring", "max_pages": 1}]
    page = _StubPage()
    monkeypatch.setattr(sources, "open_page", lambda *a, **k: True)
    monkeypatch.setattr(sources, "_harvest_with_retry", lambda *a, **k: [])
    stats = sources.iter_search(page, config, {"tweet": "article"}, lambda _record: True, control=None)
    assert stats["queries"] == 1, stats
    assert stats["pages"] == 1, stats