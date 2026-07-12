#!/usr/bin/env python3
"""Structured runtime helpers shared by context-memory hooks and workers."""

from __future__ import annotations

import copy
import argparse
import json
import os
import shutil
import subprocess
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import yaml

try:
    from scripts.context_memory_state import validate_state_yaml
    from scripts.context_memory_session_guard import load_state as load_guard_state
    from scripts.context_memory_session_guard import save_state as save_guard_state
except ImportError:
    from context_memory_state import validate_state_yaml
    from context_memory_session_guard import load_state as load_guard_state
    from context_memory_session_guard import save_state as save_guard_state


DEFAULT_CONFIG = {
    "schema_version": 3,
    "auto_init": {
        "enabled": True,
        "update_gitignore": True,
        "exclude_temp_roots": True,
    },
    "fill_table": {
        "summary_interval_turns": 3,
        "inject_token_limit": 2000,
        "backup_limit": 5,
        "retry_cooldown_seconds": 300,
        "worker": {
            "auto_run": True,
            "status": "managed",
            "note": "Hooks launch the managed background worker after the event threshold.",
        },
        "journal": {
            "enabled": True,
            "capture_prompts": True,
            "store_full_payload": False,
            "max_prompt_chars": 8000,
            "max_event_age_days": 7,
            "max_event_count": 500,
        },
    },
    "single_session_guard": {
        "enabled": False,
        "threshold_tokens": 40000,
        "min_growth_after_compact_tokens": 10000,
        "block_on_threshold": True,
        "auto_compact_window_tokens": 100000,
    },
}


STATE_TEMPLATE = """schema_version: 1
last_updated: ""
project:
  name: ""
  root: ""
  goal: ""
current_focus:
  task: ""
  status: ""
  next_step: ""
stable_context: []
dynamic_context: []
open_questions: []
decisions: []
files: []
next_actions: []
"""


GITIGNORE_BLOCK = """# context-memory: shared files
!.context-memory/
!.context-memory/schema.yaml
!.context-memory/config.yaml
!.context-memory/project.yaml
!.context-memory/handoff/
!.context-memory/handoff/*.md
# context-memory: local files
.context-memory/state.yaml
.context-memory/history.md
.context-memory/last-compact.md
.context-memory/events.sqlite
.context-memory/metadata.json
.context-memory/diagnostics.log
.context-memory/single-session-guard.json
.claude/settings.local.json
.context-memory/*.lock
.context-memory/*.tmp
.context-memory/*.bak-*
"""


def default_config() -> dict:
    return copy.deepcopy(DEFAULT_CONFIG)


