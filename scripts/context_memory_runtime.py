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
except ImportError:
    from context_memory_state import validate_state_yaml


DEFAULT_CONFIG = {
    "schema_version": 2,
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
    migrated["schema_version"] = 2
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
    else:
        memory_root = Path(args.memory_root)
        config = load_config(memory_root / "config.yaml")
        print(str(resolve_journal_path(memory_root, config)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
