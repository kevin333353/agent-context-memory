#!/usr/bin/env python3
"""Validate and safely replace context-memory state files."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml


REQUIRED_STATE_KEYS = {
    "schema_version",
    "last_updated",
    "project",
    "current_focus",
    "stable_context",
    "dynamic_context",
    "open_questions",
    "decisions",
    "files",
    "next_actions",
}

LIST_KEYS = (
    "stable_context",
    "dynamic_context",
    "open_questions",
    "decisions",
    "files",
    "next_actions",
)


def approx_tokens(text: str) -> int:
    cjk = len(re.findall(r"[\u3400-\u9fff]", text))
    return int(math.ceil(cjk + (len(text) - cjk) / 4.0))


def validate_state_yaml(text: str, token_limit: int) -> dict:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"state.yaml contains invalid YAML: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("state.yaml must be a mapping")
    missing = sorted(REQUIRED_STATE_KEYS - set(data))
    if missing:
        raise ValueError("state.yaml missing keys: " + ", ".join(missing))
    if data.get("schema_version") != 1:
        raise ValueError("schema_version must equal 1")
    if not isinstance(data.get("last_updated"), str):
        raise ValueError("last_updated must be a string")
    for key in ("project", "current_focus"):
        if not isinstance(data.get(key), dict):
            raise ValueError(f"{key} must be a mapping")
    for key in LIST_KEYS:
        if not isinstance(data.get(key), list):
            raise ValueError(f"{key} must be a list")

    actual_tokens = approx_tokens(text)
    if token_limit > 0 and actual_tokens > token_limit:
        raise ValueError(
            f"state.yaml exceeds token limit {token_limit}: about {actual_tokens} tokens"
        )
    return data


def _prune_backups(path: Path, backup_limit: int) -> None:
    backups = sorted(
        path.parent.glob(path.name + ".bak-*"),
        key=lambda item: (item.stat().st_mtime_ns, item.name),
        reverse=True,
    )
    for stale in backups[max(0, backup_limit) :]:
        stale.unlink(missing_ok=True)


def atomic_write_state(
    path: Path, text: str, backup_limit: int = 5
) -> Path | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    backup_path = None
    if path.exists() and backup_limit > 0:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup_path = path.with_name(path.name + ".bak-" + stamp)
        shutil.copy2(path, backup_path)

    descriptor, temp_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=path.parent
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)

    _prune_backups(path, backup_limit)
    return backup_path


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--path", required=True)
    validate_parser.add_argument("--token-limit", type=int, default=2000)
    args = parser.parse_args()

    if args.command == "validate":
        path = Path(args.path)
        try:
            text = path.read_text(encoding="utf-8-sig")
            validate_state_yaml(text, args.token_limit)
            result = {
                "valid": True,
                "tokens": approx_tokens(text),
                "error": "",
            }
            exit_code = 0
        except (OSError, ValueError) as exc:
            result = {"valid": False, "tokens": 0, "error": str(exc)}
            exit_code = 1
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return exit_code
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
