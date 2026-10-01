from __future__ import annotations

import re
from typing import Any

from .extract import canonical_status_url, clean_text, dedupe_links, split_handle, status_key


class Article(dict):
    pass


def _text_of(locator) -> str:
    try:
        return locator.first.inner_text(timeout=3000)
    except Exception:
        return ""


def _attr(locator, name: str) -> str | None:
    try:
        return locator.first.get_attribute(name, timeout=3000)
    except Exception:
        return None


def expand_truncated(page, selectors: dict[str, str], limit: int = 6) -> int:
    show_more = page.locator(selectors.get("show_more_text", '[data-testid="tweet-text-show-more-link"]'))
    expanded = 0
    try:
        count = min(show_more.count(), limit)
    except Exception:
        return 0
    for index in range(count):
        node = show_more.nth(index)
        try:
            if not node.is_visible():
                continue
            node.scroll_into_view_if_needed(timeout=2000)
            node.click(timeout=2000, force=True)
            expanded += 1
        except Exception:
            continue
    return expanded


def is_promoted(article, selectors: dict[str, str]) -> bool:
    for selector in (selectors.get("promoted"), 'a[href*="/i/ads_"]', '[aria-label*="Sponsored" i]', '[aria-label*="Promoted" i]'):
        if not selector:
            continue
        try:
            if article.locator(selector).count() > 0:
                return True
        except Exception:
            continue
    return False


def _status_href(article) -> str | None:
    candidates: list[str] = []
    try:
        anchors = article.locator('a[href*="/status/"]')
        for index in range(anchors.count()):
            href = anchors.nth(index).get_attribute("href")
            if not href:
                continue
            if anchors.nth(index).locator("time").count() > 0:
                return href
            candidates.append(href)
    except Exception:
        return candidates[0] if candidates else None
    for href in candidates:
        if not re.search(r"/status/\d+/(?:photo|video|analytics)", href):
            return href
    return candidates[0] if candidates else None


def _relative_time(article) -> str:
    try:
        node = article.locator("time").first
        raw = _text_of(node)
        return raw.strip()
    except Exception:
        return ""


def _datetime(article) -> str | None:
    try:
        return _attr(article.locator("time"), "datetime")
    except Exception:
        return None


def _links(article, selectors: dict[str, str]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    text_selector = selectors.get("tweet_text")
    if text_selector:
        try:
            holder = article.locator(text_selector).first
            for index in range(holder.locator("a").count()):
                anchor = holder.locator("a").nth(index)
                href = _attr(anchor, "href")
                if not href:
                    continue
                label = _text_of(anchor)
                pairs.append((href, label))
        except Exception:
            pass
    card_selector = selectors.get("card_wrapper")
    if card_selector:
        try:
            cards = article.locator(card_selector)
            for card_index in range(cards.count()):
                card = cards.nth(card_index)
                for index in range(card.locator("a").count()):
                    anchor = card.locator("a").nth(index)
                    href = _attr(anchor, "href")
                    if not href:
                        continue
                    pairs.append((href, _text_of(anchor)))
        except Exception:
            pass
    return dedupe_links(pairs)


def harvest(article, selectors: dict[str, str], expand: bool = True) -> Article | None:
    try:
        href = _status_href(article)
        key = status_key(href)
        if not key:
            return None
        record = Article()
        record["key"] = key
        record["tweet_url"] = canonical_status_url(href)
        record["relative_time"] = _relative_time(article)
        record["posted_at"] = _datetime(article)
        display = ""
        name_selector = selectors.get("user_name")
        if name_selector:
            try:
                if article.locator(name_selector).count() > 0:
                    display = _text_of(article.locator(name_selector))
            except Exception:
                display = ""
        name, _declared_handle = split_handle(display or "")
        author = key.split("/")[0]
        record["display_name"] = name or author
        record["handle"] = author
        if expand:
            try:
                show_more = article.locator(selectors.get("show_more_text", '[data-testid="tweet-text-show-more-link"]'))
                if show_more.count() > 0:
                    show_more.first.click(timeout=2500, force=True)
            except Exception:
                pass
        text_selector = selectors.get("tweet_text")
        raw_text = ""
        if text_selector:
            try:
                if article.locator(text_selector).count() > 0:
                    raw_text = _text_of(article.locator(text_selector))
            except Exception:
                raw_text = ""
        if not raw_text:
            try:
                raw_text = _text_of(article.locator('[role="group"]'))
            except Exception:
                raw_text = ""
        record["text"] = clean_text(raw_text)
        record["links"] = _links(article, selectors)
        record["promoted"] = is_promoted(article, selectors)
        return record
    except Exception:
        return None


def harvest_all(page, selectors: dict[str, str], expand: bool = True) -> list[Article]:
    out: list[Article] = []
    try:
        articles = page.locator(selectors["tweet"])
        total = articles.count()
    except Exception:
        return out
    for index in range(total):
        try:
            record = harvest(articles.nth(index), selectors, expand=expand)
        except Exception:
            record = None
        if record:
            out.append(record)
    return out