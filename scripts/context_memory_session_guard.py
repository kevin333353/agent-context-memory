#!/usr/bin/env python3
"""Claude Code single-session token guard state and transcript inspection."""

from __future__ import annotations

import argparse
import base64
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import yaml


STATE_VERSION = 1


def default_state() -> dict:
    return {
        "schema_version": STATE_VERSION,
        "transcript": "",
        "compact_offset": 0,
        "post_compact_baseline_tokens": None,
        "last_observed_tokens": None,
        "pre_compact_observed_tokens": None,
        "settings_ownership": {},
    }


def load_state(path: Path) -> dict:
    if not path.is_file():
        return default_state()
    try:
        parsed = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError):
        return default_state()
    if not isinstance(parsed, dict) or parsed.get("schema_version") != STATE_VERSION:
        return default_state()
    state = default_state()
    state.update(parsed)
    return state


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, ensure_ascii=False, indent=2) + "\n"
    temp_path = path.with_name(path.name + ".tmp")
    temp_path.write_text(payload, encoding="utf-8")
    os.replace(temp_path, path)


def _usage_tokens(usage: dict) -> int | None:
    keys = (
        "input_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    )
    if not any(key in usage for key in keys):
        return None
    try:
        return sum(int(usage.get(key) or 0) for key in keys)
    except (TypeError, ValueError):
        return None


def latest_provider_usage(transcript: Path, after_offset: int = 0) -> dict | None:
    if not transcript.is_file():
        return None
    seen: set[str] = set()
    latest = None
    try:
        with transcript.open("rb") as handle:
            if after_offset > 0:
                handle.seek(after_offset)
            while True:
                start = handle.tell()
                raw = handle.readline()
                if not raw:
                    break
                end = handle.tell()
                try:
                    record = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, ValueError, TypeError):
                    continue
                if not isinstance(record, dict):
                    continue
                message = record.get("message")
                usage = message.get("usage") if isinstance(message, dict) else None
                if not isinstance(usage, dict):
                    continue
                tokens = _usage_tokens(usage)
                if tokens is None:
                    continue
                request_id = record.get("requestId")
                key = str(request_id) if request_id else f"{start}:{record.get('timestamp')}"
                if key in seen:
                    continue
                seen.add(key)
                latest = {
                    "tokens": tokens,
                    "request_id": str(request_id or ""),
                    "offset": start,
                    "end_offset": end,
                }
    except OSError:
        return None
    return latest


def _result(reason: str, state: dict, **values) -> dict:
    baseline = state.get("post_compact_baseline_tokens")
    threshold = values.pop("threshold", 0)
    growth = values.pop("growth", 0)
    effective = max(threshold, int(baseline or 0) + growth if baseline is not None else threshold)
    return {
        "enabled": reason != "disabled",
        "should_block": False,
        "reason": reason,
        "observed_tokens": state.get("last_observed_tokens"),
        "effective_threshold": effective,
        "compact_offset": int(state.get("compact_offset") or 0),
        "baseline_tokens": baseline,
        **values,
    }


def evaluate_guard(
    transcript: Path, state_path: Path, config: dict, prompt: str
) -> dict:
    state = load_state(state_path)
    threshold = max(1, int(config.get("threshold_tokens") or 40000))
    growth = max(0, int(config.get("min_growth_after_compact_tokens") or 10000))
    if not bool(config.get("enabled", False)):
        return _result("disabled", state, threshold=threshold, growth=growth)
    if str(prompt or "").lstrip().lower().startswith("/compact"):
        return _result("compact_command", state, threshold=threshold, growth=growth)
    if not transcript.is_file():
        return _result("missing_transcript", state, threshold=threshold, growth=growth)

    transcript_text = str(transcript.resolve())
    if state.get("transcript") != transcript_text:
        ownership = state.get("settings_ownership") or {}
        state = default_state()
        state["settings_ownership"] = ownership
        state["transcript"] = transcript_text

    compact_offset = int(state.get("compact_offset") or 0)
    latest = latest_provider_usage(transcript, after_offset=compact_offset)
    if latest is None:
        save_state(state_path, state)
        return _result("missing_usage", state, threshold=threshold, growth=growth)

    observed = int(latest["tokens"])
    intervention = None
    if compact_offset > 0 and state.get("post_compact_baseline_tokens") is None:
        state["post_compact_baseline_tokens"] = observed
        pre = state.get("pre_compact_observed_tokens")
        # Emit a measured before/after pair exactly once, when the tool-forced
        # compaction actually shrank the running context.
        if pre is not None and int(pre) > observed:
            intervention = {
                "kind": "compact",
                "before_tokens": int(pre),
                "after_tokens": observed,
                "compact_offset": compact_offset,
            }
    state["last_observed_tokens"] = observed
    save_state(state_path, state)

    result = _result("below_threshold", state, threshold=threshold, growth=growth)
    if intervention is not None:
        result["intervention"] = intervention
    should_block = bool(config.get("block_on_threshold", True)) and observed >= int(
        result["effective_threshold"]
    )
    if should_block:
        result["should_block"] = True
        result["reason"] = "threshold"
    return result


