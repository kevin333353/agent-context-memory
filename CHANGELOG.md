# Changelog

## 0.2.2 - 2026-07-12

### Fixed

- Windows installation now selects one usable Python 3.9+ executable when
  multiple `python` applications are present on `PATH`.
- Unusable Python commands, including inactive WindowsApps aliases, no longer
  prevent the installer or hook runtime fallback from checking later candidates.

## 0.2.1 - 2026-07-11

### Fixed

- Codex hooks on Windows now use `commandWindows` with a quote-free encoded
  PowerShell launcher, preventing `cmd.exe /C` from treating quoted paths as
  literal `\"...\"` executable names and reporting `SessionStart hook
  (failed)` with exit code 1.
- The Codex hook installer retains a stable managed-hook marker while safely
  supporting tool installation paths that contain spaces.

## 0.2.0 - 2026-07-11

### Added

- Safe hook-driven initialization for eligible git repositories.
- A managed Python virtual environment with pinned PyYAML.
- Detached background fill-table dispatch with SQLite cursor and locking.
- Routine-model retry and repair-model fallback.
- Structured YAML validation, token limits, atomic state replacement, and bounded backups.
- Prompt redaction, event retention, initialization metadata, and persistent diagnostics.
- Managed Claude Code and Codex context-memory skills.
- `context-memory version` and expanded `doctor` worker/runtime reporting.

### Changed

- New and migrated projects enable the managed background worker by default.
- Codex and Claude `SessionStart` hooks cover `startup`, `resume`, `clear`, and `compact`.
- Replay estimates reset at transcript boundaries and are labeled offline upper bounds.
- The installer now requires Python 3.9 or newer.

### Fixed

- Nested-directory workers now resolve the project journal from the repository root.
- Invalid hook JSON and invalid or oversized state fail open without injection.
- Invalid model output no longer overwrites state or advances the event cursor.
- The invalid cross-transcript `98.57%` replay claim has been withdrawn.

### Upgrade

The `v0.1.8` installer command is permanently pinned and cannot install this
release. Replace both occurrences of `v0.1.8` with `v0.2.0` and run the updated
command once. Existing state is preserved; config and SQLite schemas migrate
idempotently on the next hook or worker run.
