from __future__ import annotations

import json
import os
import random
import re
import sys
import time
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any

from .browser import open_page, sleep_jitter
from .dom import harvest_all
from .extract import best_target, looks_like_apply_url, snippet
from .storage import append_jsonl, read_jsonl
from . import style

TEMPLATE_KEYS = ("reply_post", "dm_recruiter", "dm_founder", "follow_up")

REPLY_INVITE = re.compile(r"\b(?:comment|reply|drop)\s+(?:your|with|below|here)|\breply\s+with|\bcomment\s+below", re.IGNORECASE)
DM_INVITE = re.compile(r"\b(?:dm|message)\s+(?:me|us|in\s+dms|the\s+hiring)|in\s+your\s+dms?", re.IGNORECASE)
FOLLOW_HINT = re.compile(r"\bfollow\s+us\b", re.IGNORECASE)


def load_templates(path: Path) -> dict[str, Any]:
    with Path(path).open("rb") as handle:
        return tomllib.load(handle)


def pick_index(count: int, seed: str) -> int:
    """Deterministic index from a string seed.

    Stable across processes and Python runs - `random` is not, so variant choice
    must not use it. The same tweet key always picks the same variant, which
    matters because a redraft should not silently reword something already sent.
    """
    if count <= 0:
        return 0
    return sum(ord(char) for char in seed) % count


def pick_proof(points: list[str], seed: str) -> str:
    if not points:
        return ""
    return points[pick_index(len(points), seed)]


def template_variants(entry: dict[str, Any]) -> list[str]:
    """All renderable bodies for a template entry, `text` first.

    `variants` is an optional list of alternates. Keeping `text` as variant 0
    means the existing single-template config keeps working untouched.
    """
    if not isinstance(entry, dict):
        return []
    bodies: list[str] = []
    head = entry.get("text")
    if isinstance(head, str) and head.strip():
        bodies.append(head)
    for extra in entry.get("variants", []) or []:
        if isinstance(extra, str) and extra.strip() and extra not in bodies:
            bodies.append(extra)
    return bodies


def pick_variant(entry: dict[str, Any], seed: str) -> str:
    bodies = template_variants(entry)
    if not bodies:
        return ""
    return bodies[pick_index(len(bodies), seed)]


def fill(template: str, mapping: dict[str, str]) -> str:
    text = template
    for key, value in mapping.items():
        text = text.replace("{" + key + "}", value or "")
    kept: list[str] = []
    for line in text.splitlines():
        if re.search(r"\{[a-z_]+\}", line):
            continue
        kept.append(line)
    collapsed = "\n".join(kept)
    collapsed = re.sub(r"[ \t]{2,}", " ", collapsed)
    collapsed = re.sub(r"\n{3,}", "\n\n", collapsed)
    return collapsed.strip() + "\n"


def decide_action(record: dict[str, Any], config) -> tuple[str, str]:
    profile = config.profile
    if profile.get("exclude_hackathons_from_outreach", True) and record.get("kind") == "hackathon":
        return "none", "hackathons_excluded"
    text = record.get("text", "")
    candidates = list(record.get("apply_urls") or []) + list(record.get("reply_urls") or [])
    if candidates:
        has_real_link = any(looks_like_apply_url(url) for url in candidates)
        if has_real_link:
            return "apply", "real_apply_link_exists"
    if DM_INVITE.search(text):
        return "dm", "dm_invite"
    if REPLY_INVITE.search(text):
        return "reply", "reply_invite"
    if FOLLOW_HINT.search(text):
        return "follow", "follow_hint"
    return "none", "no_cta"


