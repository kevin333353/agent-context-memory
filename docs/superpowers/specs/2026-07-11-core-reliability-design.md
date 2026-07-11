# Agent Context Memory Core Reliability Design

## Goal

Make a fresh installation useful without manual project setup while preserving
non-blocking hooks, source-of-truth precedence, bounded memory size, and a clear
opt-out path.

The release is successful when entering an eligible git repository with Codex
or Claude Code creates a valid context-memory workspace once, records events,
updates `state.yaml` in the background after the configured threshold, and
surfaces failures through `doctor` without blocking the host agent.

## Scope

This change includes:

- safe hook-driven project initialization;
- asynchronous fill-table dispatch with locking and an event cursor;
- real configuration wiring, validation, retry, and repair-model fallback;
- prompt redaction and bounded journal retention;
- a managed Python virtual environment and installed agent skill;
- actionable diagnostics;
- corrected benchmark aggregation and claims;
- integration and Python unit tests for the new behavior.

This change does not add Linux/macOS installers, a long-running service, a new
agent adapter, or a task-quality research benchmark.

## Architecture

PowerShell remains the public Windows CLI, hook launcher, and adapter layer.
Python owns structured YAML/SQLite operations and background-worker state.
This avoids ad hoc YAML parsing in Windows PowerShell 5 while keeping existing
commands and hook payloads compatible.

The installer creates `<tool-root>/.venv`, installs the pinned dependencies in
`requirements.txt`, and installs the context-memory skill into the user skill
directories for enabled agents. Runtime scripts prefer the managed Python
executable and fall back to `python` only for source checkouts and tests.

## Hook Data Flow

1. The adapter normalizes the host payload into `context-memory/v1`.
2. The core searches upward from `cwd` for `.context-memory/state.yaml`.
3. If memory is absent, the core evaluates the auto-initialization policy.
4. The initializer creates the project files under a lock and updates
   `.gitignore` when configured.
5. The event journal redacts and stores the bounded event payload.
6. The dispatcher checks the unprocessed event count and compact trigger.
7. When work is due, the hook starts one hidden detached worker and returns.
8. The adapter emits the current state as host-specific additional context.

No model call runs synchronously on the hook path. Hook failures remain
fail-open, but they are persisted for diagnostics instead of discarded.

## Safe Auto-Initialization

Auto-initialization defaults to enabled. It runs only for `SessionStart` and
`UserPromptSubmit`, and only when all of these conditions hold:

- `cwd` resolves inside an existing git worktree;
- the git root is not the context-memory tool repository;
- the git root is not the user profile or a system temporary directory;
- the git root does not contain `.context-memory-disabled`;
- the effective global `auto_init.enabled` setting is `true`.

Initialization targets the git root, never an arbitrary nested directory. A
project lock prevents concurrent hooks from creating the files twice. The same
hook invocation then journals the event and injects the newly created state.

`auto_init.update_gitignore` defaults to `true`. Auto-init writes an origin
record so `status` and `doctor` can distinguish `manual` from `hook_auto`.
Users can opt out before first use by creating `.context-memory-disabled`, or
globally by setting `auto_init.enabled: false` in the tool configuration.

## Worker Scheduling And State

SQLite gains a singleton `worker_state` record containing:

- `last_processed_event_id`;
- `last_run_utc`;
- `last_status`;
- `last_error`;
- `last_model`;
- `last_attempt_utc`.

The dispatcher starts a worker when the count of events after the cursor reaches
`summary_interval_turns`. `PostCompact` forces dispatch regardless of count.
A project worker lock prevents concurrent Claude and Codex workers. A failed
run leaves the cursor unchanged and observes a configurable retry cooldown so a
bad provider response cannot spend money on every prompt.

The worker reads only events after the cursor, up to the configured batch
limit. A valid `no_change` result advances the cursor. A successful state write
also advances it. Parsing, validation, provider, or write failures preserve the
old state and cursor and update the diagnostic record.

## Model Retry Policy

The configured routine model receives the normal fill-table prompt. If its
output cannot be parsed or validated, the worker:

1. retries the same model once when `retry_same_model_once` is enabled, adding
   the exact validation error to a bounded repair prompt;
2. calls the configured repair model once when
   `fallback_on_invalid_yaml` is enabled and the repair model differs from the
   routine model;
