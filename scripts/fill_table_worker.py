#!/usr/bin/env python3
"""Dry-run or run the context-memory fill-table worker.

The worker reads recent hook events from `.context-memory/events.sqlite`, builds
a compact update prompt, and optionally calls the configured routine model. It
does not rewrite state.yaml unless `--apply` is passed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

import yaml

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


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


def read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return data


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig") if path.exists() else ""


def find_memory_root(cwd: Path) -> Path:
    current = cwd.resolve()
    while True:
        candidate = current / ".context-memory" / "state.yaml"
        if candidate.exists():
            return current / ".context-memory"
        if current.parent == current:
            raise FileNotFoundError("Could not find .context-memory/state.yaml")
        current = current.parent


def get_nested(data: dict, path: list[str], default=None):
    current = data
    for part in path:
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def read_events(db_path: Path, limit: int) -> list[dict]:
    if not db_path.exists():
        return []
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            """
            SELECT id, ts_utc, adapter, event, framework_event, action, prompt, summary
            FROM events
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in reversed(rows)]


def build_prompt(state_text: str, schema_text: str, events: list[dict]) -> str:
    event_lines = []
    for item in events:
        prompt = (item.get("prompt") or "").strip()
        summary = (item.get("summary") or "").strip()
        if prompt:
            detail = f"user_prompt={json.dumps(prompt, ensure_ascii=False)}"
        elif summary:
            detail = f"summary={json.dumps(summary, ensure_ascii=False)}"
        else:
            detail = "no_text_payload=true"
        event_lines.append(
            f"- id={item.get('id')} adapter={item.get('adapter')} "
            f"event={item.get('event')} action={item.get('action')} {detail}"
        )

    events_text = "\n".join(event_lines) if event_lines else "- no recent events"
    return f"""你是 context-memory/v1 的填表 worker。請根據既有 state.yaml 和最近 hook events，判斷是否需要更新記憶表。

規則：
- 只輸出 JSON object，不要 markdown，不要解釋。
- 若最近事件沒有持久記憶價值，輸出 {{"no_change":true,"notes":["..."]}}。
- 只有真的需要更新時，才輸出 {{"state_yaml":"<完整 YAML 字串>","notes":["..."]}}。
- state_yaml 必須保留原本 schema 欄位，不要新增大量新欄位。
- 不要貼完整 transcript；只保留可執行摘要、決策、檔案路徑、下一步。
- 測試 prompt、final check、純驗證訊息通常不需要寫入，請優先 no_change。

<SCHEMA_YAML>
{schema_text.strip()}
</SCHEMA_YAML>

<CURRENT_STATE_YAML>
{state_text.strip()}
</CURRENT_STATE_YAML>

<RECENT_EVENTS>
{events_text}
</RECENT_EVENTS>
"""


def approx_tokens(text: str) -> int:
    cjk = len(re.findall(r"[\u3400-\u9fff]", text))
    return int(cjk + (len(text) - cjk) / 4) + 1


def validate_state_yaml(state_yaml: str) -> dict:
    data = yaml.safe_load(state_yaml)
    if not isinstance(data, dict):
        raise ValueError("state_yaml is not a YAML mapping")
    missing = sorted(REQUIRED_STATE_KEYS - set(data.keys()))
    if missing:
        raise ValueError("state_yaml missing keys: " + ", ".join(missing))
    return data


def extract_json_object(text: str) -> dict:
    stripped = text.strip()
    try:
        data = json.loads(stripped)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{[\s\S]*\}", stripped)
    if not match:
        raise ValueError("model output does not contain a JSON object")
    data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("model output JSON is not an object")
    return data


def unwrap_claude_stdout(stdout: str) -> str:
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return stdout

    for key in ("result", "text", "content", "message"):
        value = data.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts = []
            for item in value:
                if isinstance(item, dict):
                    text = item.get("text") or item.get("content")
                    if isinstance(text, str):
                        parts.append(text)
                elif isinstance(item, str):
                    parts.append(item)
            if parts:
                return "\n".join(parts)
    return stdout


