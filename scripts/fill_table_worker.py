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
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

try:
    from scripts.context_memory_state import (
        approx_tokens,
        atomic_write_state,
        validate_state_yaml,
    )
except ImportError:
    from context_memory_state import approx_tokens, atomic_write_state, validate_state_yaml

try:
    from scripts import context_memory_journal as journal
    from scripts.context_memory_runtime import migrate_config_file
except ImportError:
    import context_memory_journal as journal
    from context_memory_runtime import migrate_config_file

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


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
    env["CONTEXT_MEMORY_WORKER_CHILD"] = "1"
    if not env.get("CONTEXT_MEMORY_USE_ANTHROPIC_API_KEY"):
        env.pop("ANTHROPIC_API_KEY", None)
        env.pop("CLAUDE_API_KEY", None)
    proc = subprocess.run(cmd, cwd=str(cwd), text=True, capture_output=True, encoding="utf-8", env=env)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"claude exited {proc.returncode}")
    return unwrap_claude_stdout(proc.stdout), " ".join(cmd[:-1] + ["<prompt>"])


def run_codex(
    prompt: str, model: str, cwd: Path, reasoning_effort: str | None = None
) -> tuple[str, str]:
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
    if reasoning_effort:
        cmd[4:4] = ["-c", f'model_reasoning_effort="{reasoning_effort}"']
    try:
        env = os.environ.copy()
        env["CONTEXT_MEMORY_WORKER_CHILD"] = "1"
        proc = subprocess.run(
            cmd,
            input=prompt,
            text=True,
            capture_output=True,
            encoding="utf-8",
            env=env,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"codex exited {proc.returncode}")
        return output_path.read_text(encoding="utf-8"), " ".join(cmd)
    finally:
        output_path.unlink(missing_ok=True)


def invoke_configured_model(
    adapter: str, model: str, prompt: str, adapter_config: dict, cwd: Path
) -> tuple[str, str]:
    if adapter == "claude-code":
        return run_claude(prompt, model, adapter_config.get("max_budget_usd"), cwd)
    return run_codex(prompt, model, cwd, adapter_config.get("reasoning_effort"))


def _resolve_journal_path(memory_root: Path, config: dict) -> Path:
    value = str(
        get_nested(
            config,
            ["fill_table", "journal", "path"],
            ".context-memory/events.sqlite",
        )
    )
    configured = Path(value)
    if configured.is_absolute():
        return configured.resolve()
    return (memory_root.parent / configured).resolve()


def _parse_model_result(output_text: str, token_limit: int) -> tuple[dict, str | None]:
    model_json = extract_json_object(output_text)
    if bool(model_json.get("no_change")):
        return model_json, None
    state_yaml = model_json.get("state_yaml")
    if not isinstance(state_yaml, str) or not state_yaml.strip():
        raise ValueError("model JSON missing non-empty state_yaml")
    validate_state_yaml(state_yaml, token_limit)
    return model_json, state_yaml


def _repair_prompt(prompt: str, error: Exception) -> str:
    detail = str(error).strip()[:1200]
    return (
        prompt
        + "\n\n<PREVIOUS_OUTPUT_ERROR>\n"
        + detail
        + "\n</PREVIOUS_OUTPUT_ERROR>\n"
        + "Return a corrected JSON object that follows every rule."
    )


