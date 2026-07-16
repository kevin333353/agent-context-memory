"""Entrypoint: run the usage proxy with a background Codex log tailer.

    python -m scripts.usage [--host H] [--port P] [--upstream URL]
                            [--db PATH] [--codex-interval SECONDS] [--no-codex]

Claude Code is pointed at ``http://HOST:PORT`` via ``ANTHROPIC_BASE_URL``; the
dashboard is at ``http://HOST:PORT/__acm/``. The Codex tailer periodically ingests
``~/.codex`` rollout logs into the same store.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time

from .store import UsageStore, default_db_path
from .codex_ingest import CodexTailer, default_sessions_root
from .proxy import make_server, DEFAULT_UPSTREAM


def _codex_loop(store: UsageStore, sessions_root, interval: float, stop: threading.Event):
    tailer = CodexTailer(store, sessions_root=sessions_root)
    # Prime immediately so the dashboard has data on first load.
    while not stop.is_set():
        try:
            tailer.scan_once()
        except Exception:
            pass  # never let ingest kill the process
        stop.wait(interval)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scripts.usage")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=int(os.environ.get("ACM_PROXY_PORT", "8788")))
    ap.add_argument("--upstream", default=os.environ.get("ACM_UPSTREAM", DEFAULT_UPSTREAM))
    ap.add_argument("--db", default=str(os.environ.get("ACM_USAGE_DB", default_db_path())))
    ap.add_argument("--codex-interval", type=float, default=15.0)
    ap.add_argument("--no-codex", action="store_true")
    args = ap.parse_args(argv)

    # When launched detached by the CLI, redirect output to a log file so the
    # process needs no console.
    log_path = os.environ.get("ACM_PROXY_LOG")
    if log_path:
        try:
            fh = open(log_path, "a", encoding="utf-8", buffering=1)
            sys.stdout = fh
            sys.stderr = fh
        except OSError:
            pass

    store = UsageStore(args.db)
    server = make_server(args.host, args.port, store, upstream=args.upstream)

    stop = threading.Event()
    codex_thread = None
    if not args.no_codex:
        codex_thread = threading.Thread(
            target=_codex_loop,
            args=(store, default_sessions_root(), args.codex_interval, stop),
            daemon=True,
        )
        codex_thread.start()

    base = f"http://{args.host}:{args.port}"
    print(f"[acm-proxy] forwarding {base} -> {args.upstream}", flush=True)
    print(f"[acm-proxy] dashboard: {base}/__acm/", flush=True)
    print(f"[acm-proxy] db: {args.db}", flush=True)
    print(f"[acm-proxy] set ANTHROPIC_BASE_URL={base} for Claude Code", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[acm-proxy] shutting down", flush=True)
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
