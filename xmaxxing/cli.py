from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import browser, config as config_module, dom, extract, filters, outreach, sources, storage

LOG = logging.getLogger("xmaxxing")


def setup_logging(config) -> Path | None:
    log_dir = config.path_for("log_dir", "logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = log_dir / f"scrape-{stamp}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(log_path, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
        force=True,
    )
    return log_path


def _paths(config) -> dict[str, Path]:
    return {
        "jobs_md": config.path_for("jobs_markdown", "jobs_and_hackathons.md"),
        "hack_md": config.path_for("hackathons_markdown", "hackathons.md"),
        "jobs_jsonl": config.path_for("jobs_jsonl", "jobs.jsonl"),
        "hack_jsonl": config.path_for("hackathons_jsonl", "hackathons.jsonl"),
        "rejected": config.path_for("rejected_jsonl", "rejected.jsonl"),
        "seen": config.path_for("seen_json", "seen.json"),
        "actions": config.path_for("actions_jsonl", "actions.jsonl"),
        "labels": config.path_for("labels_jsonl", "labels.jsonl"),
        "outreach_log": config.path_for("outreach_log", "outreach_log.jsonl"),
    }


def _entry_from(record: dict[str, Any], posting, score, kind: str) -> dict[str, Any]:
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
        "text": posting.text,
        "score": score.score,
        "signals": score.signals,
        "location_tag": score.location_tag,
        "role_hit": score.role_hit,
    }


def cmd_login(args, config) -> int:
    import time

    from playwright.sync_api import sync_playwright

    paths = _paths(config)
    profile_dir = Path(args.profile or config.get("account", "profile_dir", "x_user_data_new"))
    if not profile_dir.is_absolute():
        profile_dir = config.path.parent / profile_dir
    auth_file = Path(args.auth or config.get("account", "auth_file", "auth.json"))
    if not auth_file.is_absolute():
        auth_file = config.path.parent / auth_file
    selectors = config.selectors
    timeout_seconds = int(args.timeout_minutes) * 60
    print(f"profile: {profile_dir}")
    print(f"session: {auth_file}")
    print("A Chromium window is opening on the X login page.", flush=True)
    with sync_playwright() as playwright:
        context = browser.launch_context(playwright, config, profile_dir, auth_file)
        page = browser.first_page(context)
        page.goto("https://x.com/i/flow/login", wait_until="domcontentloaded", timeout=90000)
        try:
            page.bring_to_front()
        except Exception:
            pass
        start = time.time()
        announced = 0.0
        while True:
            if browser.is_logged_in(page, selectors, timeout_ms=2000):
                time.sleep(5)
                print("Login detected.", flush=True)
                break
            waited = time.time() - start
            if waited - announced >= 15:
                announced = waited
                print(f"  waiting for login... {int(waited)}s / {timeout_seconds}s", flush=True)
            if waited >= timeout_seconds:
                print("Timed out. Nothing saved.")
                context.close()
                return 1
            time.sleep(2)
        browser.export_auth(context, auth_file)
        context.close()
    print(f"Done. Future runs reuse {auth_file.name}; no password is stored.", flush=True)
    return 0


