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


# --- CLI wiring ----------------------------------------------------------


def test_cmd_run_reaches_the_runner_without_name_errors(monkeypatch) -> None:
    """cmd_run used `runner.RunOptions` while importing only `Runner`.

    Nothing in the suite executed cmd_run, so that NameError shipped and only
    surfaced on the first real `xmaxxing run`. Drive the whole
    parse-args -> build-options -> Runner.run path with a stand-in runner.
    """
    from xmaxxing import cli
    from xmaxxing import runner as runner_module

    captured = {}

    class _FakeRunner:
        def __init__(self, config, paths, bus=None):
            captured["paths"] = paths

        def run(self, options, control, log_path=None):
            captured["options"] = options
            captured["control"] = control
            return runner_module.RunResult(
                counts={key: 0 for key in runner_module.COUNT_KEYS},
                stats={"pages": 1, "queries": 2},
                elapsed_minutes=1.5,
            )

    monkeypatch.setattr(cli, "Runner", _FakeRunner)
    monkeypatch.setattr(cli, "setup_logging", lambda config: None)

    config = config_module.load(EXAMPLE_CONFIG)
    args = cli.build_parser().parse_args(
        ["run", "--minutes", "7", "--dry-run", "--no-resolve", "--reply-links", "3", "--source", "home"]
    )
    assert cli.cmd_run(args, config) == 0

    options = captured["options"]
    assert options.minutes == 7
    assert options.source == "home"
    assert options.dry_run is True
    assert options.no_resolve is True
    assert options.reply_links == 3
    # The time budget must survive the hand-off, or the run would never stop.
    assert captured["control"].remaining() is not None
    assert captured["control"].remaining() <= 7 * 60


def test_cmd_run_uses_the_config_default_minutes(monkeypatch) -> None:
    from xmaxxing import cli
    from xmaxxing import runner as runner_module

    captured = {}

    class _FakeRunner:
        def __init__(self, *a, **k):
            pass

        def run(self, options, control, log_path=None):
            captured["options"] = options
            return runner_module.RunResult(counts={}, stats={}, elapsed_minutes=0.1)

    monkeypatch.setattr(cli, "Runner", _FakeRunner)
    monkeypatch.setattr(cli, "setup_logging", lambda config: None)

    config = config_module.load(EXAMPLE_CONFIG)
    args = cli.build_parser().parse_args(["run"])
    assert cli.cmd_run(args, config) == 0
    assert captured["options"].minutes == float(config.get("run", "minutes", 30))


# --- stop vs. budget expiry ----------------------------------------------


def test_running_out_of_time_is_not_the_same_as_being_stopped() -> None:
    """Post-processing is gated on stop_requested, not should_stop().

    should_stop() is also true once the --minutes budget elapses. If the
    post-processing gate used it, a run that used its full budget would skip the
    reply-link harvest, the action queue and the drafts - i.e. everything the
    scrape was for - and only save seen.json. That is not a distinction you can
    eyeball, so pin it.
    """
    expired = control.RunControl(minutes=0.001)
    time.sleep(0.12)
    assert expired.should_stop() is True, "budget should be exhausted"
    assert expired.stop_requested is False, "nobody pressed stop"

    pressed = control.RunControl(minutes=None)
    pressed.request_stop()
    assert pressed.stop_requested is True


def test_stop_requested_stays_true_after_the_budget_expires() -> None:
    both = control.RunControl(minutes=0.001)
    time.sleep(0.12)
    both.request_stop()
    assert both.should_stop() is True
    assert both.stop_requested is True