def build_draft(record: dict[str, Any], action: str, templates: dict[str, Any], config,
                style_profile=None) -> str:
    if action in {"none", "apply", "follow"}:
        return ""
    profile = config.profile
    roles = record.get("roles") or []
    role = roles[0] if roles else "role"
    company = record.get("company") or ""
    lines = record.get("text", "").splitlines()
    requirement = ""
    for line in lines:
        candidate = re.sub(r"^[•\-*▪\s]+", "", line).strip(" .")
        if 8 < len(candidate) < 120 and re.search(r"(?:experience|required|skills|stack|proficient|knowledge|you have|work with)", candidate, re.IGNORECASE):
            requirement = candidate
            break
    key = "dm_recruiter" if action == "dm" else "reply_post"
    entry = templates.get(key) if isinstance(templates.get(key), dict) else {}
    # Seeded on the tweet key so a redraft reproduces the same wording instead
    # of silently changing something you already sent.
    seed = record.get("key", "")
    body = pick_variant(entry, seed)
    author = (record.get("handle") or "").lstrip("@")
    mapping = {
        "role": role,
        "company": company,
        "name": author or record.get("display_name", "").split()[0] if record.get("display_name") else "there",
        "author": f"@{author}" if author else "there",
        "their_requirement": requirement,
        "product_line": requirement or company,
        "proof": pick_proof(profile.get("proof_points", []), seed),
        "portfolio": profile.get("portfolio", ""),
        "headline": profile.get("headline", ""),
    }
    draft = fill(body, mapping)
    if style_profile is not None:
        # Runs last, so tests asserting on drafts must check the post-transform
        # string - a transform here can reintroduce a word the template dropped.
        draft = style.apply(draft, style_profile, seed)
    return draft


def build_actions(records: list[dict[str, Any]], templates: dict[str, Any], config, reply_map: dict[str, list[str]] | None = None,
                  style_profile=None) -> list[dict[str, Any]]:
    reply_map = reply_map or {}
    actions: list[dict[str, Any]] = []
    if style_profile is None:
        style_profile = style.from_config(config)
    for record in records:
        action, reason = decide_action(record, config)
        if action in {"none", "apply"}:
            continue
        reply_urls = reply_map.get(record.get("key", ""), [])
        entry = {
            "key": record.get("key"),
            "tweet_url": record.get("tweet_url"),
            "handle": record.get("handle"),
            "display_name": record.get("display_name"),
            "kind": record.get("kind"),
            "score": record.get("score"),
            "signals": record.get("signals"),
            "company": record.get("company"),
            "roles": record.get("roles"),
            "locations": record.get("locations"),
            "comp": record.get("comp"),
            "apply_urls": record.get("apply_urls"),
            "reply_urls": reply_urls,
            "text": record.get("text"),
            "action": action,
            "action_reason": reason,
            "draft": build_draft(record, action, templates, config, style_profile),
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "status": "pending",
        }
        actions.append(entry)
    return actions


def harvest_reply_links(page, tweet_url: str, config, selectors: dict[str, str], limit: int = 12) -> list[str]:
    run = config.section("run")
    if not open_page(page, tweet_url, selectors.get("tweet"), timeout_ms=25000):
        return []
    page.wait_for_timeout(2000)
    show_more = selectors.get("show_more_replies")
    if show_more:
        for _ in range(3):
            try:
                nodes = page.locator(show_more)
                if nodes.count() == 0:
                    break
                nodes.first.click(timeout=3000)
                page.wait_for_timeout(1500)
            except Exception:
                break
    found: list[str] = []
    for record in harvest_all(page, selectors, expand=False):
        for url, label in record.get("links", []):
            resolved = best_target(url, label)
            if not resolved or "t.co/" in resolved:
                continue
            if "x.com/" in resolved or "twitter.com/" in resolved:
                continue
            if resolved not in found and (looks_like_apply_url(resolved) or not found):
                found.append(resolved)
            if len(found) >= limit:
                break
        if len(found) >= limit:
            break
    sleep_jitter(int(run.get("scroll_pause_ms", 2200)), int(run.get("scroll_jitter_ms", 1600)))
    return found


