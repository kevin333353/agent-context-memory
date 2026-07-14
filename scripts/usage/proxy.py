"""Loopback forwarding proxy for Claude Code, with usage measurement.

Claude Code is pointed at this server via ``ANTHROPIC_BASE_URL``. Every request is
forwarded verbatim to the upstream (``https://api.anthropic.com`` by default); the
response is streamed back to Claude Code byte-for-byte while a copy of a
``/v1/messages`` response is parsed for its ``usage`` block and written to the store.

Design rules:
- **Fail-open.** Any measurement error must not affect what Claude Code receives.
- **No content stored.** Only token counts and identifiers reach the store.
- ``/__acm/*`` is served locally (dashboard); everything else is forwarded.
- ``Accept-Encoding`` is stripped from forwarded requests so responses are identity
  encoded and parseable (loopback bandwidth cost is negligible).

Pure stdlib: http.server + http.client.
"""

from __future__ import annotations

import http.client
import os
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from . import anthropic_usage, dashboard
from .store import UsageStore

DEFAULT_UPSTREAM = "https://api.anthropic.com"
_PARSE_BUFFER_CAP = 8 * 1024 * 1024  # retain at most 8 MB for usage parsing

# Hop-by-hop headers (RFC 7230 §6.1) plus Host / Accept-Encoding handled specially.
_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class _Upstream:
    def __init__(self, url: str):
        u = urlparse(url)
        self.scheme = u.scheme or "https"
        self.host = u.hostname
        self.port = u.port or (443 if self.scheme == "https" else 80)

    def connect(self, timeout: float = 600.0) -> http.client.HTTPConnection:
        if self.scheme == "https":
            return http.client.HTTPSConnection(self.host, self.port, timeout=timeout)
        return http.client.HTTPConnection(self.host, self.port, timeout=timeout)


class UsageProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # --- BaseHTTPRequestHandler plumbing ---------------------------------
    def log_message(self, *args) -> None:  # silence default stderr logging
        pass

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_HEAD(self):
        self._dispatch("HEAD")

    def do_OPTIONS(self):
        self._dispatch("OPTIONS")

    # --- dispatch ---------------------------------------------------------
    def _dispatch(self, method: str):
        try:
            if self.path == dashboard.PREFIX or self.path.startswith(dashboard.PREFIX + "/") \
                    or self.path.split("?")[0] == dashboard.PREFIX:
                self._serve_dashboard(method)
            else:
                self._forward(method)
        except BrokenPipeError:
            self.close_connection = True
        except Exception:  # never let a handler exception crash the thread noisily
            try:
                self.send_error(502, "proxy error")
            except Exception:
                self.close_connection = True

    def _serve_dashboard(self, method: str):
        result = dashboard.handle(self.path, method, self.server.store)
        if result is None:
            self.send_error(404)
            return
        status, ctype, body = result
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        if method != "HEAD":
            self.wfile.write(body)

    # --- forwarding -------------------------------------------------------
    def _read_request_body(self):
        length = self.headers.get("Content-Length")
        if length is None:
            return None
        try:
            return self.rfile.read(int(length))
        except (ValueError, OSError):
            return None

    def _forward_headers(self) -> list[tuple[str, str]]:
        up = self.server.upstream
        out = []
        for key in self.headers.keys():
            lk = key.lower()
            if lk in _HOP_BY_HOP or lk in ("host", "accept-encoding", "content-length"):
                continue
            for val in self.headers.get_all(key, []):
                out.append((key, val))
        out.append(("Host", up.host))
        out.append(("Accept-Encoding", "identity"))
        return out

    def _forward(self, method: str):
        up = self.server.upstream
        body = self._read_request_body()
        started = datetime.now(timezone.utc)

        try:
            conn = up.connect()
            conn.putrequest(method, self.path, skip_host=True, skip_accept_encoding=True)
            for k, v in self._forward_headers():
                conn.putheader(k, v)
            if body is not None:
                conn.putheader("Content-Length", str(len(body)))
            conn.endheaders()
            if body:
                conn.send(body)
            resp = conn.getresponse()
        except Exception:
            # Upstream unreachable / failed before a response: fail open with 502.
            self.send_error(502, "upstream unavailable")
            self._record_error(method, started)
            return

        try:
            self._relay_response(method, resp, started)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _relay_response(self, method: str, resp, started):
        ctype = resp.getheader("Content-Type", "") or ""
        has_len = resp.getheader("Content-Length") is not None
        streaming = ("text/event-stream" in ctype) or not has_len
        measure = self.path.split("?")[0] == "/v1/messages" and method == "POST"

        # Relay status + headers (minus hop-by-hop). Force Connection: close so a
        # bodyless-length streaming response is delimited by EOF.
        self.send_response(resp.status)
        for key, val in resp.getheaders():
            lk = key.lower()
            if lk in _HOP_BY_HOP or lk == "content-length":
                continue
            self.send_header(key, val)
        self.send_header("Connection", "close")
        self.close_connection = True

        parse_buf = bytearray()

        if streaming:
            self.end_headers()
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                if method != "HEAD":
                    self.wfile.write(chunk)
                    self.wfile.flush()
                if measure and len(parse_buf) < _PARSE_BUFFER_CAP:
                    parse_buf.extend(chunk)
        else:
            payload = resp.read()
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if method != "HEAD":
                self.wfile.write(payload)
            if measure:
                parse_buf.extend(payload[:_PARSE_BUFFER_CAP])

        if measure:
            self._record_usage(bytes(parse_buf), ctype, started)

    # --- measurement (fail-open) -----------------------------------------
    def _latency_ms(self, started) -> int:
        return int((datetime.now(timezone.utc) - started).total_seconds() * 1000)

    def _record_usage(self, raw: bytes, ctype: str, started):
        try:
            text = raw.decode("utf-8", errors="replace")
            ts = _now_iso()
            lat = self._latency_ms(started)
            if "text/event-stream" in ctype:
                rec = anthropic_usage.record_from_sse_text(text, ts_utc=ts, latency_ms=lat)
            else:
                import json
                rec = anthropic_usage.record_from_message(
                    json.loads(text), ts_utc=ts, latency_ms=lat
                )
            if rec is not None:
                self.server.store.record(rec)
        except Exception:
            pass  # measurement failure never affects the client

    def _record_error(self, method: str, started):
        if self.path.split("?")[0] != "/v1/messages":
            return
        try:
            from .store import UsageRecord
            self.server.store.record(UsageRecord(
                ts_utc=_now_iso(), source="claude", ingest="proxy",
                latency_ms=self._latency_ms(started), status="error:upstream",
            ))
        except Exception:
            pass


class UsageProxyServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, store: UsageStore, upstream: str):
        super().__init__(addr, UsageProxyHandler)
        self.store = store
        self.upstream = _Upstream(upstream)


def make_server(host: str, port: int, store: UsageStore,
                upstream: str = DEFAULT_UPSTREAM) -> UsageProxyServer:
    return UsageProxyServer((host, port), store, upstream)
