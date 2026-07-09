#!/usr/bin/env python3
"""Claude Code usage and context-memory comparison report.

Reads Claude Code JSONL transcripts and summarizes actual usage metadata.
Also estimates how small the injected context-memory block is compared with
the latest request's input-side context pressure.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
from pathlib import Path
from typing import Any


def token_counter():
    try:
        import tiktoken  # type: ignore

        enc = tiktoken.get_encoding("cl100k_base")
        return "tiktoken:cl100k_base", lambda text: len(enc.encode(text))
    except Exception:
        return "heuristic:mixed-cjk", estimate_tokens


def estimate_tokens(text: str) -> int:
    cjk = len(re.findall(r"[\u3400-\u9fff]", text))
    non_cjk = len(text) - cjk
    return int(math.ceil(cjk + non_cjk / 4.0))


def slug_project_path(cwd: Path) -> str:
    drive = cwd.drive.rstrip(":").replace("\\", "-").replace("/", "-")
    rest = str(cwd)[len(cwd.drive) :].strip("\\/")
    parts = [p for p in re.split(r"[\\/]+", rest) if p]
    return "--".join([drive] + parts)


def find_project_dir(cwd: Path, claude_home: Path) -> Path:
    preferred = claude_home / "projects" / slug_project_path(cwd)
    if preferred.exists():
        return preferred

    # Fallback: pick newest project dir whose name ends with the final folder.
    suffix = cwd.name.lower()
    candidates = [
        p
        for p in (claude_home / "projects").glob("*")
        if p.is_dir() and p.name.lower().endswith(suffix)
    ]
    if candidates:
        return max(candidates, key=lambda p: p.stat().st_mtime)

    raise FileNotFoundError(f"No Claude Code project dir found for {cwd}")


def iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line_no, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield line_no, json.loads(line)
            except Exception:
                continue


def get_usage(record: dict[str, Any]) -> dict[str, Any] | None:
    message = record.get("message")
    if isinstance(message, dict) and isinstance(message.get("usage"), dict):
        return message["usage"]
    return None


def unique_usage_records(transcripts: list[Path]):
    seen: set[str] = set()
    rows = []
    for path in transcripts:
        for line_no, record in iter_jsonl(path):
            usage = get_usage(record)
            if not usage:
                continue

            key = record.get("requestId") or (
                f"{path.name}:{record.get('timestamp')}:{record.get('uuid')}"
            )
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "path": str(path),
                    "line": line_no,
                    "timestamp": record.get("timestamp"),
                    "requestId": record.get("requestId"),
                    "model": (record.get("message") or {}).get("model"),
                    "usage": usage,
                }
            )
    return rows


def sum_usage(rows: list[dict[str, Any]]) -> dict[str, int]:
    total = {
        "requests": len(rows),
        "input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_1h_input_tokens": 0,
        "cache_creation_5m_input_tokens": 0,
    }

    for row in rows:
        usage = row["usage"]
        total["input_tokens"] += int(usage.get("input_tokens") or 0)
        total["cache_creation_input_tokens"] += int(
            usage.get("cache_creation_input_tokens") or 0
        )
        total["cache_read_input_tokens"] += int(usage.get("cache_read_input_tokens") or 0)
        total["output_tokens"] += int(usage.get("output_tokens") or 0)

        creation = usage.get("cache_creation") or {}
        total["cache_creation_1h_input_tokens"] += int(
            creation.get("ephemeral_1h_input_tokens") or 0
        )
        total["cache_creation_5m_input_tokens"] += int(
            creation.get("ephemeral_5m_input_tokens") or 0
        )

    return total


def usage_input_side(usage: dict[str, Any]) -> int:
    return (
        int(usage.get("input_tokens") or 0)
        + int(usage.get("cache_creation_input_tokens") or 0)
        + int(usage.get("cache_read_input_tokens") or 0)
    )


def weighted_input_equivalent(total: dict[str, int], cache_read_multiplier: float) -> float:
    # Approximate relative input cost units. This is not a bill.
    creation_1h = total["cache_creation_1h_input_tokens"]
    creation_5m = total["cache_creation_5m_input_tokens"]
    unknown_creation = max(
        0,
        total["cache_creation_input_tokens"] - creation_1h - creation_5m,
    )
    return round(
        total["input_tokens"]
        + creation_1h * 2.0
        + creation_5m * 1.25
        + unknown_creation * 1.25
        + total["cache_read_input_tokens"] * cache_read_multiplier,
        2,
    )


def extract_text(record: dict[str, Any]) -> str:
    message = record.get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif isinstance(item.get("content"), str):
                    parts.append(item["content"])
        return "\n".join(parts)
    return ""


def replay_transcript_tokens(transcripts: list[Path], count_tokens) -> dict[str, int]:
    running = ""
    total = 0
    peak = 0
    turns = 0
    for path in transcripts:
        for _, record in iter_jsonl(path):
            typ = record.get("type")
            if typ not in {"user", "assistant"}:
                continue
            text = extract_text(record)
            if not text:
                continue
            payload_tokens = count_tokens(running)
            total += payload_tokens
            peak = max(peak, payload_tokens)
            turns += 1
            running += f"\n<{typ}>\n{text}\n</{typ}>\n"
    return {"turns": turns, "baseline_replay_total_tokens": total, "baseline_replay_peak_tokens": peak}


def get_memory_context(cwd: Path) -> str:
    hook = Path.home() / ".agent-context-memory" / "context-memory-hook.ps1"
    payload = json.dumps(
        {"cwd": str(cwd), "hook_event_name": "UserPromptSubmit", "prompt": "benchmark"},
        ensure_ascii=False,
    )
    try:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(hook),
                "-Adapter",
                "claude-code",
            ],
            input=payload,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0 or not completed.stdout.strip():
            return ""
        obj = json.loads(completed.stdout)
        return obj.get("hookSpecificOutput", {}).get("additionalContext", "")
    except Exception:
        return ""


def percent(part: float, whole: float) -> float:
    if whole <= 0:
        return 0.0
    return round((part / whole) * 100.0, 2)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cwd", default=os.getcwd())
    parser.add_argument("--claude-home", default=str(Path.home() / ".claude"))
    parser.add_argument("--project-dir")
    parser.add_argument("--transcript", action="append")
    parser.add_argument("--all", action="store_true", help="include all transcripts in the project dir")
    parser.add_argument("--cache-read-multiplier", type=float, default=0.1)
    args = parser.parse_args()

    cwd = Path(args.cwd).resolve()
    claude_home = Path(args.claude_home)
    project_dir = Path(args.project_dir) if args.project_dir else find_project_dir(cwd, claude_home)

    if args.transcript:
        transcripts = [Path(p) for p in args.transcript]
    else:
        all_jsonl = sorted(project_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
        transcripts = all_jsonl if args.all else all_jsonl[-1:]

    source, count_tokens = token_counter()
    rows = unique_usage_records(transcripts)
    total = sum_usage(rows)
    input_side_total = (
        total["input_tokens"]
        + total["cache_creation_input_tokens"]
        + total["cache_read_input_tokens"]
    )
    cache_hit_share = percent(total["cache_read_input_tokens"], input_side_total)

    latest = rows[-1] if rows else None
    latest_usage = latest["usage"] if latest else {}
    latest_input_side = usage_input_side(latest_usage) if latest else 0

    memory_context = get_memory_context(cwd)
    memory_tokens = count_tokens(memory_context) if memory_context else 0
    latest_upper_saved = max(0, latest_input_side - memory_tokens)

    replay = replay_transcript_tokens(transcripts, count_tokens)
    memory_replay_total = memory_tokens * max(1, replay["turns"])
    replay_saved = max(0, replay["baseline_replay_total_tokens"] - memory_replay_total)

    result = {
        "token_counter": source,
        "inputs": {
            "cwd": str(cwd),
            "project_dir": str(project_dir),
            "transcripts": [str(p) for p in transcripts],
            "dedupe": "unique requestId where available",
            "billing_note": "Claude provider usage metadata is authoritative; replay numbers are estimates",
        },
        "actual_claude_usage": {
            **total,
            "input_side_total_tokens": input_side_total,
            "cache_hit_share_percent": cache_hit_share,
            "weighted_input_equivalent_estimate": weighted_input_equivalent(
                total, args.cache_read_multiplier
            ),
        },
        "latest_request": {
            "timestamp": latest.get("timestamp") if latest else None,
            "model": latest.get("model") if latest else None,
            "input_side_tokens": latest_input_side,
            "memory_context_tokens": memory_tokens,
            "upper_bound_replaceable_tokens": latest_upper_saved,
            "upper_bound_replaceable_percent": percent(latest_upper_saved, latest_input_side),
        },
        "transcript_replay_estimate": {
            **replay,
            "memory_replay_total_tokens": memory_replay_total,
            "replay_saved_tokens": replay_saved,
            "replay_saved_percent": percent(replay_saved, replay["baseline_replay_total_tokens"]),
        },
    }

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