def prefill_reply(page, selectors: dict[str, str], draft: str) -> bool:
    try:
        button = page.locator(selectors.get("reply_button", '[data-testid="reply"]')).first
        if button.count() == 0:
            return False
        button.click(timeout=8000)
        page.wait_for_timeout(1200)
        composer = page.locator(selectors.get("composer", '[data-testid="tweetTextarea_0"]')).first
        if composer.count() == 0:
            return False
        composer.click(timeout=5000)
        page.keyboard.type(draft, delay=random.randint(12, 45))
        page.bring_to_front()
        return True
    except Exception as error:
        print(f"  ! prefill failed: {error}")
        return False


def review(actions: list[dict[str, Any]], labels_path: Path, log_path: Path, browser_hook=None,
           actions_path: Path | None = None) -> dict[str, int]:
    stats = {"pass": 0, "reject": 0, "skipped": 0, "opened": 0, "resolved": 0}
    if not actions:
        print("No pending actions.")
        return stats
    print(f"\n{len(actions)} pending action(s). Keys: [o] open + prefill in browser, [y] mark pass, [n] mark reject, [Enter] skip, [q] quit\n")
    for index, action in enumerate(actions, start=1):
        title = " / ".join(filter(None, [action.get("company") or "", (action.get("roles") or [""])[0]])) or action.get("handle", "")
        print("=" * 72)
        print(f"[{index}/{len(actions)}] {title}   action={action['action']} ({action['action_reason']})  score={action.get('score')}")
        print(f"  {action.get('tweet_url')}")
        print(f"  {snippet(action.get('text', ''), 300)}")
        for url in (action.get("apply_urls") or []):
            print(f"  apply: {url}")
        for url in (action.get("reply_urls") or []):
            print(f"  from replies: {url}")
        if action.get("draft"):
            print("  draft:")
            for line in action["draft"].strip().splitlines():
                print(f"    | {line}")
        while True:
            try:
                choice = input("  > ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                choice = "q"
            if choice in {"q", "quit", "exit"}:
                print("Stopped.")
                return stats
            if choice in {"o", "open"}:
                if browser_hook:
                    ok = browser_hook(action)
                    print("  opened in browser - review and send there." if ok else "  could not open")
                    if ok:
                        stats["opened"] += 1
                continue
            if choice in {"y", "pass"}:
                stats["pass"] += 1
            elif choice in {"n", "reject"}:
                stats["reject"] += 1
            else:
                stats["skipped"] += 1
            break
        append_jsonl(labels_path, {
            "key": action.get("key"),
            "tweet_url": action.get("tweet_url"),
            "verdict": "pass" if choice in {"y", "pass"} else "reject" if choice in {"n", "reject"} else "skip",
            "kind": action.get("kind"),
            "text": action.get("text"),
            "handle": action.get("handle"),
            "labelled_at": datetime.now().isoformat(timespec="seconds"),
        })
        append_jsonl(log_path, {
            "at": datetime.now().isoformat(timespec="seconds"),
            "key": action.get("key"),
            "event": "review",
            "verdict": choice,
            "action": action.get("action"),
        })
        # Write the verdict back onto the queued row. Without this the item stays
        # "pending" and the next `review` offers it again, forever.
        if actions_path is not None and action.get("key"):
            resolved = "sent" if choice in {"y", "pass"} else "dismissed" if choice in {"n", "reject"} else "skipped"
            if resolve_action(action["key"], resolved, actions_path):
                stats["resolved"] += 1
    return stats


def tune(scorer, labels: list[dict[str, Any]], threshold: int) -> dict[str, Any]:
    labelled = [row for row in labels if row.get("verdict") in {"pass", "reject"}]
    if not labelled:
        return {"labelled": 0}
    true_positive = false_positive = false_negative = true_negative = 0
    reject_reasons: dict[str, int] = {}
    fired_on_rejects: dict[str, int] = {}
    fired_on_passes: dict[str, int] = {}
    for row in labelled:
        text = row.get("text", "")
        result = scorer.score_text(text, has_links=False)
        accepted, reason = scorer.verdict(result)
        for signal in result.signals:
            target = fired_on_rejects if row["verdict"] == "reject" else fired_on_passes
            target[signal] = target.get(signal, 0) + 1
        if row["verdict"] == "pass":
            if accepted:
                true_positive += 1
            else:
                false_negative += 1
        else:
            if accepted:
                false_positive += 1
                reject_reasons[reason] = reject_reasons.get(reason, 0) + 1
            else:
                true_negative += 1
    total = len(labelled)
    precision = true_positive / (true_positive + false_positive) if (true_positive + false_positive) else 0.0
    recall = true_positive / (true_positive + false_negative) if (true_positive + false_negative) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    print(f"labelled: {total}")
    print(f"precision: {precision:.2f}   recall: {recall:.2f}   f1: {f1:.2f}")
    print(f"true pass={true_positive}  missed={false_negative}  false keep={false_positive}  correct reject={true_negative}")
    print("\nsignals firing on your REJECTS (candidates to tighten):")
    for signal, count in sorted(fired_on_rejects.items(), key=lambda item: -item[1]):
        share_on_passes = fired_on_passes.get(signal, 0)
        print(f"  {signal:<24} rejects={count:<4} passes={share_on_passes}")
    print("\nsignals firing on your PASSES (candidates to boost):")
    for signal, count in sorted(fired_on_passes.items(), key=lambda item: -item[1]):
        print(f"  {signal:<24} passes={count:<4} rejects={fired_on_rejects.get(signal, 0)}")
    return {
        "labelled": total,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "false_positives": false_positive,
        "false_negatives": false_negative,
        "threshold": threshold,
    }


STATUSES = ("pending", "sent", "skipped", "dismissed", "attempted", "failed")
RESOLVED = ("sent", "skipped", "dismissed", "attempted", "failed")


def load_actions(path: Path, status: str | None = "pending") -> list[dict[str, Any]]:
    rows = list(read_jsonl(path))
    if status:
        rows = [row for row in rows if row.get("status", "pending") == status]
    return rows


def rewrite_actions(path: Path, updates: dict[str, dict[str, Any]]) -> int:
    """Write `status` changes back into actions.jsonl, keyed by tweet key.

    Without this the queue is write-only: `review` reads rows with status
    "pending", records a verdict in labels.jsonl, and leaves the row untouched -
    so the same item comes back on the next review, forever.

    Rewrites the whole file atomically via a temp file plus os.replace, the same
    pattern `redraft` uses, so an interrupted dashboard can't truncate the queue.
    Unknown keys are ignored; a key missing from the file is a no-op.
    """
    if not updates or not Path(path).exists():
        return 0
    rows = list(read_jsonl(path))
    applied = 0
    for row in rows:
        patch = updates.get(row.get("key"))
        if not patch:
            continue
        row.update(patch)
        applied += 1
    if not applied:
        return 0
    temp = Path(path).with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(temp, path)
    return applied


def resolve_action(key: str, status: str, path: Path, *, note: str = "", sent_url: str = "") -> bool:
    """Move one queued action to a resolved status and stamp the time."""
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}; expected one of {STATUSES}")
    patch: dict[str, Any] = {
        "status": status,
        "resolved_at": datetime.now().isoformat(timespec="seconds"),
    }
    if note:
        patch["note"] = note
    if sent_url:
        patch["sent_url"] = sent_url
    return rewrite_actions(path, {key: patch}) > 0


def resolved_count(path: Path) -> int:
    """How many actions have already been dealt with. Drives the auto-send gate."""
    if not Path(path).exists():
        return 0
    return sum(1 for row in read_jsonl(path) if row.get("status", "pending") in RESOLVED)


def pending_count(path: Path) -> int:
    return len(load_actions(path, status="pending"))


def pending_send_report(actions: list[dict[str, Any]]) -> str:
    counts: dict[str, int] = {}
    for action in actions:
        counts[action.get("action", "unknown")] = counts.get(action.get("action", "unknown"), 0) + 1
    return ", ".join(f"{key}={value}" for key, value in sorted(counts.items())) or "none"


def pause(seconds: float) -> None:
    time.sleep(seconds)


def eprint(*args: Any) -> None:
    print(*args, file=sys.stderr)