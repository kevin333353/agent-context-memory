#!/usr/bin/env python3
"""Record hook events and launch one non-blocking fill-table worker when due."""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from scripts import context_memory_journal as journal
    from scripts import fill_table_worker
    from scripts.context_memory_runtime import (
        exclusive_lock,
        load_config,
        managed_python,
    )
except ImportError:
    import context_memory_journal as journal
    import fill_table_worker
    from context_memory_runtime import exclusive_lock, load_config, managed_python


def _parse_utc(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def append_diagnostic(tool_root: Path, memory_root: Path | None, message: str) -> None:
    if memory_root:
        path = memory_root / "diagnostics.log"
    else:
        path = tool_root / "logs" / "hook-diagnostics.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_message = " ".join(str(message).replace("\x00", "").splitlines())[:2000]
    line = f"{datetime.now(timezone.utc).isoformat()} {safe_message}\n"
    existing = path.read_text(encoding="utf-8", errors="replace").splitlines() if path.exists() else []
    path.write_text("\n".join((existing + [line.rstrip()])[-200:]) + "\n", encoding="utf-8")


def _launch_detached(tool_root: Path, memory_root: Path, adapter: str) -> bool:
    python_path = managed_python(tool_root) or Path(sys.executable)
    script_path = tool_root / "scripts" / "context_memory_dispatch.py"
    if not python_path.is_file() or not script_path.is_file():
        return False
    command = [
        str(python_path),
        str(script_path),
        "run-worker",
        "--memory-root",
        str(memory_root),
        "--adapter",
        adapter,
        "--tool-root",
        str(tool_root),
    ]
    kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "cwd": str(memory_root.parent),
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
        )
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(command, **kwargs)
    return True


def record_and_maybe_dispatch(
    memory_root: Path,
    adapter: str,
    event: dict,
    config: dict,
    tool_root: Path,
    launch_worker=None,
) -> dict:
    memory_root = memory_root.resolve()
    db_path = memory_root / "events.sqlite"
    event_id = journal.append_event(db_path, event, config)
    result = {
        "journaled": True,
        "event_id": event_id,
        "dispatch_due": False,
        "worker_started": False,
        "dispatch_reason": "below_threshold",
    }

    fill_config = config.get("fill_table", {}) or {}
    worker_config = fill_config.get("worker", {}) or {}
    if not bool(worker_config.get("auto_run", False)):
        result["dispatch_reason"] = "auto_run_disabled"
        return result

    threshold = max(1, int(fill_config.get("summary_interval_turns") or 3))
    unprocessed = journal.read_unprocessed_events(db_path, threshold)
    force_compact = str(event.get("event") or "") == "post_compact"
    due = force_compact or len(unprocessed) >= threshold
    result["dispatch_due"] = due
    if not due:
        return result
    result["dispatch_reason"] = "post_compact" if force_compact else "threshold"

    if os.environ.get("CONTEXT_MEMORY_DISABLE_WORKER_DISPATCH") == "1":
        result["dispatch_reason"] = "disabled_env"
        return result

    state = journal.get_worker_state(db_path)
    cooldown = max(0, int(fill_config.get("retry_cooldown_seconds") or 0))
    last_attempt = _parse_utc(str(state.get("last_attempt_utc") or ""))
    if state.get("last_status") in {"running", "failed"} and last_attempt and cooldown:
        elapsed = (datetime.now(timezone.utc) - last_attempt).total_seconds()
        if elapsed < cooldown:
            result["dispatch_reason"] = "cooldown"
            return result

    launch = launch_worker or _launch_detached
    try:
        result["worker_started"] = bool(launch(tool_root, memory_root, adapter))
        if not result["worker_started"]:
            result["dispatch_reason"] = "runtime_unavailable"
    except Exception as exc:
        result["dispatch_reason"] = "launch_failed"
        append_diagnostic(tool_root, memory_root, f"worker launch failed: {exc}")
    return result


def run_worker_locked(memory_root: Path, adapter: str, tool_root: Path) -> dict:
    try:
        with exclusive_lock(memory_root / "worker.lock", timeout_seconds=0.1):
            return fill_table_worker.run_worker(
                memory_root.parent, adapter, live=True, apply=True
            )
    except TimeoutError:
        return {"status": "locked", "memory_root": str(memory_root)}
    except Exception as exc:
        append_diagnostic(tool_root, memory_root, f"worker failed: {exc}")
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    record_parser = subparsers.add_parser("record-and-dispatch")
    record_parser.add_argument("--memory-root", required=True)
    record_parser.add_argument("--adapter", required=True)
    record_parser.add_argument("--tool-root", required=True)
    record_parser.add_argument("--event-b64", required=True)

    worker_parser = subparsers.add_parser("run-worker")
    worker_parser.add_argument("--memory-root", required=True)
    worker_parser.add_argument("--adapter", required=True)
    worker_parser.add_argument("--tool-root", required=True)

    args = parser.parse_args()
    memory_root = Path(args.memory_root)
    tool_root = Path(args.tool_root)
    if args.command == "record-and-dispatch":
        event = json.loads(base64.b64decode(args.event_b64).decode("utf-8"))
        config = load_config(memory_root / "config.yaml")
        result = record_and_maybe_dispatch(
            memory_root, args.adapter, event, config, tool_root
        )
    else:
        result = run_worker_locked(memory_root, args.adapter, tool_root)
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
