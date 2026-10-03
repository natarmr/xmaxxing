from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from xmaxxing import config as config_module, control, sender

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = config_module.load(ROOT / "config.example.toml")


class _Cfg:
    """Minimal stand-in exposing the two lookups sender/send_reply make."""

    def __init__(self, verify_seconds: int = 1) -> None:
        self._verify = verify_seconds

    def get(self, section: str, key: str, default=None):
        if section == "outreach" and key == "send_verify_seconds":
            return self._verify
        return default


# --- policy --------------------------------------------------------------


def test_default_mode_never_auto_sends() -> None:
    policy = sender.SendPolicy.from_config(EXAMPLE)
    assert policy.mode == "review"
    allowed, reason = policy.allows("reply", resolved_before=99, sent_this_run=0)
    assert allowed is False
    assert reason == "review_mode", reason


def test_auto_after_needs_the_reviewed_count_first() -> None:
    policy = sender.SendPolicy(mode="auto_after", auto_after=3, max_sends=25)
    allowed, reason = policy.allows("reply", resolved_before=2, sent_this_run=0)
    assert allowed is False and reason == "reviewing_first_3", reason
    allowed, reason = policy.allows("reply", resolved_before=3, sent_this_run=0)
    assert allowed is True and reason == "auto", reason


def test_auto_mode_sends_from_the_first_item() -> None:
    policy = sender.SendPolicy(mode="auto", max_sends=25)
    assert policy.allows("reply", resolved_before=0, sent_this_run=0)[0] is True


def test_dms_and_follows_are_never_auto_sent() -> None:
    for mode in sender.MODES:
        policy = sender.SendPolicy(mode=mode, auto_after=0, max_sends=25)
        for action in ("dm", "follow", "apply", "none"):
            allowed, reason = policy.allows(action, resolved_before=99, sent_this_run=0)
            assert allowed is False, f"{mode}/{action} was allowed to auto-send"
            assert reason.endswith("_needs_review"), reason


def test_dry_run_never_sends_whatever_the_mode() -> None:
    policy = sender.SendPolicy(mode="auto", max_sends=25, dry_run=True)
    allowed, reason = policy.allows("reply", resolved_before=99, sent_this_run=0)
    assert allowed is False and reason == "dry_run", reason


def test_the_send_cap_is_enforced() -> None:
    policy = sender.SendPolicy(mode="auto", max_sends=2)
    assert policy.allows("reply", sent_this_run=1)[0] is True
    allowed, reason = policy.allows("reply", sent_this_run=2)
    assert allowed is False and reason == "send_cap_reached", reason
    zeroed = sender.SendPolicy(mode="auto", max_sends=0)
    assert zeroed.allows("reply", sent_this_run=0) == (False, "send_cap_zero")


def test_an_unknown_mode_falls_back_to_review() -> None:
    policy = sender.SendPolicy(mode="review", max_sends=25)
    bogus = sender.SendPolicy.from_config(_Cfg(), dry_run=False)
    assert bogus.mode == "review"
    assert policy.allows("reply", resolved_before=99)[0] is False


# --- outcome honesty -----------------------------------------------------


def test_only_a_verified_send_counts_as_sent() -> None:
    assert sender.SendOutcome("sent", True).sent is True
    assert sender.SendOutcome("sent", False).sent is False
    assert sender.SendOutcome("attempted", False).sent is False
    assert sender.SendOutcome("failed", False).sent is False


# --- verification --------------------------------------------------------


def test_draft_fingerprint_survives_rewrapping() -> None:
    draft = "Interested in the AI Engineer opening - I'm a\n\n  ML engineer building\nin Python."
    fingerprint = sender.draft_fingerprint(draft)
    assert "\n" not in fingerprint
    assert fingerprint.startswith("interested in the ai engineer")
    assert fingerprint == sender.draft_fingerprint("Interested   in the AI Engineer opening - I'm a")


def test_fingerprint_matches_across_whitespace_differences() -> None:
    fingerprint = sender.draft_fingerprint("Interested in the role opening")
    haystack = "some other text\nInterested\n  in the role opening\nmore text"
    assert fingerprint in sender._normalise(haystack)


