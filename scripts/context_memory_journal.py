#!/usr/bin/env python3
"""Append context-memory hook events to a local SQLite journal."""

from __future__ import annotations

import argparse
import base64
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


def truncate(value: str | None, limit: int) -> str:
    if not value:
        return ""
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n...[truncated {len(value) - limit} chars]"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--event-b64")
    args = parser.parse_args()

    if args.event_b64:
        raw = base64.b64decode(args.event_b64).decode("utf-8")
    else:
        raw = sys.stdin.read()
    if not raw.strip():
        return 0

    event = json.loads(raw)
    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    max_prompt_chars = int(event.get("max_prompt_chars") or 8000)
    prompt = truncate(event.get("prompt"), max_prompt_chars)
    summary = truncate(event.get("summary"), max_prompt_chars)

    store_full_payload = bool(event.get("store_full_payload"))
    payload_json = json.dumps(event.get("payload") or {}, ensure_ascii=False) if store_full_payload else ""

    with sqlite3.connect(db_path) as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              ts_utc TEXT NOT NULL,
              protocol TEXT NOT NULL,
              adapter TEXT NOT NULL,
              event TEXT NOT NULL,
              framework_event TEXT NOT NULL,
              action TEXT NOT NULL,
              cwd TEXT NOT NULL,
              prompt TEXT NOT NULL,
              summary TEXT NOT NULL,
              payload_json TEXT NOT NULL
            )
            """
        )
        con.execute(
            """
            INSERT INTO events (
              ts_utc, protocol, adapter, event, framework_event, action,
              cwd, prompt, summary, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                "context-memory/v1",
                str(event.get("adapter") or ""),
                str(event.get("event") or ""),
                str(event.get("framework_event") or ""),
                str(event.get("action") or ""),
                str(event.get("cwd") or ""),
                prompt,
                summary,
                payload_json,
            ),
        )
        con.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_events_event_id
            ON events(event, id)
            """
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