3. records the final failure without modifying state.

Every attempt is subject to the adapter budget settings already represented in
configuration where the host CLI supports a budget flag.

## State Validation And Writes

Validation parses YAML with `yaml.safe_load` and enforces:

- a mapping at the document root;
- `schema_version: 1`;
- all required top-level keys;
- mappings for `project` and `current_focus`;
- lists for context, question, decision, file, and action fields;
- a string `last_updated` value;
- the effective `inject_token_limit` after serialization;
- rejection of YAML tags or non-serializable values.

The writer creates a timestamped backup, writes UTF-8 to a temporary file in
the same directory, flushes it, and atomically replaces `state.yaml`. It retains
only the configured number of backups.

The CLI `validate` command delegates structured state validation to the same
Python code used by the worker, ensuring CLI and runtime behavior cannot drift.

## Privacy And Retention

The journal keeps prompts because the fill-table worker needs recent user intent,
but applies redaction before SQLite insertion. Redaction covers common API-key,
authorization-header, password-assignment, and private-key patterns. The event
still records that redaction occurred.

Configuration controls prompt capture, maximum prompt characters, maximum event
age, and maximum event count. Defaults are prompt capture enabled, 8,000
characters, seven days, and 500 events. Pruning runs after insertion and never
removes events newer than the worker cursor until they have been processed.
Full hook payload storage remains disabled by default.

## Installation And Agent Integration

The installer:

- verifies a usable Python interpreter;
- creates or refreshes `<tool-root>/.venv`;
- installs pinned dependencies from `requirements.txt`;
- installs hooks for the selected agents;
- copies the managed context-memory skill into enabled user skill directories;
- reports every initialized project, or explicitly reports that zero projects
  were initialized and that hook auto-init will handle the first eligible repo.

Codex `SessionStart` matches `startup|resume|clear|compact`. Hook definitions
remain user-level so they can auto-initialize newly opened repositories. The
installer and uninstaller only replace or remove entries they own.

## Diagnostics

Hook errors are appended to `.context-memory/diagnostics.log` when a project is
known, otherwise to `<tool-root>/logs/hook-diagnostics.log`. Logs are bounded
and contain no prompt text or environment dump.

`doctor` checks:

- managed Python and PyYAML availability;
- hook presence and current Codex trust-visible definition;
- project initialization and origin;
- valid structured YAML and injection size;
- journal write/read health;
- worker cursor, last status, last model, and last error;
- stale locks and disabled auto-init markers.

`doctor` must distinguish a skipped, uninitialized, degraded, and healthy setup.

## Benchmark Corrections

Transcript replay state resets for every transcript. Reports show per-session
results before any aggregate. The memory comparison is labeled an offline
upper-bound replay estimate, not measured product savings. Actual provider
usage remains separate from replay estimates.

README and benchmark documentation stop presenting `96.42%` or `98.57%` as
quality-preserving measured savings. They explicitly state that the current
suite measures input-token pressure only and does not prove task success after
compression.

## Error Handling

- Hook launch, initialization, journaling, and dispatch failures never block a
  user prompt or agent session.
- Initialization uses a lock and idempotent writes.
- Worker failures never replace valid state or advance the cursor.
- A missing managed environment is visible in diagnostics and causes worker
  dispatch to skip rather than repeatedly spawning a broken process.
- Invalid host input produces no injection and a bounded diagnostic entry.

## Testing

Python tests use the standard-library `unittest` runner so no test framework is
required. PowerShell integration tests continue to exercise the public CLI and
all adapters.

Required coverage includes:

- first hook in an eligible repo auto-initializes and injects;
- nested-directory hooks initialize the git root and find the correct journal;
- non-git, disabled, profile, temp, and tool roots do not auto-initialize;
- concurrent initialization is idempotent;
- prompts are redacted and retention is bounded;
- event cursors prevent duplicate processing;
- threshold and `PostCompact` dispatch behavior;
- routine retry, repair fallback, and cursor preservation on failure;
- invalid YAML types and oversized state are rejected;
- state replacement is atomic and backup retention works;
- `doctor` reports managed-environment and worker failures;
- Codex hook matchers cover every documented session source;
- multi-transcript replay resets state between sessions.

The final verification command runs Python unit tests followed by the existing
PowerShell protocol suite from a clean temporary project.
