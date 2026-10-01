from __future__ import annotations

import json
import random
import time
from pathlib import Path
from typing import Any

STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
window.chrome = window.chrome || {runtime: {}};
"""

BLOCKED_RESOURCE_TYPES = {"image", "media", "font"}
BLOCKED_URL_PARTS = ("google-analytics", "googletagmanager", "doubleclick", "adservice", "t.co/i/", "abs.twimg.com/responsive_client")


def restore_auth(context, auth_file: Path | None) -> int:
    if not auth_file or not Path(auth_file).exists():
        return 0
    try:
        payload = json.loads(Path(auth_file).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    cookies = [cookie for cookie in payload.get("cookies", []) if cookie.get("name") and cookie.get("domain")]
    restored = 0
    for cookie in cookies:
        payload_cookie = {
            "name": cookie["name"],
            "value": cookie["value"],
            "domain": cookie["domain"],
            "path": cookie.get("path", "/"),
        }
        if cookie.get("expires"):
            payload_cookie["expires"] = float(cookie["expires"])
        same_site = (cookie.get("sameSite") or "").lower()
        if same_site in {"lax", "none", "strict"}:
            payload_cookie["sameSite"] = same_site.capitalize() if same_site != "none" else "None"
        try:
            context.add_cookies([payload_cookie])
            restored += 1
        except Exception:
            continue
    if restored:
        print(f"Restored {restored} session cookie(s) from {Path(auth_file).name}")
    return restored


def launch_context(playwright, config, profile_dir: Path, auth_file: Path):
    account = config.section("account")
    run = config.section("run")
    profile_dir.mkdir(parents=True, exist_ok=True)
    args = [
        "--disable-blink-features=AutomationControlled",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--disable-features=IsolateOrigins,site-per-process,TranslateUI",
    ]
    kwargs: dict[str, Any] = {
        "user_data_dir": str(profile_dir),
        "headless": bool(run.get("headless", False)),
        "viewport": {
            "width": int(account.get("viewport_width", 1440)),
            "height": int(account.get("viewport_height", 900)),
        },
        "locale": account.get("locale", "en-US"),
        "timezone_id": account.get("timezone", "Asia/Kolkata"),
        "args": args,
        "ignore_default_args": ["--enable-automation"],
    }
    if run.get("user_agent"):
        kwargs["user_agent"] = run["user_agent"]
    context = playwright.chromium.launch_persistent_context(**kwargs)
    context.set_default_timeout(15000)
    context.add_init_script(STEALTH_JS)
    restore_auth(context, auth_file)
    if run.get("block_media", True):
        def route_handler(route, request):
            if request.resource_type in BLOCKED_RESOURCE_TYPES or any(part in request.url for part in BLOCKED_URL_PARTS):
                try:
                    route.abort()
                except Exception:
                    pass
                return
            try:
                route.continue_()
            except Exception:
                pass
        context.route("**/*", route_handler)
    return context


def first_page(context):
    return context.pages[0] if context.pages else context.new_page()


def has_auth_cookie(context) -> bool:
    try:
        for cookie in context.cookies("https://x.com"):
            if cookie.get("name") == "auth_token" and cookie.get("value"):
                return True
    except Exception:
        return False
    return False


def is_logged_in(page, selectors: dict[str, str], timeout_ms: int = 8000) -> bool:
    context = page.context
    if has_auth_cookie(context):
        return True
    deadline = time.time() + max(0.0, timeout_ms / 1000.0)
    markers = [selector.strip() for selector in (selectors.get("logged_in_marker") or "").split(",") if selector.strip()]
    while True:
        for marker in markers or [selectors.get("home_tab", "")]:
            if not marker:
                continue
            try:
                if page.locator(marker).count() > 0:
                    return True
            except Exception:
                continue
        if has_auth_cookie(context) or time.time() >= deadline:
            return False
        time.sleep(1.0)


def ensure_login(page, config, selectors: dict[str, str]) -> bool:
    if is_logged_in(page, selectors):
        return True
    if auth_file_present(config):
        print("Session file exists but X rejected it - it may have expired.")
    print("\n" + "=" * 62)
    print("NOT LOGGED IN. A browser window is open at the X login page.")
    print("Log in there (2FA included). This script will continue on its own.")
    print("=" * 62 + "\n")
    try:
        if "/i/flow/login" not in page.url:
            page.goto("https://x.com/i/flow/login", wait_until="domcontentloaded", timeout=60000)
    except Exception:
        pass
    try:
        page.bring_to_front()
    except Exception:
        pass
    announced = 0.0
    start = time.time()
    while True:
        if is_logged_in(page, selectors, timeout_ms=2000):
            time.sleep(4)
            print("Login detected.")
            return True
        waited = time.time() - start
        if waited - announced >= 30:
            announced = waited
            print(f"  still waiting for login ({int(waited)}s)...", flush=True)


def auth_file_present(config) -> bool:
    raw = config.get("account", "auth_file", "auth.json")
    path = Path(raw)
    return (path if path.is_absolute() else config.path.parent / path).exists()


def export_auth(context, auth_file: Path) -> None:
    auth_file.parent.mkdir(parents=True, exist_ok=True)
    context.storage_state(path=str(auth_file))
    print(f"Saved session to {auth_file.name}")


def jitter(pause_ms: int, jitter_ms: int) -> float:
    return (pause_ms + random.randint(0, max(0, jitter_ms))) / 1000.0


def sleep_jitter(pause_ms: int, jitter_ms: int) -> None:
    time.sleep(jitter(pause_ms, jitter_ms))


def open_page(page, url: str, wait_selector: str | None = None, timeout_ms: int = 20000) -> bool:
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    except Exception as error:
        print(f"  ! goto failed: {error}")
        return False
    if wait_selector:
        try:
            page.wait_for_selector(wait_selector, timeout=timeout_ms, state="attached")
        except Exception:
            return False
    return True