def test_send_reply_refuses_without_a_url_or_draft() -> None:
    outcome = sender.send_reply(object(), {"tweet_url": "", "draft": "x"}, EXAMPLE, {})
    assert outcome.status == "failed" and "tweet_url" in outcome.detail
    outcome = sender.send_reply(object(), {"tweet_url": "https://x.com/a/status/1", "draft": "  "}, EXAMPLE, {})
    assert outcome.status == "failed" and "empty draft" in outcome.detail


# --- selector fallbacks --------------------------------------------------


class _SelectorPage:
    """Resolves selectors against a fixed set, like a page would."""

    def __init__(self, present: set[str]) -> None:
        self.present = present
        self.clicked: list[str] = []

    def locator(self, selector):
        page = self

        class _Node:
            def __init__(self, selector): self._s = selector
            # Playwright's `.first` is a property, not a method.
            @property
            def first(self): return self
            def count(self): return 1 if self._s in page.present else 0
            def click(self, **kwargs): page.clicked.append(self._s)
            def inner_text(self, **kwargs): return ""
            def nth(self, _i): return self
        return _Node(selector)


def test_send_falls_back_when_the_primary_selector_is_missing() -> None:
    """X renames test ids; a stale selector must not silently disable sending."""
    page = _SelectorPage({'[data-testid="tweetButton"]'})
    node = sender._first_visible(page, sender.SEND_REPLY_SELECTORS)
    assert node is not None
    node.click()
    assert page.clicked == ['[data-testid="tweetButton"]'], page.clicked


def test_no_send_button_anywhere_returns_none() -> None:
    assert sender._first_visible(_SelectorPage(set()), sender.SEND_REPLY_SELECTORS) is None


def test_a_configured_selector_is_tried_first() -> None:
    page = _SelectorPage({'[data-testid="tweetButtonInline"]', '[data-testid="tweetButton"]'})
    node = sender._first_visible(page, ('[data-testid="tweetButton"]',) + sender.SEND_REPLY_SELECTORS)
    node.click()
    assert page.clicked == ['[data-testid="tweetButton"]'], page.clicked


def test_toast_detection_has_fallbacks() -> None:
    assert sender._toast_text(_SelectorPage({'[role="alert"]'}), "") == ""
    assert sender._toast_text(_SelectorPage(set()), '[data-testid="toast"]') == ""


# --- kill switch ---------------------------------------------------------


def test_stop_before_press_leaves_the_draft_unsent() -> None:
    """The control check sits between typing and pressing - that's the whole point."""
    events: list[str] = []

    class _StubPage:
        def locator(self, selector):
            events.append(f"locate:{selector}")
            return self

        def first(self):
            return self

        def count(self):
            return 1

        def click(self, **kwargs):
            events.append("click")
            return None

        def inner_text(self, **kwargs):
            return ""

        def wait_for_timeout(self, ms):
            return None

    stop = control.RunControl(minutes=None)
    stop.request_stop()

    from xmaxxing import browser as browser_module
    from xmaxxing import outreach as outreach_module

    real_open = browser_module.open_page
    real_prefill = outreach_module.prefill_reply
    try:
        browser_module.open_page = lambda *a, **k: True
        outreach_module.prefill_reply = lambda page, sel, draft: events.append("prefill") or True
        outcome = sender.send_reply(
            _StubPage(),
            {"tweet_url": "https://x.com/a/status/1", "draft": "hello there friend"},
            _Cfg(),
            {"tweet": "article", "toast": "toast", "send_reply_button": "sendbtn"},
            control=stop,
        )
    finally:
        browser_module.open_page = real_open
        outreach_module.prefill_reply = real_prefill

    assert outcome.status == "skipped", outcome
    assert outcome.sent is False
    assert "prefill" in events, f"the draft should have been composed: {events}"
    assert not any(e.startswith("locate:sendbtn") for e in events), (
        f"the send button must not be touched after a stop: {events}"
    )