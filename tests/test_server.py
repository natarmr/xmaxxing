from __future__ import annotations

import json
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from xmaxxing import config as config_module, server
from xmaxxing.dashboard import DashboardState

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = config_module.load(ROOT / "config.example.toml")


def _paths(tmp: Path) -> dict[str, Path]:
    names = {
        "jobs_md": "jobs_and_hackathons.md",
        "hack_md": "hackathons.md",
        "jobs_jsonl": "jobs.jsonl",
        "hack_jsonl": "hackathons.jsonl",
        "rejected": "rejected.jsonl",
        "seen": "seen.json",
        "actions": "actions.jsonl",
        "reply_links": "reply_links.jsonl",
        "labels": "labels.jsonl",
        "outreach_log": "outreach_log.jsonl",
    }
    return {key: tmp / value for key, value in names.items()}


class _Harness:
    """A real server on an ephemeral loopback port, torn down after each test."""

    def __init__(self, tmp: Path) -> None:
        self.state = DashboardState(EXAMPLE, _paths(tmp))
        self.httpd = server.make_server(self.state, port=0)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def get(self, path: str, *, token: str | None = "auto", origin: str | None = None):
        headers = {}
        if token == "auto":
            token = self.state.token
        if token is not None:
            headers["X-Xmaxxing-Token"] = token
        if origin is not None:
            headers["Origin"] = origin
        req = urllib.request.Request(self.url(path), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                return res.status, res.read(), dict(res.headers)
        except urllib.error.HTTPError as err:
            return err.code, err.read(), dict(err.headers)

    def post(self, path: str, payload: dict, *, token: str | None = "auto", origin: str | None = None,
             content_type: str | None = "application/json"):
        headers = {"Content-Type": content_type} if content_type else {}
        if token == "auto":
            token = self.state.token
        if token is not None:
            headers["X-Xmaxxing-Token"] = token
        if origin is not None:
            headers["Origin"] = origin
        req = urllib.request.Request(
            self.url(path), data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                return res.status, json.loads(res.read() or b"{}")
        except urllib.error.HTTPError as err:
            body = err.read()
            try:
                return err.code, json.loads(body or b"{}")
            except json.JSONDecodeError:
                return err.code, {"raw": body.decode("utf-8", "replace")}

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def _harness():
    tmp = tempfile.TemporaryDirectory()
    return _Harness(Path(tmp.name)), tmp


# --- routing -------------------------------------------------------------


def test_health_needs_no_token() -> None:
    h, tmp = _harness()
    try:
        status, body, _ = h.get("/api/health", token=None)
        assert status == 200 and json.loads(body)["ok"] is True
    finally:
        h.close(); tmp.cleanup()


def test_index_is_served_with_a_token_and_embeds_it() -> None:
    h, tmp = _harness()
    try:
        status, body, headers = h.get("/")
        assert status == 200
        assert b"XMAXXING_TOKEN" in body
        assert h.state.token.encode() in body
        assert "text/html" in headers["Content-Type"]
    finally:
        h.close(); tmp.cleanup()


def test_index_via_query_token_survives_a_reload() -> None:
    h, tmp = _harness()
    try:
        req = urllib.request.Request(h.url(f"/?t={h.state.token}"))
        with urllib.request.urlopen(req, timeout=5) as res:
            assert res.status == 200
    finally:
        h.close(); tmp.cleanup()


def test_a_guessed_port_gets_nothing() -> None:
    h, tmp = _harness()
    try:
        status, _body, _ = h.get("/", token=None)
        assert status == 403
        status, _body, _ = h.get("/app.js", token="wrong-token")
        assert status == 403
    finally:
        h.close(); tmp.cleanup()


def test_state_requires_the_token() -> None:
    h, tmp = _harness()
    try:
        assert h.get("/api/state", token=None)[0] == 403
        assert h.get("/api/state", token="nope")[0] == 403
        assert h.get("/api/state")[0] == 200
    finally:
        h.close(); tmp.cleanup()


def test_state_snapshot_has_the_shape_the_ui_expects() -> None:
    h, tmp = _harness()
    try:
        _status, body, _ = h.get("/api/state")
        snap = json.loads(body)
        for key in ("run", "busy", "policy", "jobs", "hackathons", "actions", "rejected", "reply_links"):
            assert key in snap, key
        assert snap["run"]["status"] == "idle"
        assert snap["policy"]["mode"] == "review"
        assert snap["policy"]["would_send"] is False
    finally:
        h.close(); tmp.cleanup()


# --- the localhost threat model -------------------------------------------


def test_cross_origin_posts_are_refused() -> None:
    """Any page you visit could otherwise drive your authenticated scraper."""
    h, tmp = _harness()
    try:
        status, payload = h.post("/api/start", {}, origin="https://evil.example")
        assert status == 403, (status, payload)
        assert "cross-origin" in payload.get("error", "")
    finally:
        h.close(); tmp.cleanup()


def test_same_origin_posts_are_allowed() -> None:
    h, tmp = _harness()
    try:
        origin = f"http://127.0.0.1:{h.port}"
        status, _payload = h.post("/api/stop", {}, origin=origin)
        # 409 not 403: the request was allowed through, nothing was running.
        assert status == 409, status
    finally:
        h.close(); tmp.cleanup()


def test_posts_without_the_token_are_refused() -> None:
    h, tmp = _harness()
    try:
        status, _ = h.post("/api/start", {}, token=None)
        assert status == 403
        status, _ = h.post("/api/stop", {}, token="wrong")
        assert status == 403
    finally:
        h.close(); tmp.cleanup()


def test_a_non_json_content_type_is_refused() -> None:
    """Forces a CORS preflight for anything that isn't our own page."""
    h, tmp = _harness()
    try:
        status, payload = h.post("/api/start", {}, content_type="text/plain")
        assert status == 400, (status, payload)
        assert "content-type" in payload.get("error", "").lower()
    finally:
        h.close(); tmp.cleanup()


def test_responses_carry_a_locked_down_csp() -> None:
    h, tmp = _harness()
    try:
        _status, _body, headers = h.get("/")
        csp = headers.get("Content-Security-Policy", "")
        assert "default-src 'none'" in csp
        assert "script-src 'self'" in csp
        assert headers.get("X-Content-Type-Options") == "nosniff"
        assert headers.get("Referrer-Policy") == "no-referrer"
    finally:
        h.close(); tmp.cleanup()


def test_server_binds_loopback_only() -> None:
    h, tmp = _harness()
    try:
        assert h.httpd.server_address[0] == "127.0.0.1"
        assert server.BIND_HOST == "127.0.0.1"
    finally:
        h.close(); tmp.cleanup()


# --- ledger through the API ----------------------------------------------


def test_resolving_an_action_over_the_api() -> None:
    h, tmp = _harness()
    try:
        with h.state.paths["actions"].open("w", encoding="utf-8") as fh:
            fh.write(json.dumps({"key": "a/1", "action": "reply", "status": "pending", "draft": "hi"}) + "\n")
        h.state._invalidate()
        status, payload = h.post("/api/actions/resolve", {"key": "a/1", "status": "sent"})
        assert status == 200 and payload["ok"] is True, payload
        _status, body, _ = h.get("/api/state")
        snap = json.loads(body)
        assert snap["actions"][0]["status"] == "sent"
        assert snap["policy"]["resolved_before"] == 1
    finally:
        h.close(); tmp.cleanup()


def test_the_api_rejects_an_unknown_status() -> None:
    h, tmp = _harness()
    try:
        with h.state.paths["actions"].open("w", encoding="utf-8") as fh:
            fh.write(json.dumps({"key": "a/1", "status": "pending"}) + "\n")
        status, payload = h.post("/api/actions/resolve", {"key": "a/1", "status": "teleported"})
        assert status == 404 or payload.get("ok") is False, (status, payload)
    finally:
        h.close(); tmp.cleanup()


def test_stop_when_nothing_is_running_is_a_conflict_not_a_crash() -> None:
    h, tmp = _harness()
    try:
        status, payload = h.post("/api/stop", {})
        assert status == 409 and payload["ok"] is False, payload
    finally:
        h.close(); tmp.cleanup()


# --- one run at a time ----------------------------------------------------


def test_a_second_start_is_refused_while_a_run_is_in_flight() -> None:
    h, tmp = _harness()
    release = threading.Event()
    try:
        # Stand in for the worker with something that actually stays alive.
        h.state._control = object()
        h.state._thread = threading.Thread(target=release.wait)
        h.state._thread.start()
        assert h.state.busy is True
        try:
            status, payload = h.post("/api/start", {"minutes": 1})
            assert status == 409, (status, payload)
            assert "already in flight" in payload["message"], payload
        finally:
            release.set()
            h.state._thread.join(timeout=2)
    finally:
        h.close(); tmp.cleanup()


def test_tokens_are_per_instance_and_long() -> None:
    h, tmp = _harness()
    try:
        assert len(h.state.token) >= 32, h.state.token
        h2, tmp2 = _harness()
        try:
            assert h2.state.token != h.state.token
        finally:
            h2.close(); tmp2.cleanup()
    finally:
        h.close(); tmp.cleanup()