def cmd_run(args, config) -> int:
    from playwright.sync_api import sync_playwright

    log_path = setup_logging(config)
    paths = _paths(config)
    scorer = filters.Scorer(config)
    templates = outreach.load_templates(config.path.parent / "outreach_templates.toml")

    seen = storage.SeenStore(paths["seen"])
    seen.load()
    if args.seed_legacy:
        seeded = seen.seed_from_markdown(config.path.parent / "jobs_and_hackathons.md")
        LOG.info("seeded %s keys from legacy markdown", seeded)
    LOG.info("seen store: %s keys", len(seen))

    if not args.dry_run:
        paths["jobs_md"].parent.mkdir(parents=True, exist_ok=True)
    jobs_digest = storage.MarkdownDigest(paths["jobs_md"], "Jobs from X")
    hack_digest = storage.MarkdownDigest(paths["hack_md"], "Hackathons from X")

    counts = {"scanned": 0, "kept": 0, "rejected": 0, "promoted": 0, "duplicates": 0, "postings": 0}
    kept_records: list[dict[str, Any]] = []

    def on_record(record: dict[str, Any]) -> bool:
        counts["scanned"] += 1
        key = record["key"]
        if key in seen:
            counts["duplicates"] += 1
            return False
        seen.add(key, handle=record.get("handle"), kind=record.get("kind"))
        if record.get("promoted"):
            counts["promoted"] += 1
            if not args.dry_run:
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
            if not args.dry_run:
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
        record["company"] = None
        record["roles"] = extract.extract_roles(text)
        postings = extract.build_posting(text, record.get("links", []), record.get("handle", ""), resolve=not args.no_resolve)
        if not postings:
            postings = [extract.Posting(text=text, links=record.get("links", []))]
        for posting in postings:
            record["company"] = (posting.companies or [None])[0]
            record["roles"] = posting.roles or record["roles"]
            record["locations"] = posting.locations
            record["comp"] = posting.comp
            record["apply_urls"] = posting.apply_urls
            entry = _entry_from(record, posting, score, kind)
            counts["postings"] += 1
            if not args.dry_run:
                if kind == "hackathon":
                    storage.append_jsonl(paths["hack_jsonl"], entry)
                    hack_digest.append(entry)
                else:
                    storage.append_jsonl(paths["jobs_jsonl"], entry)
                    jobs_digest.append(entry)
        if kind == "job":
            kept_records.append(record)
        counts["kept"] += 1
        LOG.info("kept [%s] score=%s %s %s", kind, score.score, record.get("handle"), extract.snippet(text, 100))
        return True

    profile_dir = Path(args.profile or config.get("account", "profile_dir", "x_user_data_new"))
    if not profile_dir.is_absolute():
        profile_dir = config.path.parent / profile_dir
    auth_file = Path(args.auth or config.get("account", "auth_file", "auth.json"))
    if not auth_file.is_absolute():
        auth_file = config.path.parent / auth_file

    minutes = int(args.minutes if args.minutes is not None else config.get("run", "minutes", 30))
    deadline = sources.deadline_from_minutes(minutes)
    selectors = config.selectors

    with sync_playwright() as playwright:
        context = browser.launch_context(playwright, config, profile_dir, auth_file)
        page = browser.first_page(context)
        page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=60000)
        browser.ensure_login(page, config, selectors)
        if not args.dry_run:
            browser.export_auth(context, auth_file)

        if args.source == "search":
            stats = sources.iter_search(page, config, selectors, on_record, deadline)
        else:
            stats = sources.iter_home(page, config, selectors, on_record, deadline)

        if args.reply_links > 0 and kept_records and not args.dry_run:
            LOG.info("harvesting reply links for top %s posts", args.reply_links)
            ranked = sorted(kept_records, key=lambda item: item.get("score", 0), reverse=True)[: args.reply_links]
            for record in ranked:
                try:
                    urls = outreach.harvest_reply_links(page, record["tweet_url"], config, selectors)
                except Exception as error:
                    LOG.warning("reply link harvest failed for %s: %s", record["key"], error)
                    continue
                if urls:
                    record.setdefault("reply_urls", [])
                    record["reply_urls"] = list(dict.fromkeys(record["reply_urls"] + urls))
                    storage.append_jsonl(paths["actions"], {**record, "reply_urls": record["reply_urls"]})

        if not args.dry_run:
            actions = outreach.build_actions(kept_records, templates, config)
            for action in actions:
                storage.append_jsonl(paths["actions"], action)
            LOG.info("action queue: %s (%s)", len(actions), outreach.pending_send_report(actions))
        context.close()

    seen.save()
    elapsed = (datetime.now() - (deadline - timedelta(minutes=minutes))).total_seconds() / 60
    print("\n" + "=" * 60)
    print(f"run finished in {elapsed:.1f} min (log: {log_path.name if log_path else '-'})")
    print(f"  scanned={counts['scanned']} kept={counts['kept']} postings={counts['postings']}")
    print(f"  rejected={counts['rejected']} promoted={counts['promoted']} duplicates={counts['duplicates']}")
    print(f"  pages={stats.get('pages')} queries={stats.get('queries')}")
    if args.dry_run:
        print("  dry run: nothing written")
    return 0


def cmd_review(args, config) -> int:
    paths = _paths(config)
    actions = outreach.load_actions(paths["actions"], status="pending")
    if not actions:
        print("No pending actions in actions.jsonl")
        return 0
    hook = None
    browser_holder: dict[str, Any] = {}
    if args.open_browser:
        from playwright.sync_api import sync_playwright

        playwright = sync_playwright().start()
        profile_dir = Path(config.get("account", "profile_dir", "x_user_data_new"))
        if not profile_dir.is_absolute():
            profile_dir = config.path.parent / profile_dir
        auth_file = Path(config.get("account", "auth_file", "auth.json"))
        if not auth_file.is_absolute():
            auth_file = config.path.parent / auth_file
        context = browser.launch_context(playwright, config, profile_dir, auth_file)
        page = browser.first_page(context)
        browser.ensure_login(page, config, config.selectors)
        browser_holder.update({"pw": playwright, "ctx": context, "page": page})

        def hook(action: dict[str, Any]) -> bool:
            page = browser_holder["page"]
            if not browser.open_page(page, action["tweet_url"], config.selectors.get("tweet"), timeout_ms=25000):
                return False
            if action["action"] == "reply" and action.get("draft"):
                return outreach.prefill_reply(page, config.selectors, action["draft"])
            return True

        hook = hook

    try:
        stats = outreach.review(actions, paths["labels"], paths["outreach_log"], hook)
    finally:
        if browser_holder:
            browser_holder["ctx"].close()
            browser_holder["pw"].stop()
    print("\nreview summary:", stats)
    print("next: python -m xmaxxing tune")
    return 0


