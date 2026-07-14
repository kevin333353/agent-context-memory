# Changelog

## 0.5.0 - 2026-07-14

### Added

- **Tool-attributable savings on the dashboard**, cleanly separated from
  Anthropic prompt-cache efficiency. Both compare the tool's mechanism (a compact
  `state.yaml` block) against the baseline of carrying the full running transcript:
  - **工具省下 · 估算** — offline upper-bound estimate from
    `benchmarks/simulate-token-savings.py`.
  - **工具省下 · 實測 A/B** — real provider A/B (tool on vs off, same task) from
    `benchmarks/provider-ab-benchmark.py`.
- New `savings_estimates` table in the usage store with `UsageSavings`,
  `record_savings`, `latest_savings`, `recent_savings`; `scripts/usage/savings.py`
  parses either benchmark's JSON and persists it (`python -m usage.savings
  --db <db> simulate|ab-record …`). `/api/summary` gains a `savings` block.
- Dashboard reworked to three honest meters: two tool-savings meters above plus
  the Anthropic prompt-cache meter (explicitly marked *not caused by this tool*).

### Changed

- Dropped the earlier "工具壓縮率" (forced-compaction before/after) meter from the
  dashboard: a compaction's reduction is mostly Claude Code's native mechanism,
  not this tool's marginal saving, so presenting it as tool savings overstated the
  attribution. The guard still records compaction before/after pairs to the
  `interventions` table as an internal diagnostic, but they are no longer shown as
  savings.

### Notes

- The estimate is an offline upper bound (assumption-driven); the A/B is a
  ground-truth token count on a synthetic task. Neither is a billed-cost figure.
  Example on this repo's own `state.yaml`: estimate ≈ 86%, single claude recall
  A/B ≈ 59%.

## 0.4.0 - 2026-07-14

### Added

- Real, observed usage measurement with a local dashboard (distinct from the
  existing estimated benchmarks). A loopback proxy captures Claude Code token and
  prompt-cache usage from live responses; a log tailer ingests Codex CLI usage
  from `~/.codex` rollout logs. Both normalize into one global `usage.sqlite`.
- `context-memory proxy start|stop|status|enable|disable` commands. `enable claude`
  reversibly points `ANTHROPIC_BASE_URL` at the proxy; the dashboard is served at
  `http://127.0.0.1:8788/__acm/`.
- A self-contained dashboard (embedded HTML, no external requests) showing token
  usage, cache-hit ratio, a Claude-vs-Codex comparison, per-model and per-request
  detail, and an illustrative (non-billing) list-price savings figure.
- Pure-standard-library implementation under `scripts/usage/`; no new runtime
  dependencies, covered by the stdlib `unittest` suite.

### Changed

- The Claude Code hook definition now launches PowerShell with
  `-WindowStyle Hidden -NonInteractive -NoLogo`, eliminating the console window
  that flashed on every hook event (Codex was already unaffected).

### Notes

- Claude is measured via proxy because subscription mode still returns a full
  `usage` block through `ANTHROPIC_BASE_URL`; Codex (ChatGPT subscription) cannot
  be cleanly proxied, so its usage is read from local logs instead. The proxy is
  loopback-only and intended for local self-use; any dollar figure is an
  illustrative API-price conversion, not a bill.

## 0.3.0 - 2026-07-12

### Added

- An opt-in, per-repository Claude Code single-session guard with a configurable
  provider-input threshold and compact-loop protection.
- Reversible project-local `autoCompactWindow` management with a 100k fallback.
- `PreCompact` checkpointing and `PostCompact` guard reset support.
- `context-memory single-session enable|status|disable` commands and doctor
  diagnostics for thresholds, observed tokens, and environment overrides.

### Changed

- Claude Code can block a normal prompt at the configured threshold and show an
  exact `/compact` recovery command; missing usage or checkpoint failures remain
  fail-open.
- Installed Claude guidance keeps large logs, searches, and reports in subagents
  or artifacts so the main session receives only short summaries.

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