def _deep_merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_config(path: Path) -> dict:
    if not path.exists():
        return default_config()
    with path.open("r", encoding="utf-8-sig") as handle:
        parsed = yaml.safe_load(handle) or {}
    if not isinstance(parsed, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return _deep_merge(DEFAULT_CONFIG, parsed)


def migrate_config_file(path: Path) -> dict:
    if path.exists():
        with path.open("r", encoding="utf-8-sig") as handle:
            parsed = yaml.safe_load(handle) or {}
        if not isinstance(parsed, dict):
            raise ValueError(f"{path} must contain a YAML mapping")
    else:
        parsed = {}
    old_version = int(parsed.get("schema_version") or 1)
    migrated = _deep_merge(DEFAULT_CONFIG, parsed)
    migrated["schema_version"] = 3
    old_worker = parsed.get("fill_table", {}).get("worker", {}) or {}
    if old_version < 2 and old_worker.get("status") == "not_installed":
        migrated["fill_table"]["worker"].update(DEFAULT_CONFIG["fill_table"]["worker"])
    serialized = yaml.safe_dump(migrated, allow_unicode=True, sort_keys=False)
    existing = path.read_text(encoding="utf-8-sig") if path.exists() else ""
    if existing != serialized:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(path.name + ".tmp")
        temp_path.write_text(serialized, encoding="utf-8")
        os.replace(temp_path, path)
    return migrated


def _write_yaml_atomic(path: Path, value: dict) -> None:
    serialized = yaml.safe_dump(value, allow_unicode=True, sort_keys=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(path.name + ".tmp")
    temp_path.write_text(serialized, encoding="utf-8")
    os.replace(temp_path, path)


def _read_json_object(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8-sig") or "{}")
    except (OSError, ValueError) as exc:
        raise ValueError(f"{path} must contain valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return parsed


def _write_json_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(path.name + ".tmp")
    temp_path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temp_path, path)


def configure_single_session(
    project_root: Path, tool_root: Path, action: str, threshold_tokens: int
) -> dict:
    project_root = project_root.resolve()
    memory_root = project_root / ".context-memory"
    if action == "enable":
        memory_root = initialize_memory(
            project_root, tool_root, update_gitignore=True, origin="manual"
        )
    elif not memory_root.is_dir():
        raise ValueError(f"Context memory is not initialized: {memory_root}")

    config_path = memory_root / "config.yaml"
    config = migrate_config_file(config_path)
    guard_config = config["single_session_guard"]
    state_path = memory_root / "single-session-guard.json"
    state = load_guard_state(state_path)
    settings_path = project_root / ".claude" / "settings.local.json"
    settings = _read_json_object(settings_path)
    managed_value = int(guard_config.get("auto_compact_window_tokens") or 100000)

    if action == "enable":
        guard_config["enabled"] = True
        guard_config["threshold_tokens"] = max(1, int(threshold_tokens))
        ownership = state.get("settings_ownership") or {}
        if not bool(ownership.get("managed")):
            ownership = {
                "managed": True,
                "managed_value": managed_value,
                "previous_existed": "autoCompactWindow" in settings,
                "previous_value": settings.get("autoCompactWindow"),
            }
        state["settings_ownership"] = ownership
        settings["autoCompactWindow"] = managed_value
        _write_yaml_atomic(config_path, config)
        _write_json_atomic(settings_path, settings)
        save_guard_state(state_path, state)
        result_action = "enabled"
        settings_restored = False
        settings_preserved = False
    elif action == "disable":
        guard_config["enabled"] = False
        ownership = state.get("settings_ownership") or {}
        current = settings.get("autoCompactWindow")
        managed = ownership.get("managed_value", managed_value)
        settings_restored = False
        settings_preserved = False
        if bool(ownership.get("managed")):
            if current == managed:
                if bool(ownership.get("previous_existed")):
                    settings["autoCompactWindow"] = ownership.get("previous_value")
                else:
                    settings.pop("autoCompactWindow", None)
                _write_json_atomic(settings_path, settings)
                settings_restored = True
            else:
                settings_preserved = True
        state["settings_ownership"] = {}
        _write_yaml_atomic(config_path, config)
        save_guard_state(state_path, state)
        result_action = "disabled"
    elif action == "status":
        result_action = "status"
        settings_restored = False
        settings_preserved = False
    else:
        raise ValueError(f"Unknown single-session action: {action}")

    baseline = state.get("post_compact_baseline_tokens")
    threshold = int(guard_config.get("threshold_tokens") or 40000)
    growth = int(guard_config.get("min_growth_after_compact_tokens") or 10000)
    effective = max(threshold, int(baseline) + growth) if baseline is not None else threshold
    ownership = state.get("settings_ownership") or {}
    return {
        "action": result_action,
        "project_root": str(project_root),
        "memory_root": str(memory_root),
        "enabled": bool(guard_config.get("enabled", False)),
        "threshold_tokens": threshold,
        "post_compact_baseline_tokens": baseline,
        "last_observed_tokens": state.get("last_observed_tokens"),
        "effective_threshold": effective,
        "auto_compact_window_tokens": managed_value,
        "auto_compact_managed": bool(ownership.get("managed"))
        and settings.get("autoCompactWindow") == managed_value,
        "environment_override": bool(os.environ.get("CLAUDE_CODE_AUTO_COMPACT_WINDOW")),
        "settings_restored": settings_restored,
        "settings_preserved": settings_preserved,
        "settings_path": str(settings_path),
    }


def find_git_root(cwd: Path) -> Path | None:
    try:
        resolved = cwd.resolve()
    except OSError:
        return None
    if not resolved.is_dir():
        return None
    proc = subprocess.run(
        ["git", "-C", str(resolved), "rev-parse", "--show-toplevel"],
        text=True,
        capture_output=True,
        encoding="utf-8",
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    return Path(proc.stdout.strip()).resolve()


def find_memory_root(cwd: Path) -> Path | None:
    try:
        current = cwd.resolve()
    except OSError:
        return None
    while True:
        candidate = current / ".context-memory"
        if (candidate / "state.yaml").is_file():
            return candidate
        if current.parent == current:
            return None
        current = current.parent


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def is_auto_init_eligible(
    cwd: Path, tool_root: Path, config: dict
) -> tuple[bool, Path | None, str]:
    auto_config = config.get("auto_init", {})
    if not bool(auto_config.get("enabled", True)):
        return False, None, "disabled_config"

    git_root = find_git_root(cwd)
    if git_root is None:
        return False, None, "not_git"
    if git_root == tool_root.resolve():
        return False, git_root, "tool_repo"
    if git_root == Path.home().resolve():
        return False, git_root, "user_profile"
    if bool(auto_config.get("exclude_temp_roots", True)):
        temp_root = Path(tempfile.gettempdir()).resolve()
        if _is_relative_to(git_root, temp_root):
            return False, git_root, "temp_root"
    if (git_root / ".context-memory-disabled").exists():
        return False, git_root, "disabled_marker"
    return True, git_root, "eligible"


@contextmanager
def exclusive_lock(
    path: Path, timeout_seconds: float = 5.0, stale_after_seconds: float = 600.0
) -> Iterator[None]:
    deadline = time.monotonic() + timeout_seconds
    descriptor = None
    path.parent.mkdir(parents=True, exist_ok=True)
    while descriptor is None:
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(descriptor, str(os.getpid()).encode("ascii"))
        except FileExistsError:
            try:
                age = time.time() - path.stat().st_mtime
                if age > stale_after_seconds:
                    path.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Timed out waiting for lock {path}")
            time.sleep(0.05)
    try:
        yield
    finally:
        if descriptor is not None:
            os.close(descriptor)
        path.unlink(missing_ok=True)


def _write_if_missing(path: Path, text: str) -> None:
    if not path.exists():
        path.write_text(text, encoding="utf-8")


def _copy_if_missing(source: Path, target: Path) -> None:
    if source.exists() and not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def ensure_gitignore(project_root: Path) -> None:
    path = project_root / ".gitignore"
    existing = path.read_text(encoding="utf-8-sig") if path.exists() else ""
    if "!.context-memory/schema.yaml" in existing:
        required = (
            ".context-memory/single-session-guard.json",
            ".claude/settings.local.json",
        )
        existing_lines = {line.strip() for line in existing.splitlines()}
        missing = [line for line in required if line not in existing_lines]
        if missing:
            path.write_text(
                existing.rstrip() + "\n" + "\n".join(missing) + "\n",
                encoding="utf-8",
            )
        return
    prefix = existing.rstrip()
    combined = f"{prefix}\n\n{GITIGNORE_BLOCK}" if prefix else GITIGNORE_BLOCK
    path.write_text(combined.rstrip() + "\n", encoding="utf-8")


def initialize_memory(
    project_root: Path,
    tool_root: Path,
    update_gitignore: bool,
    origin: str,
) -> Path:
    project_root = project_root.resolve()
    memory_root = project_root / ".context-memory"
    lock_path = memory_root / "init.lock"
    with exclusive_lock(lock_path):
        memory_root.mkdir(parents=True, exist_ok=True)
        template_root = tool_root / "templates" / ".context-memory"
        for relative in (
            Path("schema.yaml"),
            Path("config.yaml"),
            Path("project.yaml"),
            Path("handoff") / "README.md",
        ):
            _copy_if_missing(template_root / relative, memory_root / relative)

        migrate_config_file(memory_root / "config.yaml")

        _write_if_missing(memory_root / "state.yaml", STATE_TEMPLATE)
        _write_if_missing(memory_root / "history.md", "# Context Memory History\n")

        project_path = memory_root / "project.yaml"
        if project_path.exists():
            project_data = yaml.safe_load(project_path.read_text(encoding="utf-8-sig")) or {}
            if isinstance(project_data, dict):
                project = project_data.setdefault("project", {})
                if isinstance(project, dict) and not project.get("name"):
                    project["name"] = project_root.name
                    project_path.write_text(
                        yaml.safe_dump(project_data, allow_unicode=True, sort_keys=False),
                        encoding="utf-8",
                    )

        metadata_path = memory_root / "metadata.json"
        if not metadata_path.exists():
            metadata_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "initialization_origin": origin,
                        "initialized_at_utc": datetime.now(timezone.utc).isoformat(),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        if update_gitignore:
            ensure_gitignore(project_root)
    return memory_root


def managed_python(tool_root: Path) -> Path | None:
    candidates = [
        tool_root / ".venv" / "Scripts" / "python.exe",
        tool_root / ".venv" / "bin" / "python",
    ]
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def resolve_journal_path(memory_root: Path, config: dict) -> Path:
    value = str(
        config.get("fill_table", {})
        .get("journal", {})
        .get("path", ".context-memory/events.sqlite")
    )
    configured = Path(value)
    if configured.is_absolute():
        return configured.resolve()
    return (memory_root.parent / configured).resolve()


def read_valid_state(memory_root: Path) -> dict:
    config = load_config(memory_root / "config.yaml")
    token_limit = int(config.get("fill_table", {}).get("inject_token_limit") or 2000)
    state_path = memory_root / "state.yaml"
    try:
        state_text = state_path.read_text(encoding="utf-8-sig")
        validate_state_yaml(state_text, token_limit)
        return {"valid": True, "state_text": state_text, "error": ""}
    except (OSError, ValueError) as exc:
        return {"valid": False, "state_text": "", "error": str(exc)}


def auto_initialize(cwd: Path, tool_root: Path) -> dict:
    existing = find_memory_root(cwd)
    if existing:
        return {
            "initialized": False,
            "memory_root": str(existing),
            "reason": "existing",
        }
    config = load_config(tool_root / "config.yaml")
    if os.environ.get("CONTEXT_MEMORY_ALLOW_TEMP_AUTO_INIT") == "1":
        config["auto_init"]["exclude_temp_roots"] = False
    eligible, project_root, reason = is_auto_init_eligible(cwd, tool_root, config)
    if not eligible or project_root is None:
        return {"initialized": False, "memory_root": None, "reason": reason}
    memory_root = initialize_memory(
        project_root,
        tool_root,
        update_gitignore=bool(config["auto_init"].get("update_gitignore", True)),
        origin="hook_auto",
    )
    return {
        "initialized": True,
        "memory_root": str(memory_root),
        "reason": "initialized",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    auto_parser = subparsers.add_parser("auto-init")
    auto_parser.add_argument("--cwd", required=True)
    auto_parser.add_argument("--tool-root", required=True)
    init_parser = subparsers.add_parser("init")
    init_parser.add_argument("--project-root", required=True)
    init_parser.add_argument("--tool-root", required=True)
    init_parser.add_argument("--origin", default="manual")
    init_parser.add_argument("--update-gitignore", action="store_true")
    read_parser = subparsers.add_parser("read-state")
    read_parser.add_argument("--memory-root", required=True)
    journal_parser = subparsers.add_parser("journal-path")
    journal_parser.add_argument("--memory-root", required=True)
    single_parser = subparsers.add_parser("single-session")
    single_parser.add_argument("--project-root", required=True)
    single_parser.add_argument("--tool-root", required=True)
    single_parser.add_argument("--action", choices=("enable", "status", "disable"), required=True)
    single_parser.add_argument("--threshold-tokens", type=int, default=40000)
    args = parser.parse_args()
    if args.command == "auto-init":
        result = auto_initialize(Path(args.cwd), Path(args.tool_root))
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    elif args.command == "init":
        memory_root = initialize_memory(
            Path(args.project_root),
            Path(args.tool_root),
            update_gitignore=bool(args.update_gitignore),
            origin=args.origin,
        )
        print(
            json.dumps(
                {"initialized": True, "memory_root": str(memory_root)},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
    elif args.command == "read-state":
        print(
            json.dumps(
                read_valid_state(Path(args.memory_root)),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
    elif args.command == "single-session":
        print(
            json.dumps(
                configure_single_session(
                    Path(args.project_root),
                    Path(args.tool_root),
                    args.action,
                    args.threshold_tokens,
                ),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
    else:
        memory_root = Path(args.memory_root)
        config = load_config(memory_root / "config.yaml")
        print(str(resolve_journal_path(memory_root, config)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