def cmd_tune(args, config) -> int:
    paths = _paths(config)
    scorer = filters.Scorer(config)
    labels = list(storage.read_jsonl(paths["labels"]))
    if not labels:
        print(f"No labels yet. Run: python -m xmaxxing review --open-browser")
        return 0
    report = outreach.tune(scorer, labels, scorer.threshold)
    if args.threshold is not None:
        print(f"\n(current threshold {scorer.threshold} -> requested {args.threshold}; edit [scoring] threshold in config.toml)")
    return 0


def cmd_selftest(args, config) -> int:
    samples = [
        ("We are hiring a Full Stack AI Intern. Comment your portfolio! Remote, stipend 15k/month.", "job"),
        ("Intern: does this AI thing work? me: git push --force", "reject"),
        ("Consumer Competition Claims has launched a class action against Valve in the Netherlands.", "reject"),
        ("Registration is open for ISRO Bharatiya Antariksh Hackathon 2026. Prize pool 5L. Deadline: June 30", "hackathon"),
        ("Congrats to everyone who qualified for the Amazon HackOn 48-Hour Hackathon!", "reject"),
        ("Redrob AI is hiring Software Developers and Interns. Graduation Year: 2023/2024/2025 Apply now.", "job"),
        ("Keycard Shell is the first fully modular open-source hardware wallet. Get your starter kit today.", "reject"),
        ("Hiring AI engineers who treat evals as the actual work. SF only. $200K-$250K + equity.", "job"),
        ("My doctor said my iron levels are critically low. I needed a bioavailable mineral infusion.", "reject"),
        ("everyone is hiring. my feed is filled with founders and companies hiring", "reject"),
    ]
    scorer = filters.Scorer(config)
    print(f"threshold={scorer.threshold}\n")
    passed = 0
    for text, expected in samples:
        score = scorer.score_text(text, has_links="http" in text)
        accepted, reason = scorer.verdict(score)
        kind = score.kind or "-"
        ok = kind == expected if expected != "reject" else not accepted
        passed += int(ok)
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] expected={expected:<9} got={kind:<9} accepted={str(accepted):<5} score={score.score:>3} role={score.role_hit or '-'} :: {reason}")
    print(f"\n{passed}/{len(samples)} expectations met")
    print("\nurl normalization:")
    for raw in (
        "https://x.com/user/status/123?ref_src=twsrc%5Etfw&s=20",
        "https://x.com/user/status/123/analytics",
        "https://x.com/user/status/123/photo/1",
        "https://twitter.com/User/status/123",
    ):
        print(f"  {raw} -> {extract.status_key(raw)}")
    print("\ntext cleanup:")
    print(" ", repr(extract.clean_text("apply here:\nhttps://\njobfound.org/job/x\n  -is-hiring")))
    print("\nmulti-job split:", len(extract.split_multi_jobs(
        "Nitesh is hiring for Frontend Developer Apply here: https://a\n\nTower Research is hiring for Software Engineer I Apply here: https://b\n\nSingle is hiring for QA Apply: https://c"
    )), "segments")
    return 0 if passed == len(samples) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="xmaxxing", description="X job and hackathon finder")
    parser.add_argument("--config", default=None, help="path to config.toml")
    sub = parser.add_subparsers(dest="command", required=True)

    login = sub.add_parser("login", help="log in once and export the session")
    login.add_argument("--profile", default=None)
    login.add_argument("--auth", default=None)
    login.add_argument("--timeout-minutes", type=int, default=20, dest="timeout_minutes")
    login.set_defaults(func=cmd_login)

    run = sub.add_parser("run", help="scrape and write results")
    run.add_argument("--source", choices=["search", "home"], default="search")
    run.add_argument("--minutes", type=int, default=None)
    run.add_argument("--profile", default=None)
    run.add_argument("--auth", default=None)
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--seed-legacy", action="store_true", default=True)
    run.add_argument("--reply-links", type=int, default=0, help="harvest reply-thread links for the top N posts")
    run.add_argument("--no-resolve", action="store_true", help="skip t.co resolution")
    run.set_defaults(func=cmd_run)

    review = sub.add_parser("review", help="review the action queue")
    review.add_argument("--open-browser", action="store_true")
    review.set_defaults(func=cmd_review)

    tune = sub.add_parser("tune", help="measure the scorer against your labels")
    tune.add_argument("--threshold", type=int, default=None)
    tune.set_defaults(func=cmd_tune)

    selftest = sub.add_parser("selftest", help="offline scorer and parser checks")
    selftest.set_defaults(func=cmd_selftest)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = config_module.load(args.config)
    except config_module.ConfigError as error:
        print(f"config error: {error}")
        return 2
    return int(args.func(args, config) or 0)


if __name__ == "__main__":
    raise SystemExit(main())