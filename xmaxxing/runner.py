from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import browser, extract, filters, outreach, sender, sources, storage
from .control import RunControl
from .events import EventBus

LOG = logging.getLogger("xmaxxing")

COUNT_KEYS = ("scanned", "kept", "rejected", "promoted", "duplicates", "postings", "collapsed")


@dataclass
class RunOptions:
    source: str = "search"
    minutes: float | None = None
    dry_run: bool = False
    no_resolve: bool = False
    reply_links: int = 0
    seed_legacy: bool = True
    profile: str | None = None
    auth: str | None = None


@dataclass
class RunResult:
    counts: dict[str, int] = field(default_factory=dict)
    stats: dict[str, int] = field(default_factory=dict)
    send: dict[str, int] = field(default_factory=dict)
    elapsed_minutes: float = 0.0
    log_path: Path | None = None
    stopped: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class Runner:
    """The scrape-and-score pipeline, shared by the CLI and the dashboard.

    One implementation on purpose: `xmaxxing run` and the dashboard's Start button
    must not drift into two scrapers with two different notions of "stopped".
    The CLI prints a summary; the dashboard subscribes to `bus`. Neither adds
    behaviour of its own.
    """

    def __init__(self, config, paths: dict[str, Path], bus: EventBus | None = None) -> None:
        self.config = config
        self.paths = paths
        self.bus = bus or EventBus()

    # -- helpers ---------------------------------------------------------

    def emit(self, event_type: str, **payload: Any) -> None:
        self.bus.publish({"type": event_type, "at": datetime.now().strftime("%H:%M:%S"), **payload})

    def resolve_profile_dir(self, options: RunOptions) -> Path:
        profile_dir = Path(options.profile or self.config.get("account", "profile_dir", "x_user_data_new"))
        return profile_dir if profile_dir.is_absolute() else self.config.path.parent / profile_dir

    def resolve_auth_file(self, options: RunOptions) -> Path:
        auth_file = Path(options.auth or self.config.get("account", "auth_file", "auth.json"))
        return auth_file if auth_file.is_absolute() else self.config.path.parent / auth_file

    def _auto_send(self, page, control: RunControl, options: RunOptions) -> dict[str, int]:
        """Send queued replies, if the policy in [outreach] allows it.

        Runs after the queue is built so you review the whole batch before
        anything goes out. Every outcome is written back to the ledger - a
        failure that isn't recorded is how you end up retrying a reply that
        actually landed.
        """
        paths = self.paths
        selectors = self.config.selectors
        resolved_before = outreach.resolved_count(paths["actions"])
        policy = sender.SendPolicy.from_config(self.config, dry_run=options.dry_run)
        pending = outreach.load_actions(paths["actions"], status="pending")
        summary = {"attempted": 0, "sent": 0, "failed": 0, "skipped": 0}

        eligible = [a for a in pending if a.get("action") in sender.AUTO_SENDABLE]
        allowed, reason = policy.allows(
            "reply" if eligible else "none", resolved_before=resolved_before, sent_this_run=0
        )
        self.emit("send_policy", mode=policy.mode, allowed=allowed, reason=reason,
                  resolved_before=resolved_before, pending=len(pending), replyable=len(eligible))
        if not allowed:
            LOG.info("auto-send not permitted: %s", reason)
            return summary

        run = self.config.section("run")
        pause_ms = int(self.config.get("outreach", "send_pause_ms", 4000) or 0)
        jitter_ms = int(self.config.get("outreach", "send_pause_jitter_ms", 6000) or 0)

        for index, action in enumerate(eligible):
            if control.should_stop():
                self.emit("send_stopped", after=summary["attempted"])
                break
            allowed, reason = policy.allows(
                "reply", resolved_before=resolved_before, sent_this_run=summary["attempted"]
            )
            if not allowed:
                self.emit("send_policy", mode=policy.mode, allowed=False, reason=reason)
                break
            self.emit("send_start", index=index, total=len(eligible),
                      company=action.get("company"), key=action.get("key"))
            outcome = sender.send_reply(page, action, self.config, selectors, control=control)
            summary["attempted"] += 1
            summary[outcome.status if outcome.status in summary else "skipped"] += 1
            outreach.resolve_action(
                action["key"], outcome.status, paths["actions"], note=outcome.detail
            )
            self.emit("send_result", key=action.get("key"), status=outcome.status,
                      verified=outcome.verified, detail=outcome.detail,
                      company=action.get("company"))
            LOG.info("send %s -> %s (%s)", action.get("key"), outcome.status, outcome.detail)
            if index < len(eligible) - 1 and not control.should_stop():
                browser.sleep_jitter(pause_ms, jitter_ms)
        return summary

    # -- pipeline --------------------------------------------------------

    def run(self, options: RunOptions, control: RunControl, log_path: Path | None = None) -> RunResult:
        from playwright.sync_api import sync_playwright

        config = self.config
        paths = self.paths
        scorer = filters.Scorer(config)
        templates = outreach.load_templates(config.path.parent / "outreach_templates.toml")

        result = RunResult(counts={key: 0 for key in COUNT_KEYS}, log_path=log_path)

        seen = storage.SeenStore(paths["seen"])
        seen.load()
        if options.seed_legacy:
            # Config-driven, not a hardcoded filename: pointing jobs_markdown
            # elsewhere should not silently seed from a file that no longer exists.
            seeded = seen.seed_from_markdown(paths["jobs_md"])
            LOG.info("seeded %s keys from legacy markdown", seeded)
        LOG.info("seen store: %s keys", len(seen))
        self.emit("state", state="running", seen=len(seen))
        if options.dry_run:
            # Nothing may be written, including seen.json.
            seen = storage.SeenStore(paths["seen"])
            seen.load()

        if not options.dry_run:
            paths["jobs_md"].parent.mkdir(parents=True, exist_ok=True)
        jobs_digest = storage.MarkdownDigest(paths["jobs_md"], "Jobs from X")
        hack_digest = storage.MarkdownDigest(paths["hack_md"], "Hackathons from X")

        counts = result.counts
        kept_records: list[dict[str, Any]] = []
        written_keys: set[str] = set()
        if not options.dry_run:
            for existing in list(storage.read_jsonl(paths["jobs_jsonl"])) + list(storage.read_jsonl(paths["hack_jsonl"])):
                written_keys.add(extract.posting_key(existing))

        def on_record(record: dict[str, Any]) -> bool:
            counts["scanned"] += 1
            key = record["key"]
            if key in seen:
                counts["duplicates"] += 1
                return False
            seen.add(key, handle=record.get("handle"), kind=record.get("kind"))
            if record.get("promoted"):
                counts["promoted"] += 1
                if not options.dry_run:
                    storage.append_jsonl(paths["rejected"], {
                        "key": key, "tweet_url": record["tweet_url"], "handle": record.get("handle"),
                        "reason": "promoted_or_sponsored", "snippet": extract.snippet(record.get("text", "")), "at": record["tweet_url"],
                    })
                return False
            text = record.get("text", "")
            score = scorer.score_text(text, has_links=bool(record.get("links")))
            accepted, reason = scorer.verdict(score)
            if not accepted:
                counts["rejected"] += 1
                if not options.dry_run:
                    storage.append_jsonl(paths["rejected"], {
                        "key": key, "tweet_url": record["tweet_url"], "handle": record.get("handle"),
                        "reason": reason, "score": score.score, "signals": score.signals,
                        "snippet": extract.snippet(text), "at": record["tweet_url"],
                    })
                return False
            kind = score.kind or "job"
            record["kind"] = kind
            record["score"] = score.score
            record["signals"] = score.signals
            postings = extract.build_posting(text, record.get("links", []), record.get("handle", ""), resolve=not options.no_resolve)
            if not postings:
                postings = [extract.Posting(text=text, links=record.get("links", []))]
            record_wrote_job = False
            for posting in postings:
                segment_score = score
                segment_kind = kind
                if posting.text != text:
                    segment_score = scorer.score_text(posting.text, has_links=bool(record.get("links")))
                    segment_ok, segment_reason = scorer.verdict(segment_score)
                    if not segment_ok:
                        LOG.info("segment dropped: %s | %s", extract.snippet(posting.text, 70), segment_reason)
                        continue
                    segment_kind = segment_score.kind or "job"
                record["company"] = (posting.companies or [None])[0]
                record["roles"] = posting.roles or record.get("roles") or []
                record["locations"] = posting.locations
                record["comp"] = posting.comp
                record["apply_urls"] = posting.apply_urls
                entry = entry_from(record, posting, segment_score, segment_kind)
                counts["postings"] += 1
                if segment_kind == "job":
                    record_wrote_job = True
                if options.dry_run:
                    continue
                dedupe_key = extract.posting_key(entry)
                if dedupe_key in written_keys:
                    counts["collapsed"] += 1
                    LOG.info("collapsed duplicate: %s %s", entry.get("company"), (entry.get("roles") or ["-"])[0])
                    continue
                written_keys.add(dedupe_key)
                if segment_kind == "hackathon":
                    storage.append_jsonl(paths["hack_jsonl"], entry)
                    hack_digest.append(entry)
                else:
                    storage.append_jsonl(paths["jobs_jsonl"], entry)
                    jobs_digest.append(entry)
                self.emit("posting", entry=entry)
            if record_wrote_job:
                kept_records.append(record)
                counts["kept"] += 1
                LOG.info("kept [%s] score=%s %s %s", kind, score.score, record.get("handle"), extract.snippet(text, 100))
                self.emit("kept", key=key, company=record.get("company"), score=score.score, kind=kind)
            self.emit("progress", counts=dict(counts))
            return record_wrote_job

        selectors = self.config.selectors
        profile_dir = self.resolve_profile_dir(options)
        auth_file = self.resolve_auth_file(options)

        with sync_playwright() as playwright:
            context = browser.launch_context(playwright, config, profile_dir, auth_file)
            try:
                page = browser.first_page(context)
                page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=60000)
                if not browser.ensure_login(page, config, selectors, control=control, on_state=self.emit):
                    result.error = "not_logged_in"
                    return result
                if not options.dry_run:
                    browser.export_auth(context, auth_file)

                if options.source == "search":
                    result.stats = sources.iter_search(page, config, selectors, on_record, control=control)
                else:
                    result.stats = sources.iter_home(page, config, selectors, on_record, control=control)

                if options.reply_links > 0 and kept_records and not options.dry_run and not control.should_stop():
                    self.emit("state", phase="reply_links", count=options.reply_links)
                    LOG.info("harvesting reply links for top %s posts", options.reply_links)
                    ranked = sorted(kept_records, key=lambda item: item.get("score", 0), reverse=True)[: options.reply_links]
                    for record in ranked:
                        if control.should_stop():
                            break
                        try:
                            urls = outreach.harvest_reply_links(page, record["tweet_url"], config, selectors)
                        except Exception as error:
                            LOG.warning("reply link harvest failed for %s: %s", record["key"], error)
                            continue
                        if urls:
                            storage.append_jsonl(paths["reply_links"], {
                                "key": record["key"],
                                "tweet_url": record["tweet_url"],
                                "handle": record.get("handle"),
                                "urls": urls,
                                "at": datetime.now().isoformat(timespec="seconds"),
                            })
                            LOG.info("reply links for %s: %s", record["key"], len(urls))

                if not options.dry_run and not control.should_stop():
                    reply_map = {
                        row["key"]: row.get("urls", [])
                        for row in storage.read_jsonl(paths["reply_links"])
                        if row.get("urls")
                    }
                    # Never queue the same tweet twice. With auto-send on the
                    # table, a duplicate row means messaging someone twice.
                    queued = {
                        row.get("key") for row in storage.read_jsonl(paths["actions"])
                    }
                    actions = [
                        action for action in outreach.build_actions(kept_records, templates, config, reply_map)
                        if action.get("key") not in queued
                    ]
                    for action in actions:
                        storage.append_jsonl(paths["actions"], action)
                        queued.add(action.get("key"))
                        self.emit("action", action=action)
                    LOG.info("action queue: %s (%s)", len(actions), outreach.pending_send_report(actions))
                    self.emit("state", phase="outreach", actions=len(actions))

                    result.send = self._auto_send(page, control, options)
                    if result.send.get("attempted"):
                        LOG.info("auto-send: %s", result.send)
                        self.emit("state", phase="send_done", send=result.send)
            finally:
                try:
                    context.close()
                except Exception:
                    pass

        result.stopped = control.stop_requested
        result.elapsed_minutes = round(control.elapsed() / 60, 1)
        if not options.dry_run:
            # Persist the seen store even when stopped early, otherwise the next
            # run re-scrapes everything the interrupted one already handled.
            seen.save()
        return result


def entry_from(record: dict[str, Any], posting, score, kind: str) -> dict[str, Any]:
    """Flatten one scored segment into the jsonl row shape.

    Lives here rather than in `cli` because both frontends need it, and the row
    keys are the dashboard's read model - keep them stable.
    """
    return {
        "key": record["key"],
        "tweet_url": record["tweet_url"],
        "handle": record.get("handle"),
        "display_name": record.get("display_name"),
        "relative_time": record.get("relative_time"),
        "posted_at": record.get("posted_at"),
        "scraped_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "kind": kind,
        "company": (posting.companies or [None])[0],
        "roles": posting.roles,
        "locations": posting.locations,
        "comp": posting.comp,
        "eligibility": posting.eligibility.get("raw"),
        "eligibility_years": posting.eligibility.get("years"),
        "deadline": posting.deadline.get("raw"),
        "deadline_date": posting.deadline.get("date"),
        "expired": posting.deadline.get("expired"),
        "apply_urls": posting.apply_urls,
        "links": [{"url": url, "label": label} for url, label in posting.links],
        "text": posting.text,
        "score": score.score,
        "signals": score.signals,
        "location_tag": score.location_tag,
        "role_hit": score.role_hit,
    }