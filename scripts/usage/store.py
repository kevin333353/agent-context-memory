"""SQLite store for normalized usage events.

Global, cross-repo database (default under the tool dir's ``usage/``). Holds only
token counts and identifiers — never prompt, response, or header content.

A ``UsageRecord`` is the single normalized shape both the Claude proxy and the
Codex log ingest write. Field meanings:

- ``input_tokens``      uncached input tokens only
- ``cache_read_tokens`` tokens served from cache (Anthropic ``cache_read_input_tokens``
                        / Codex ``cached_input_tokens``)
- ``cache_creation_tokens`` tokens written to cache (Anthropic only; 0 for Codex)
- ``output_tokens``     output tokens (includes reasoning tokens where the provider
                        folds them in)
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional


SCHEMA_VERSION = 1


@dataclass
class UsageRecord:
    ts_utc: str
    source: str                       # 'claude' | 'codex'
    ingest: str                       # 'proxy' | 'log'
    model: Optional[str] = None
    session_id: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    service_tier: Optional[str] = None
    latency_ms: Optional[int] = None
    status: str = "ok"
    # Stable idempotency key so the log tailer can re-scan a file without
    # inserting duplicates. The proxy leaves it None (every request is unique).
    dedupe_key: Optional[str] = None

    def total_input(self) -> int:
        return self.input_tokens + self.cache_creation_tokens + self.cache_read_tokens

    def cache_hit_ratio(self) -> float:
        total = self.total_input()
        return (self.cache_read_tokens / total) if total else 0.0


_CREATE = """
CREATE TABLE IF NOT EXISTS usage_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc TEXT NOT NULL,
    source TEXT NOT NULL,
    ingest TEXT NOT NULL,
    model TEXT,
    session_id TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    service_tier TEXT,
    latency_ms INTEGER,
    status TEXT NOT NULL DEFAULT 'ok',
    dedupe_key TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage_events(ts_utc);
CREATE INDEX IF NOT EXISTS idx_usage_source ON usage_events(source);
CREATE INDEX IF NOT EXISTS idx_usage_session ON usage_events(session_id);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def default_db_path() -> Path:
    """Global DB under the user's tool directory."""
    home = Path.home()
    return home / ".agent-context-memory" / "usage" / "usage.sqlite"


class UsageStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # The proxy handles each request in its own thread, so the connection is
        # shared across threads; serialize every access with _lock.
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_CREATE)
        self._conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "UsageStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def record(self, rec: UsageRecord) -> bool:
        """Insert one record. Returns True if inserted, False if the dedupe_key
        already existed (idempotent re-scan). Never raises on a duplicate."""
        cols = [
            "ts_utc", "source", "ingest", "model", "session_id",
            "input_tokens", "output_tokens", "cache_creation_tokens",
            "cache_read_tokens", "service_tier", "latency_ms", "status",
            "dedupe_key",
        ]
        placeholders = ", ".join("?" for _ in cols)
        data = asdict(rec)
        values = [data[c] for c in cols]
        with self._lock:
            try:
                self._conn.execute(
                    f"INSERT INTO usage_events ({', '.join(cols)}) VALUES ({placeholders})",
                    values,
                )
                self._conn.commit()
                return True
            except sqlite3.IntegrityError:
                # UNIQUE(dedupe_key) collision — already ingested.
                return False

    # ---- queries used by the dashboard -----------------------------------

    def _fetchall(self, sql: str, args: tuple = ()) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def _fetchone(self, sql: str, args: tuple = ()):
        with self._lock:
            return self._conn.execute(sql, args).fetchone()

    def summary(self, source: Optional[str] = None) -> dict:
        where = "WHERE source = ?" if source else ""
        args = (source,) if source else ()
        row = self._fetchone(
            f"""
            SELECT
                COUNT(*)                               AS requests,
                COALESCE(SUM(input_tokens),0)          AS input_tokens,
                COALESCE(SUM(output_tokens),0)         AS output_tokens,
                COALESCE(SUM(cache_creation_tokens),0) AS cache_creation_tokens,
                COALESCE(SUM(cache_read_tokens),0)     AS cache_read_tokens
            FROM usage_events {where}
            """,
            args,
        )
        d = dict(row)
        total_input = d["input_tokens"] + d["cache_creation_tokens"] + d["cache_read_tokens"]
        d["total_input_tokens"] = total_input
        d["cache_hit_ratio"] = (d["cache_read_tokens"] / total_input) if total_input else 0.0
        return d

    def by_source(self) -> list[dict]:
        return self._fetchall(
            """
            SELECT source,
                   COUNT(*)                              AS requests,
                   COALESCE(SUM(input_tokens),0)          AS input_tokens,
                   COALESCE(SUM(output_tokens),0)         AS output_tokens,
                   COALESCE(SUM(cache_creation_tokens),0) AS cache_creation_tokens,
                   COALESCE(SUM(cache_read_tokens),0)     AS cache_read_tokens
            FROM usage_events GROUP BY source ORDER BY source
            """
        )

    def by_model(self) -> list[dict]:
        return self._fetchall(
            """
            SELECT source, model,
                   COUNT(*)                              AS requests,
                   COALESCE(SUM(input_tokens),0)          AS input_tokens,
                   COALESCE(SUM(output_tokens),0)         AS output_tokens,
                   COALESCE(SUM(cache_creation_tokens),0) AS cache_creation_tokens,
                   COALESCE(SUM(cache_read_tokens),0)     AS cache_read_tokens
            FROM usage_events GROUP BY source, model ORDER BY requests DESC
            """
        )

    def recent(self, limit: int = 100, offset: int = 0) -> list[dict]:
        return self._fetchall(
            """
            SELECT id, ts_utc, source, ingest, model, session_id,
                   input_tokens, output_tokens, cache_creation_tokens,
                   cache_read_tokens, service_tier, latency_ms, status
            FROM usage_events ORDER BY id DESC LIMIT ? OFFSET ?
            """,
            (int(limit), int(offset)),
        )

    def count(self) -> int:
        return int(self._fetchone("SELECT COUNT(*) FROM usage_events")[0])

    # ---- tailer offset bookkeeping ---------------------------------------

    def get_meta(self, key: str) -> Optional[str]:
        row = self._fetchone("SELECT value FROM meta WHERE key = ?", (key,))
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            self._conn.commit()
