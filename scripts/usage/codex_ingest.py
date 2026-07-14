"""Ingest Codex CLI usage from local rollout logs.

Codex writes one JSONL rollout per session under
``~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl``. Per-turn token usage arrives as::

    {"type": "event_msg",
     "payload": {"type": "token_count",
                 "info": {"last_token_usage": {"input_tokens": .., "cached_input_tokens": ..,
                                               "output_tokens": .., ...}}}}

``input_tokens`` there is the *total* input including the cached portion (same
convention as the OpenAI API), so the normalized record stores::

    input_tokens        = last.input_tokens - last.cached_input_tokens   (uncached only)
    cache_read_tokens   = last.cached_input_tokens
    cache_creation_tokens = 0        (Codex/ChatGPT has no cache-creation concept)
    output_tokens       = last.output_tokens

Pure stdlib. Idempotent: each record carries a stable ``dedupe_key`` so a file can
be re-scanned without inserting duplicates.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator, Optional

from .store import UsageRecord, UsageStore


def default_sessions_root() -> Path:
    return Path.home() / ".codex" / "sessions"


def _extract_model(obj: dict) -> Optional[str]:
    """Best-effort model lookup from any rollout event."""
    payload = obj.get("payload")
    if isinstance(payload, dict):
        for key in ("model", "model_slug"):
            if isinstance(payload.get(key), str):
                return payload[key]
        info = payload.get("info")
        if isinstance(info, dict) and isinstance(info.get("model"), str):
            return info["model"]
        # turn_context / session_meta may nest it
        for sub in ("turn_context", "context"):
            node = payload.get(sub)
            if isinstance(node, dict) and isinstance(node.get("model"), str):
                return node["model"]
    if isinstance(obj.get("model"), str):
        return obj["model"]
    return None


def normalize_token_usage(last: dict) -> dict:
    """Map a Codex ``last_token_usage`` dict to normalized token fields."""
    total_input = int(last.get("input_tokens") or 0)
    cached = int(last.get("cached_input_tokens") or 0)
    # Guard against a provider that ever reports cached separately from input.
    uncached = max(total_input - cached, 0)
    return {
        "input_tokens": uncached,
        "cache_read_tokens": cached,
        "cache_creation_tokens": 0,
        "output_tokens": int(last.get("output_tokens") or 0),
    }


def session_id_from_path(path: Path) -> str:
    # rollout-2025-10-09T08-50-27-<uuid>.jsonl -> the uuid tail
    stem = path.stem
    parts = stem.split("-")
    # last 5 dash-separated groups form the uuid; fall back to the whole stem
    if len(parts) >= 5:
        return "-".join(parts[-5:])
    return stem


def iter_records_from_rollout(path: Path, start_line: int = 0) -> Iterator[tuple[int, UsageRecord]]:
    """Yield ``(line_no, UsageRecord)`` for each token_count event at or after
    ``start_line`` (0-based). ``line_no`` is the 0-based index of the line, usable
    to resume. Malformed lines are skipped without stopping the scan."""
    session_id = session_id_from_path(path)
    model: Optional[str] = None
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for idx, line in enumerate(fh):
                if idx < start_line:
                    # still track model from skipped context lines cheaply
                    continue
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except (ValueError, TypeError):
                    continue
                m = _extract_model(obj)
                if m:
                    model = m
                payload = obj.get("payload")
                if not (isinstance(payload, dict) and payload.get("type") == "token_count"):
                    continue
                info = payload.get("info") or {}
                last = info.get("last_token_usage")
                if not isinstance(last, dict):
                    continue
                fields = normalize_token_usage(last)
                rec = UsageRecord(
                    ts_utc=str(obj.get("timestamp") or ""),
                    source="codex",
                    ingest="log",
                    model=model,
                    session_id=session_id,
                    service_tier=None,
                    dedupe_key=f"codex:{session_id}:{idx}",
                    **fields,
                )
                yield idx, rec
    except OSError:
        return


class CodexTailer:
    """Scan the Codex sessions tree and ingest new token-usage events.

    Progress is tracked per file in the store's ``meta`` table (processed line
    count). ``dedupe_key`` provides a second safety net, so even a full re-scan
    (e.g. offsets lost) will not double-count.
    """

    def __init__(self, store: UsageStore, sessions_root: Optional[Path] = None):
        self.store = store
        self.sessions_root = Path(sessions_root) if sessions_root else default_sessions_root()

    def _meta_key(self, path: Path) -> str:
        try:
            rel = path.relative_to(self.sessions_root)
        except ValueError:
            rel = path
        return f"codex_lines:{rel.as_posix()}"

    def scan_once(self) -> int:
        """Ingest all new records. Returns the number of rows inserted."""
        inserted = 0
        if not self.sessions_root.exists():
            return 0
        for path in sorted(self.sessions_root.rglob("rollout-*.jsonl")):
            key = self._meta_key(path)
            start = int(self.store.get_meta(key) or 0)
            last_line = start - 1
            for line_no, rec in iter_records_from_rollout(path, start_line=start):
                if self.store.record(rec):
                    inserted += 1
                last_line = line_no
            if last_line >= start:
                self.store.set_meta(key, str(last_line + 1))
        return inserted
