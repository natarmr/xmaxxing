from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from . import browser, config as config_module, extract, filters, outreach, storage
from .control import RunControl
from .runner import RunOptions, Runner

LOG = logging.getLogger("xmaxxing")


def setup_logging(config) -> Path | None:
    log_dir = config.path_for("log_dir", "logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
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
        "reply_links": config.path_for("reply_links_jsonl", "reply_links.jsonl"),
        "labels": config.path_for("labels_jsonl", "labels.jsonl"),
        "outreach_log": config.path_for("outreach_log", "outreach_log.jsonl"),
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
    log_path = setup_logging(config)
    paths = _paths(config)
    minutes = float(args.minutes if args.minutes is not None else config.get("run", "minutes", 30))
    options = RunOptions(
        source=args.source,
        minutes=minutes,
        dry_run=bool(args.dry_run),
        no_resolve=bool(args.no_resolve),
        reply_links=int(args.reply_links),
        seed_legacy=bool(args.seed_legacy),
        profile=args.profile,
        auth=args.auth,
    )
    # Ctrl+C has always been the only way to stop a CLI run; make it explicit
    # rather than relying on the traceback.
    control = RunControl(minutes=minutes)
    previous = signal.getsignal(signal.SIGINT)

    def on_sigint(signum, frame):  # pragma: no cover - interactive only
        if control.stop_requested:
            raise KeyboardInterrupt
        control.request_stop()
        print("\n  stop requested - finishing the current step...")

    try:
        signal.signal(signal.SIGINT, on_sigint)
    except ValueError:
        previous = None
    try:
        result = Runner(config, paths).run(options, control, log_path=log_path)
    except KeyboardInterrupt:
        print("\ninterrupted")
        return 130
    finally:
        if previous is not None:
            try:
                signal.signal(signal.SIGINT, previous)
            except ValueError:
                pass

    if not result.ok:
        print(f"  error: {result.error}")
        return 1

    print("\n" + "=" * 60)
    print(f"run finished in {result.elapsed_minutes:.1f} min (log: {log_path.name if log_path else '-'})")
    counts = result.counts
    # .get, not [...]: a RunResult that never reached the scraping loop carries
    # no counters, and a summary print must not be what turns that into a crash.
    print(f"  scanned={counts.get('scanned', 0)} kept={counts.get('kept', 0)} postings={counts.get('postings', 0)}")
    print(f"  rejected={counts.get('rejected', 0)} promoted={counts.get('promoted', 0)} "
          f"duplicates={counts.get('duplicates', 0)} collapsed={counts.get('collapsed', 0)}")
    print(f"  pages={result.stats.get('pages')} queries={result.stats.get('queries')}")
    if result.stopped:
        print("  stopped early on request")
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
        stats = outreach.review(actions, paths["labels"], paths["outreach_log"], hook, actions_path=paths["actions"])
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


def cmd_redraft(args, config) -> int:
    paths = _paths(config)
    templates = outreach.load_templates(config.path.parent / "outreach_templates.toml")
    if not paths["actions"].exists():
        print("No actions.jsonl yet.")
        return 0
    actions = list(storage.read_jsonl(paths["actions"]))
    if not actions:
        print("actions.jsonl is empty.")
        return 0
    rebuilt = []
    for action in actions:
        fresh = dict(action)
        if fresh.get("text"):
            fresh["roles"] = extract.extract_roles(fresh["text"])
            fresh["company"] = extract.extract_company(fresh["text"], fresh.get("handle") or "")
        fresh["draft"] = outreach.build_draft(fresh, action.get("action", "reply"), templates, config)
        rebuilt.append(fresh)
    temp = paths["actions"].with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        for action in rebuilt:
            handle.write(json.dumps(action, ensure_ascii=False) + "\n")
    os.replace(temp, paths["actions"])
    print(f"Rebuilt {len(rebuilt)} draft(s) in {paths['actions'].name}")
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


def cmd_dashboard(args, config) -> int:
    from . import server
    from .dashboard import DashboardState

    setup_logging(config)
    state = DashboardState(config, _paths(config))
    server.serve(state, port=int(args.port), open_browser=not args.no_open)
    return 0


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

    redraft = sub.add_parser("redraft", help="rebuild outreach drafts after editing templates")
    redraft.set_defaults(func=cmd_redraft)

    selftest = sub.add_parser("selftest", help="offline scorer and parser checks")
    selftest.set_defaults(func=cmd_selftest)

    dash = sub.add_parser("dashboard", help="local web dashboard on 127.0.0.1")
    dash.add_argument("--port", type=int, default=8765, help="0 picks a free port")
    dash.add_argument("--no-open", action="store_true", help="do not open a browser")
    dash.set_defaults(func=cmd_dashboard)
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