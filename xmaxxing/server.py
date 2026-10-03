from __future__ import annotations

import json
import logging
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import events as events_module
from .assets import APP_CSS, APP_JS, INDEX_HTML

LOG = logging.getLogger("xmaxxing")

# Loopback only, never 0.0.0.0. This server drives an authenticated X session
# and can send DMs from the operator's account; anything that can reach it can
# drive that.
BIND_HOST = "127.0.0.1"


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    state = None            # set by make_server
    server_version = "xmaxxing"

    def log_message(self, fmt: str, *args) -> None:  # quieter than the default
        LOG.debug("http %s", fmt % args)

    # -- helpers ---------------------------------------------------------

    @property
    def origin(self) -> str:
        host = self.headers.get("Host") or f"{BIND_HOST}:{self.server.server_port}"
        return f"http://{host}"

    def _token_ok(self, query_token: str | None = None) -> bool:
        supplied = self.headers.get("X-Xmaxxing-Token")
        if not supplied and query_token:
            supplied = query_token
        return bool(supplied) and supplied == self.state.token

    def _is_cross_origin(self) -> bool:
        """A cross-origin caller can send a simple request without a preflight.

        That means any web page you visit could POST /api/start and make this
        tool scrape or send from your account. We reject anything whose Origin
        is not us, and require a custom header (which forces a preflight the
        browser then refuses) on every mutating call.
        """
        origin = self.headers.get("Origin")
        if origin is None:
            return False          # same-origin GET, curl, or a non-browser client
        return origin.rstrip("/") != self.origin.rstrip("/")

    def _send(self, code: int, body: bytes, content_type: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        # Lock the page down: no external anything, and no framing.
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:",
        )
        self.send_header("Referrer-Policy", "no-referrer")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code: int, payload: dict) -> None:
        self._send(code, json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _deny(self, why: str) -> None:
        self._json(403, {"error": why})

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            raise ValueError("content-type must be application/json")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    # -- routing ---------------------------------------------------------

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        query = (parse_qs(parsed.query).get("t") or [None])[0]
        route = parsed.path

        if route == "/api/health":
            self._json(200, {"ok": True})
            return
        if route in ("/", "/index.html", "/app.js", "/app.css"):
            if not self._token_ok(query):
                # Don't leak the dashboard to anything that guessed the port.
                self._send(403, b"token required", "text/plain; charset=utf-8")
                return
            token = query or ""
            if route == "/":
                # Embed the server's own token, not just what arrived in the
                # query: the caller may have authenticated via the header, in
                # which case `query` is empty and the page would come up with a
                # blank token and fail every API call.
                self._send(200, INDEX_HTML.replace("__TOKEN__", self.state.token).encode("utf-8"),
                           "text/html; charset=utf-8")
            elif route == "/app.js":
                self._send(200, APP_JS.encode("utf-8"), "application/javascript; charset=utf-8")
            else:
                self._send(200, APP_CSS.encode("utf-8"), "text/css; charset=utf-8")
            return
        if route == "/api/state":
            if not self._token_ok():
                self._deny("bad or missing token")
                return
            self._json(200, self.state.snapshot())
            return
        if route == "/api/events":
            if not self._token_ok():
                self._deny("bad or missing token")
                return
            self._stream()
            return
        self._json(404, {"error": "not found"})

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if self._is_cross_origin():
            self._deny("cross-origin requests are refused")
            return
        if not self._token_ok():
            self._deny("bad or missing token")
            return
        try:
            payload = self._read_json()
        except ValueError as error:
            self._json(400, {"error": str(error)})
            return

        if parsed.path == "/api/start":
            ok, message = self.state.start(payload)
            self._json(200 if ok else 409, {"ok": ok, "message": message, "run": self.state.run.as_dict()})
            return
        if parsed.path == "/api/stop":
            ok, message = self.state.stop()
            self._json(200 if ok else 409, {"ok": ok, "message": message, "run": self.state.run.as_dict()})
            return
        if parsed.path == "/api/actions/resolve":
            ok, message = self.state.resolve(
                str(payload.get("key") or ""),
                str(payload.get("status") or ""),
                str(payload.get("note") or ""),
                str(payload.get("sent_url") or ""),
            )
            self._json(200 if ok else 404, {"ok": ok, "message": message})
            return
        self._json(404, {"error": "not found"})

    # -- SSE -------------------------------------------------------------

    def _stream(self) -> None:
        channel = self.state.subscribe()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        try:
            # Tell the page the stream is live, then keep it open.
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            while True:
                try:
                    event = channel.get(timeout=15)
                    self.wfile.write(events_module.EventBus.format_sse(event))
                    self.wfile.flush()
                    if event.get("type") == "done":
                        break
                except Exception:
                    # Periodic comment doubles as a keep-alive and a disconnect probe.
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.state.unsubscribe(channel)
            self.close_connection = True


def make_server(state, port: int = 0):
    handler = type("_BoundHandler", (_Handler,), {"state": state})
    server = ThreadingHTTPServer((BIND_HOST, port), handler)
    server.daemon_threads = True
    return server


def serve(state, port: int = 0, open_browser: bool = True) -> None:
    server = make_server(state, port)
    bound = server.server_address[1]
    url = f"http://{BIND_HOST}:{bound}/?t={state.token}"
    # flush: stdout is block-buffered when redirected, and the URL is the one
    # line a caller needs to read out of a pipe.
    print(f"dashboard on {url}", flush=True)
    print("  loopback only; the token in the URL is what keeps other pages out.", flush=True)
    print("  press Ctrl+C to stop.", flush=True)
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
    finally:
        server.shutdown()
        server.server_close()
        # Give the worker a moment to notice the stop flag.
        for _ in range(20):
            if not state.busy:
                break
            time.sleep(0.1)