def run_worker(
    cwd: Path,
    adapter: str,
    live: bool,
    apply: bool,
    invoke_model=None,
    limit: int = 50,
    model_override: str | None = None,
) -> dict:
    cwd = Path(cwd)
    memory_root = find_memory_root(cwd)
    config = migrate_config_file(memory_root / "config.yaml")
    state_path = memory_root / "state.yaml"
    state_text = read_text(state_path)
    schema_text = read_text(memory_root / "schema.yaml")
    journal_path = _resolve_journal_path(memory_root, config)
    events = journal.read_unprocessed_events(journal_path, limit)

    adapter_config = (
        get_nested(config, ["fill_table", "adapters", adapter], {}) or {}
    )
    routine_model = model_override or adapter_config.get("routine_model")
    if not routine_model:
        raise ValueError(f"No routine model configured for adapter {adapter}")

    prompt = build_prompt(state_text, schema_text, events)
    report = {
        "mode": "live" if live else "dry-run",
        "adapter": adapter,
        "model": routine_model,
        "memory_root": str(memory_root),
        "journal_path": str(journal_path),
        "events": events,
        "prompt_chars": len(prompt),
        "prompt_token_estimate": approx_tokens(prompt),
        "apply": bool(apply),
    }
    if not live:
        report.update(
            {
                "status": "dry_run",
                "prompt_preview": prompt[:2000],
                "written": None,
            }
        )
        return report
    if not events:
        report.update({"status": "no_events", "written": None})
        return report

    invoke = invoke_model or invoke_configured_model
    token_limit = int(
        get_nested(config, ["fill_table", "inject_token_limit"], 2000)
    )
    validation_config = (
        get_nested(config, ["fill_table", "validation"], {}) or {}
    )
    attempts = [routine_model]
    if bool(validation_config.get("retry_same_model_once", True)):
        attempts.append(routine_model)
    repair_model = adapter_config.get("repair_model")
    if (
        bool(validation_config.get("fallback_on_invalid_yaml", True))
        and repair_model
        and repair_model != routine_model
    ):
        attempts.append(repair_model)

    last_error = None
    model_json = None
    state_yaml = None
    command_preview = ""
    used_model = routine_model
    attempt_prompt = prompt
    attempted_models = []
    timestamp = datetime.now(timezone.utc).isoformat()
    journal.update_worker_state(
        journal_path,
        last_attempt_utc=timestamp,
        last_status="running",
        last_error="",
    )
    for model in attempts:
        used_model = model
        attempted_models.append(model)
        try:
            output_text, command_preview = invoke(
                adapter, model, attempt_prompt, adapter_config, memory_root.parent
            )
            model_json, state_yaml = _parse_model_result(output_text, token_limit)
            last_error = None
            break
        except Exception as exc:
            last_error = exc
            attempt_prompt = _repair_prompt(prompt, exc)

    if last_error is not None or model_json is None:
        error_text = str(last_error or "model returned no result")[:2000]
        journal.update_worker_state(
            journal_path,
            last_run_utc=datetime.now(timezone.utc).isoformat(),
            last_status="failed",
            last_error=error_text,
            last_model=used_model,
        )
        raise ValueError(error_text) from last_error

    event_id = int(events[-1]["id"])
    no_change = state_yaml is None
    status = "no_change" if no_change else ("updated" if apply else "preview")
    backup_path = None
    written = None
    if apply and state_yaml:
        backup_limit = int(
            get_nested(config, ["fill_table", "backup_limit"], 5)
        )
        backup_path = atomic_write_state(
            state_path, state_yaml.rstrip() + "\n", backup_limit
        )
        written = str(state_path)

    if apply:
        journal.update_worker_state(
            journal_path,
            last_processed_event_id=event_id,
            last_run_utc=datetime.now(timezone.utc).isoformat(),
            last_status=status,
            last_error="",
            last_model=used_model,
        )
    else:
        journal.update_worker_state(
            journal_path,
            last_run_utc=datetime.now(timezone.utc).isoformat(),
            last_status=status,
            last_error="",
            last_model=used_model,
        )

    report.update(
        {
            "status": status,
            "command_preview": command_preview,
            "attempted_models": attempted_models,
            "model_notes": model_json.get("notes", []),
            "no_change": no_change,
            "state_yaml_chars": len(state_yaml) if state_yaml else 0,
            "valid_state_yaml": not no_change,
            "backup_path": str(backup_path) if backup_path else None,
            "written": written,
        }
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cwd", default=os.getcwd())
    parser.add_argument("--adapter", choices=["claude-code", "codex-cli"], default="claude-code")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--model")
    args = parser.parse_args()

    report = run_worker(
        Path(args.cwd),
        args.adapter,
        live=args.live,
        apply=args.apply,
        limit=args.limit,
        model_override=args.model,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
