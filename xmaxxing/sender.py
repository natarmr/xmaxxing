from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

from .control import RunControl

# The only action type that may ever be sent automatically. DMs need a different
# composer and a different navigation path, so they stay human-gated until that
# exists - see docs in [outreach].
AUTO_SENDABLE = ("reply",)

MODES = ("review", "auto_after", "auto")

# Fallbacks for the inline send button. X renames test ids periodically and a
# stale one fails silently as "not found", so try the plausible variants in
# order rather than trusting a single selector. Config still wins when it sets
# `send_reply_button` - these are only the defaults.
SEND_REPLY_SELECTORS = (
    '[data-testid="tweetButtonInline"]',
    '[data-testid="tweetButton"]',
    'div[data-testid="tweetButtonInline"]',
)
TOAST_SELECTORS = (
    '[data-testid="toast"]',
    '[role="alert"]',
)


@dataclass(frozen=True)
class SendOutcome:
    status: str          # sent | failed | attempted | skipped
    verified: bool       # did we actually see it land?
    detail: str = ""

    @property
    def sent(self) -> bool:
        return self.status == "sent" and self.verified


@dataclass(frozen=True)
class SendPolicy:
    """When auto-send is allowed. Pure data, no I/O, so it is easy to test.

    `resolved_before` and `sent_this_run` are passed in rather than tracked here
    so the policy cannot disagree with the ledger about what has happened.
    """

    mode: str = "review"
    auto_after: int = 3
    max_sends: int = 25
    dry_run: bool = False

    @classmethod
    def from_config(cls, config, *, dry_run: bool = False, resolved_before: int = 0) -> "SendPolicy":
        mode = str(config.get("outreach", "mode", "review") or "review").strip().lower()
        if mode not in MODES:
            mode = "review"
        return cls(
            mode=mode,
            auto_after=int(config.get("outreach", "auto_after", 3) or 0),
            max_sends=int(config.get("outreach", "auto_after_sends", 25) or 0),
            dry_run=bool(dry_run),
        )

    def allows(self, action_type: str, *, resolved_before: int = 0, sent_this_run: int = 0) -> tuple[bool, str]:
        """Return (allowed, reason). The reason is what the UI shows the operator."""
        if self.dry_run:
            return False, "dry_run"
        if action_type not in AUTO_SENDABLE:
            return False, f"{action_type}_needs_review"
        if self.mode == "review":
            return False, "review_mode"
        if self.max_sends <= 0:
            return False, "send_cap_zero"
        if sent_this_run >= self.max_sends:
            return False, "send_cap_reached"
        if self.mode == "auto_after" and resolved_before < self.auto_after:
            return False, f"reviewing_first_{self.auto_after}"
        return True, "auto"


NORMALISE = re.compile(r"\s+")


def _normalise(text: str) -> str:
    return NORMALISE.sub(" ", text or "").strip().lower()


def draft_fingerprint(draft: str, length: int = 40) -> str:
    """A stable prefix used to recognise our own reply in the thread.

    Matching on the opening words only: the tail mutates as X re-wraps and
    truncates text, so a full-text comparison produces false "failed" verdicts.
    """
    flat = _normalise(draft)
    return flat[:length]


def _article_texts(page, tweet_selector: str) -> list[str]:
    try:
        articles = page.locator(tweet_selector)
        count = articles.count()
    except Exception:
        return []
    texts: list[str] = []
    for index in range(min(count, 40)):
        try:
            texts.append(articles.nth(index).inner_text(timeout=2000))
        except Exception:
            continue
    return texts


def _toast_text(page, toast_selector: str) -> str:
    selectors = (toast_selector,) + TOAST_SELECTORS if toast_selector else TOAST_SELECTORS
    node = _first_visible(page, selectors)
    if node is None:
        return ""
    try:
        return node.inner_text(timeout=2000).strip()
    except Exception:
        return ""


def _first_visible(page, selectors: tuple[str, ...]):
    """Return the first locator that matches something, or None."""
    for selector in selectors:
        try:
            node = page.locator(selector)
            if node.count() > 0:
                return node.first
        except Exception:
            continue
    return None


def send_reply(page, action: dict[str, Any], config, selectors: dict[str, str], *,
               control: RunControl | None = None) -> SendOutcome:
    """Post one queued reply, then prove it landed.

    Returns `sent` only when the reply was observed in the thread. If it can't
    be confirmed the result is `attempted`, never `sent` - the ledger has to be
    able to tell the truth about what went out from the account.

    The control check between typing and pressing is the kill switch: pressing
    Stop leaves the draft typed but unsent.
    """
    tweet_url = action.get("tweet_url")
    draft = action.get("draft") or ""
    if not tweet_url:
        return SendOutcome("failed", False, "no tweet_url")
    fingerprint = draft_fingerprint(draft)
    if not fingerprint:
        return SendOutcome("failed", False, "empty draft")

    from .browser import open_page

    tweet_selector = selectors.get("tweet") or 'article[data-testid="tweet"]'
    configured = selectors.get("send_reply_button")
    send_selectors = (configured,) + SEND_REPLY_SELECTORS if configured else SEND_REPLY_SELECTORS
    toast_selector = selectors.get("toast") or ""

    if not open_page(page, tweet_url, tweet_selector, timeout_ms=25000):
        return SendOutcome("failed", False, "could not open post")

    before = _toast_text(page, toast_selector)
    if before:
        return SendOutcome("failed", False, f"x already showing an error: {before}")

    from .outreach import prefill_reply

    if not prefill_reply(page, selectors, draft):
        return SendOutcome("failed", False, "composer did not accept the draft")

    # --- kill switch: between composing and committing ---
    if control is not None and control.should_stop():
        return SendOutcome("skipped", False, "stopped before send")

    button = _first_visible(page, send_selectors)
    if button is None:
        return SendOutcome("failed", False, f"send button not found (tried {len(send_selectors)} selectors)")
    try:
        button.click(timeout=8000)
    except Exception as error:
        return SendOutcome("failed", False, f"send click failed: {error}")

    # --- verify ---
    limit = float(config.get("outreach", "send_verify_seconds", 12) or 12)
    deadline = time.monotonic() + max(1.0, limit)
    while time.monotonic() < deadline:
        if control is not None and control.should_stop():
            return SendOutcome("attempted", False, "stopped while verifying")
        if any(fingerprint in _normalise(text) for text in _article_texts(page, tweet_selector)):
            return SendOutcome("sent", True, "reply found in thread")
        toast = _toast_text(page, toast_selector)
        if toast:
            return SendOutcome("failed", False, f"x reported: {toast}")
        try:
            page.wait_for_timeout(1000)
        except Exception:
            break
    return SendOutcome("attempted", False, "posted but not confirmed in thread")