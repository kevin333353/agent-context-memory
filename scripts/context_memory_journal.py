#!/usr/bin/env python3
"""Persist bounded, redacted context-memory hook events and worker state."""

from __future__ import annotations

import argparse
import base64
import json
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from scripts.context_memory_runtime import default_config
except ImportError:
    from context_memory_runtime import default_config


WORKER_FIELDS = {
    "last_processed_event_id",
    "last_run_utc",
    "last_status",
    "last_error",
    "last_model",
    "last_attempt_utc",
}


REDACTION_RULES = (
    re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?"
        r"-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(r"(?im)(authorization\s*:\s*(?:bearer|basic)\s+)\S+"),
    re.compile(
        r"(?im)(\b(?:[A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD|PASSWD|PWD)|password)"
        r"\s*[:=]\s*[\"']?)([^\s\"']+)"
    ),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
)


def truncate(value: str | None, limit: int) -> str:
    if not value:
        return ""
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n...[truncated {len(value) - limit} chars]"


def redact_sensitive_text(value: str | None) -> tuple[str, int]:
    text = value or ""
    total = 0

    text, count = REDACTION_RULES[0].subn("[REDACTED PRIVATE KEY]", text)
    total += count
    text, count = REDACTION_RULES[1].subn(r"\1[REDACTED]", text)
    total += count
    text, count = REDACTION_RULES[2].subn(r"\1[REDACTED]", text)
    total += count
    text, count = REDACTION_RULES[3].subn("[REDACTED API KEY]", text)
    total += count
    return text, total


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def _ensure_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
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
          payload_json TEXT NOT NULL,
          redaction_count INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(events)")
    }
    if "redaction_count" not in columns:
        connection.execute(
            "ALTER TABLE events ADD COLUMN redaction_count INTEGER NOT NULL DEFAULT 0"
        )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS worker_state (
          singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
          last_processed_event_id INTEGER NOT NULL DEFAULT 0,
          last_run_utc TEXT NOT NULL DEFAULT '',
          last_status TEXT NOT NULL DEFAULT 'never_run',
          last_error TEXT NOT NULL DEFAULT '',
          last_model TEXT NOT NULL DEFAULT '',
          last_attempt_utc TEXT NOT NULL DEFAULT ''
        )
        """
    )
    connection.execute(
        "INSERT OR IGNORE INTO worker_state(singleton) VALUES (1)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_events_event_id ON events(event, id)"
    )


def _journal_config(config: dict) -> dict:
    return config.get("fill_table", {}).get("journal", {}) or {}


def _prune_events(connection: sqlite3.Connection, config: dict) -> None:
    journal_config = _journal_config(config)
    cursor = int(
        connection.execute(
            "SELECT last_processed_event_id FROM worker_state WHERE singleton = 1"
        ).fetchone()[0]
    )

    max_age_days = int(journal_config.get("max_event_age_days") or 0)
    if max_age_days > 0 and cursor > 0:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
        connection.execute(
            "DELETE FROM events WHERE id <= ? AND ts_utc < ?", (cursor, cutoff)
        )

    max_count = int(journal_config.get("max_event_count") or 0)
    if max_count > 0 and cursor > 0:
        count = int(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0])
        excess = max(0, count - max_count)
        if excess:
            connection.execute(
                """
                DELETE FROM events WHERE id IN (
                  SELECT id FROM events
                  WHERE id <= ?
                  ORDER BY id ASC
                  LIMIT ?
                )
                """,
                (cursor, excess),
            )


def append_event(db_path: Path, event: dict, config: dict) -> int:
    journal_config = _journal_config(config)
    max_prompt_chars = int(journal_config.get("max_prompt_chars") or 8000)
    capture_prompts = bool(journal_config.get("capture_prompts", True))

    raw_prompt = str(event.get("prompt") or "") if capture_prompts else ""
    prompt, prompt_redactions = redact_sensitive_text(raw_prompt)
    summary, summary_redactions = redact_sensitive_text(str(event.get("summary") or ""))
    prompt = truncate(prompt, max_prompt_chars)
    summary = truncate(summary, max_prompt_chars)

    payload_json = ""
    payload_redactions = 0
    if bool(journal_config.get("store_full_payload", False)):
        raw_payload = json.dumps(event.get("payload") or {}, ensure_ascii=False)
        payload_json, payload_redactions = redact_sensitive_text(raw_payload)

    connection = _connect(db_path)
    try:
        _ensure_schema(connection)
        cursor = connection.execute(
            """
            INSERT INTO events (
              ts_utc, protocol, adapter, event, framework_event, action,
              cwd, prompt, summary, payload_json, redaction_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                prompt_redactions + summary_redactions + payload_redactions,
            ),
        )
        event_id = int(cursor.lastrowid)
        _prune_events(connection, config)
        connection.commit()
        return event_id
    finally:
        connection.close()


def get_worker_state(db_path: Path) -> dict:
    connection = _connect(db_path)
    try:
        _ensure_schema(connection)
        row = connection.execute(
            "SELECT * FROM worker_state WHERE singleton = 1"
        ).fetchone()
        connection.commit()
        return dict(row)
    finally:
        connection.close()


def update_worker_state(db_path: Path, **fields) -> None:
    invalid = set(fields) - WORKER_FIELDS
    if invalid:
        raise ValueError(f"Unknown worker state fields: {', '.join(sorted(invalid))}")
    if not fields:
        return
    assignments = ", ".join(f"{key} = ?" for key in fields)
    values = list(fields.values())
    connection = _connect(db_path)
    try:
        _ensure_schema(connection)
        connection.execute(
            f"UPDATE worker_state SET {assignments} WHERE singleton = 1", values
        )
        connection.commit()
    finally:
        connection.close()


def read_unprocessed_events(db_path: Path, limit: int) -> list[dict]:
    connection = _connect(db_path)
    try:
        _ensure_schema(connection)
        cursor = int(
            connection.execute(
                "SELECT last_processed_event_id FROM worker_state WHERE singleton = 1"
            ).fetchone()[0]
        )
        rows = connection.execute(
            """
            SELECT id, ts_utc, adapter, event, framework_event, action,
                   cwd, prompt, summary, redaction_count
            FROM events
            WHERE id > ?
            ORDER BY id ASC
            LIMIT ?
            """,
            (cursor, max(1, int(limit))),
        ).fetchall()
        connection.commit()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def _config_from_event(event: dict) -> dict:
    config = default_config()
    journal_config = config["fill_table"]["journal"]
    for key in (
        "capture_prompts",
        "store_full_payload",
        "max_prompt_chars",
        "max_event_age_days",
        "max_event_count",
    ):
        if key in event:
            journal_config[key] = event[key]
    return config


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--event-b64")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()

    if args.status:
        print(
            json.dumps(
                get_worker_state(Path(args.db)),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return 0

    if args.event_b64:
        raw = base64.b64decode(args.event_b64).decode("utf-8")
    else:
        raw = sys.stdin.read()
    if not raw.strip():
        return 0

    event = json.loads(raw)
    event_id = append_event(Path(args.db), event, _config_from_event(event))
    print(json.dumps({"event_id": event_id}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
