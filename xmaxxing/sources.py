from __future__ import annotations

import time
import urllib.parse
from datetime import datetime, timedelta
from typing import Any, Callable

from .browser import open_page, sleep_jitter
from .dom import harvest_all

RecordSink = Callable[[dict[str, Any]], bool]


def search_url(query: str, live: bool = True) -> str:
    params = {"q": query, "src": "typed_query"}
    if live:
        params["f"] = "live"
    return "https://x.com/search?" + urllib.parse.urlencode(params)


def _click_next(page, selectors: dict[str, str]) -> bool:
    selector = selectors.get("next_button")
    if not selector:
        return False
    try:
        node = page.locator(selector).first
        if node.count() > 0 and node.is_visible(timeout=2000):
            node.click(timeout=3000)
            return True
    except Exception:
        return False
    return False


def _interstitial(page, selectors: dict[str, str]) -> bool:
    selector = selectors.get("error_state")
    if not selector:
        return False
    try:
        return page.locator(selector).count() > 0
    except Exception:
        return False


def _harvest_with_retry(page, selectors: dict[str, str], attempts: int = 3):
    records = harvest_all(page, selectors)
    for attempt in range(1, attempts):
        if records:
            break
        wait = 6000 * attempt
        print(f"  (no articles yet, waiting {wait // 1000}s more...)")
        page.wait_for_timeout(wait)
        records = harvest_all(page, selectors)
    return records


def iter_search(page, config, selectors: dict[str, str], on_record: RecordSink, deadline: datetime | None = None) -> dict[str, int]:
    run = config.section("run")
    queries = config.queries
    stats = {"pages": 0, "scraped": 0, "accepted": 0, "queries": 0}
    for query in queries:
        if deadline and datetime.now() >= deadline:
            break
        stats["queries"] += 1
        label = query.get("name", query.get("q", "")[:40])
        max_pages = int(query.get("max_pages", 3))
        url = search_url(query["q"])
        print(f"\n[query] {label}  pages<={max_pages}")
        if not open_page(page, url, selectors.get("tweet"), timeout_ms=30000):
            page.wait_for_timeout(5000)
        page.wait_for_timeout(3000)
        for page_index in range(max_pages):
            if deadline and datetime.now() >= deadline:
                break
            records = _harvest_with_retry(page, selectors)
            new_items = 0
            for record in records:
                if record.get("promoted"):
                    continue
                stats["scraped"] += 1
                if on_record(record):
                    new_items += 1
                    stats["accepted"] += 1
            stats["pages"] += 1
            print(f"  page {page_index + 1}/{max_pages}: {len(records)} articles, {new_items} kept, url={page.url[-60:]}")
            if _interstitial(page, selectors):
                print("  ! X error state detected, backing off 15s")
                page.wait_for_timeout(15000)
            before_url = page.url
            page.mouse.wheel(0, 6000)
            sleep_jitter(int(run.get("scroll_pause_ms", 2200)), int(run.get("scroll_jitter_ms", 1600)))
            if new_items == 0:
                clicked = _click_next(page, selectors)
                if clicked:
                    print("  -> clicked Next")
                elif page.url == before_url:
                    print("  -> no more results")
                    break
    return stats


def iter_home(page, config, selectors: dict[str, str], on_record: RecordSink, deadline: datetime | None = None) -> dict[str, int]:
    run = config.section("run")
    stats = {"pages": 0, "scraped": 0, "accepted": 0, "queries": 0}
    print("\n[source] home feed")
    if not open_page(page, "https://x.com/home", selectors.get("tweet"), timeout_ms=25000):
        print("  ! home feed did not load")
        return stats
    stall_limit = int(run.get("stall_limit", 6))
    consecutive_empty = 0
    page.wait_for_timeout(3000)
    while True:
        if deadline and datetime.now() >= deadline:
            break
        records = _harvest_with_retry(page, selectors, attempts=1)
        new_items = 0
        for record in records:
            if record.get("promoted"):
                continue
            stats["scraped"] += 1
            if on_record(record):
                new_items += 1
                stats["accepted"] += 1
        stats["pages"] += 1
        if new_items:
            consecutive_empty = 0
        else:
            consecutive_empty += 1
        if stats["pages"] % 10 == 0 or consecutive_empty:
            print(f"  pass {stats['pages']}: {len(records)} articles, {new_items} new, stall={consecutive_empty}")
        if consecutive_empty >= stall_limit:
            if _interstitial(page, selectors):
                print("  ! X error state, reloading feed")
                page.reload(wait_until="domcontentloaded")
                page.wait_for_timeout(8000)
            else:
                page.mouse.wheel(0, 9000)
                page.wait_for_timeout(4000)
            consecutive_empty = 0
        else:
            page.mouse.wheel(0, 3000)
        sleep_jitter(int(run.get("scroll_pause_ms", 2200)), int(run.get("scroll_jitter_ms", 1600)))
    return stats


def deadline_from_minutes(minutes: int) -> datetime:
    return datetime.now() + timedelta(minutes=minutes)