def run_claude(prompt: str, model: str, budget: float | None, cwd: Path) -> tuple[str, str]:
    cmd = [
        "claude",
        "--safe-mode",
        "-p",
        "--model",
        model,
        "--output-format",
        "json",
        "--no-session-persistence",
        "--permission-mode",
        "dontAsk",
        "--tools",
        "",
    ]
    if budget:
        cmd.extend(["--max-budget-usd", str(budget)])
    cmd.append(prompt)
    env = os.environ.copy()
    if not env.get("CONTEXT_MEMORY_USE_ANTHROPIC_API_KEY"):
        env.pop("ANTHROPIC_API_KEY", None)
        env.pop("CLAUDE_API_KEY", None)
    proc = subprocess.run(cmd, cwd=str(cwd), text=True, capture_output=True, encoding="utf-8", env=env)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"claude exited {proc.returncode}")
    return unwrap_claude_stdout(proc.stdout), " ".join(cmd[:-1] + ["<prompt>"])


def run_codex(prompt: str, model: str, cwd: Path) -> tuple[str, str]:
    with NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".txt") as tmp:
        output_path = Path(tmp.name)
    cmd = [
        "codex",
        "exec",
        "-m",
        model,
        "--cd",
        str(cwd),
        "--skip-git-repo-check",
        "--ephemeral",
        "--output-last-message",
        str(output_path),
        "-",
    ]
    try:
        proc = subprocess.run(cmd, input=prompt, text=True, capture_output=True, encoding="utf-8")
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"codex exited {proc.returncode}")
        return output_path.read_text(encoding="utf-8"), " ".join(cmd)
    finally:
        output_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cwd", default=os.getcwd())
    parser.add_argument("--adapter", choices=["claude-code", "codex-cli"], default="claude-code")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--model")
    args = parser.parse_args()

    cwd = Path(args.cwd)
    memory_root = find_memory_root(cwd)
    config = read_yaml(memory_root / "config.yaml")
    state_path = memory_root / "state.yaml"
    schema_path = memory_root / "schema.yaml"
    state_text = read_text(state_path)
    schema_text = read_text(schema_path)

    journal_path_value = get_nested(config, ["fill_table", "journal", "path"], ".context-memory/events.sqlite")
    journal_path = (cwd / journal_path_value).resolve()
    events = read_events(journal_path, args.limit)

    adapter_config = get_nested(config, ["fill_table", "adapters", args.adapter], {}) or {}
    model = args.model or adapter_config.get("routine_model")
    if not model:
        raise ValueError(f"No routine model configured for adapter {args.adapter}")

    prompt = build_prompt(state_text, schema_text, events)
    report = {
        "mode": "live" if args.live else "dry-run",
        "adapter": args.adapter,
        "model": model,
        "memory_root": str(memory_root),
        "journal_path": str(journal_path),
        "events": events,
        "prompt_chars": len(prompt),
        "prompt_token_estimate": approx_tokens(prompt),
        "apply": bool(args.apply),
    }

    if not args.live:
        report["prompt_preview"] = prompt[:2000]
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if args.adapter == "claude-code":
        budget = adapter_config.get("max_budget_usd")
        output_text, command_preview = run_claude(prompt, model, budget, cwd)
    else:
        output_text, command_preview = run_codex(prompt, model, cwd)

    model_json = extract_json_object(output_text)
    no_change = bool(model_json.get("no_change"))
    state_yaml = model_json.get("state_yaml")
    if no_change:
        state_yaml = None
    else:
        if not isinstance(state_yaml, str) or not state_yaml.strip():
            raise ValueError("model JSON missing non-empty state_yaml")
        validate_state_yaml(state_yaml)

    report["command_preview"] = command_preview
    report["model_notes"] = model_json.get("notes", [])
    report["no_change"] = no_change
    report["state_yaml_chars"] = len(state_yaml) if state_yaml else 0
    report["valid_state_yaml"] = (not no_change)

    if args.apply and state_yaml:
        backup_path = state_path.with_suffix(".yaml.bak-" + datetime.now().strftime("%Y%m%d%H%M%S"))
        shutil.copy2(state_path, backup_path)
        state_path.write_text(state_yaml.rstrip() + "\n", encoding="utf-8")
        report["backup_path"] = str(backup_path)
        report["written"] = str(state_path)
    else:
        report["written"] = None

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