def mark_compact_boundary(transcript: Path, state_path: Path, event: str) -> dict:
    prev = load_state(state_path)
    ownership = prev.get("settings_ownership") or {}
    # Carry the last observed context size across the reset so the first
    # post-compact observation can measure the drop. A user-initiated /clear is
    # not the tool saving anything, so it starts no intervention pair.
    pre_observed = None
    if str(event).lower() != "clear":
        # Prefer a fresh observation; otherwise keep a value already carried by
        # an earlier boundary (Claude Code fires PreCompact then PostCompact, so
        # the second call sees last_observed already reset to None).
        pre_observed = prev.get("last_observed_tokens")
        if pre_observed is None:
            pre_observed = prev.get("pre_compact_observed_tokens")
    state = default_state()
    state["settings_ownership"] = ownership
    state["pre_compact_observed_tokens"] = pre_observed
    try:
        state["transcript"] = str(transcript.resolve())
        state["compact_offset"] = transcript.stat().st_size if transcript.is_file() else 0
    except OSError:
        state["transcript"] = str(transcript)
        state["compact_offset"] = 0
    save_state(state_path, state)
    return state


def resolve_usage_db_path() -> Path:
    """The usage DB the proxy/dashboard read: ``<ToolRoot>/usage/usage.sqlite``.

    Derived from this module's location (``<ToolRoot>/scripts/…``) so it matches
    the CLI's proxy ``--db`` in every install layout — including a tool installed
    into a repo directory instead of the home default, where the store's
    home-based ``default_db_path()`` would point at a different, unread DB.
    """
    return Path(__file__).resolve().parent.parent / "usage" / "usage.sqlite"


def record_intervention_to_store(
    memory_root, source: str, transcript_path: str, intervention: dict, db_path=None
) -> bool:
    """Best-effort: persist a measured compaction to the global usage store.

    Never raises — measurement must never break the guard. Deduped on
    ``transcript_path`` + ``compact_offset`` so a re-run of the same prompt
    records the same compaction only once. Returns True only when a new row was
    inserted.
    """
    try:
        try:
            from usage.store import (  # running as a script: scripts/ on sys.path
                UsageStore, UsageIntervention,
            )
        except ImportError:
            from scripts.usage.store import (  # imported as scripts.usage
                UsageStore, UsageIntervention,
            )
        offset = intervention.get("compact_offset")
        rec = UsageIntervention(
            ts_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            source=source,
            kind=str(intervention.get("kind") or "compact"),
            memory_root=str(memory_root),
            before_tokens=int(intervention["before_tokens"]),
            after_tokens=int(intervention["after_tokens"]),
            dedupe_key=f"compact:{transcript_path}:{offset}",
        )
        with UsageStore(db_path or resolve_usage_db_path()) as store:
            return store.record_intervention(rec)
    except Exception:
        return False


def handle_hook_event(memory_root: Path, event: dict) -> dict:
    config_path = memory_root / "config.yaml"
    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8-sig")) or {}
    except (OSError, ValueError, TypeError, yaml.YAMLError):
        config = {}
    guard_config = config.get("single_session_guard", {}) or {}
    guard_enabled = bool(guard_config.get("enabled", False))
    framework_event = str(event.get("hook_event_name") or event.get("event") or "")
    transcript_value = str(event.get("transcript_path") or "")
    transcript = Path(transcript_value) if transcript_value else Path("__missing__")
    state_path = memory_root / "single-session-guard.json"

    if not guard_enabled:
        return {"enabled": False, "should_block": False, "reason": "disabled"}

    if framework_event == "UserPromptSubmit":
        result = evaluate_guard(
            transcript,
            state_path,
            guard_config,
            str(event.get("prompt") or ""),
        )
        intervention = result.get("intervention")
        if intervention:
            transcript_key = (
                str(transcript.resolve()) if transcript.is_file() else str(transcript)
            )
            record_intervention_to_store(
                memory_root, "claude", transcript_key, intervention
            )
        return result
    if framework_event in {"PreCompact", "PostCompact"}:
        state = mark_compact_boundary(transcript, state_path, framework_event)
        return {
            "enabled": guard_enabled,
            "should_block": False,
            "reason": framework_event.lower(),
            "compact_offset": state["compact_offset"],
        }
    if framework_event == "SessionStart" and str(event.get("source") or "") in {
        "clear",
        "compact",
    }:
        state = mark_compact_boundary(
            transcript, state_path, str(event.get("source") or "")
        )
        return {
            "enabled": guard_enabled,
            "should_block": False,
            "reason": "session_boundary",
            "compact_offset": state["compact_offset"],
        }
    return {
        "enabled": guard_enabled,
        "should_block": False,
        "reason": "unhandled_event",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--memory-root", required=True)
    parser.add_argument("--event-b64", required=True)
    args = parser.parse_args()
    event = json.loads(base64.b64decode(args.event_b64).decode("utf-8"))
    result = handle_hook_event(Path(args.memory_root), event)
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
