# Agent Context Memory Core Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver `v0.2.0` with safe hook-driven initialization, non-blocking background memory updates, real validation and diagnostics, bounded private journaling, and honest replay benchmarks.

**Architecture:** PowerShell remains the public Windows CLI and host adapter layer. Focused Python modules own YAML, SQLite, redaction, state validation, atomic writes, and worker orchestration through the tool-managed virtual environment. Hook execution stays fail-open and synchronous work is limited to local initialization, journaling, dispatch checks, and context serialization.

**Tech Stack:** Windows PowerShell 5.1, Python 3.9+, SQLite, PyYAML 6.0.2, `unittest`, Codex/Claude CLI hooks.

## Global Constraints

- Auto-initialize only eligible git roots and honor `.context-memory-disabled`.
- Never call a model synchronously from a hook.
- Never overwrite valid state or advance the event cursor after a failed worker run.
- Keep existing `context-memory/v1` adapter output compatible.
- Keep hooks fail-open while persisting bounded diagnostics without prompt text.
- Preserve pinned installer semantics; publish the repaired behavior as `v0.2.0` rather than changing `v0.1.8`.
- Use TDD for every production behavior change.

---

### Task 1: Structured Runtime Foundation

**Files:**
- Create: `requirements.txt`
- Create: `scripts/context_memory_runtime.py`
- Create: `tests/test_context_memory_runtime.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `load_config(path: Path) -> dict`
- Produces: `find_git_root(cwd: Path) -> Path | None`
- Produces: `find_memory_root(cwd: Path) -> Path | None`
- Produces: `is_auto_init_eligible(cwd, tool_root, config) -> tuple[bool, Path | None, str]`
- Produces: `initialize_memory(project_root, tool_root, update_gitignore, origin) -> Path`
- Produces: `managed_python(tool_root: Path) -> Path | None`

- [ ] **Step 1: Add failing runtime tests**

```python
def test_eligible_nested_git_directory_resolves_repo_root(self):
    repo = self.make_git_repo()
    nested = repo / "src" / "feature"
    nested.mkdir(parents=True)
    eligible, root, reason = runtime.is_auto_init_eligible(
        nested, self.tool_root, runtime.default_config()
    )
    self.assertTrue(eligible)
    self.assertEqual(root, repo.resolve())
    self.assertEqual(reason, "eligible")

def test_disabled_repo_is_not_auto_initialized(self):
    repo = self.make_git_repo()
    (repo / ".context-memory-disabled").write_text("", encoding="utf-8")
    eligible, _, reason = runtime.is_auto_init_eligible(
        repo, self.tool_root, runtime.default_config()
    )
    self.assertFalse(eligible)
    self.assertEqual(reason, "disabled_marker")
```

- [ ] **Step 2: Run tests and verify the missing-module failure**

Run: `python -m unittest tests.test_context_memory_runtime -v`

Expected: FAIL because `scripts.context_memory_runtime` does not exist.

- [ ] **Step 3: Implement config loading, eligibility, idempotent initialization, and managed-Python lookup**

```python
DEFAULT_CONFIG = {
    "auto_init": {"enabled": True, "update_gitignore": True},
    "fill_table": {"summary_interval_turns": 3, "inject_token_limit": 2000},
}

def find_git_root(cwd: Path) -> Path | None:
    proc = subprocess.run(
        ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
        text=True, capture_output=True, encoding="utf-8", check=False,
    )
    return Path(proc.stdout.strip()).resolve() if proc.returncode == 0 else None
```

Initialization must copy the repository templates, create `state.yaml`,
`history.md`, `handoff/README.md`, and `metadata.json`, and update `.gitignore`
using the existing managed block without replacing unrelated content.

- [ ] **Step 4: Run runtime tests and the existing protocol suite**

Run: `python -m unittest tests.test_context_memory_runtime -v`

Expected: PASS.

Run: `powershell -NoProfile -ExecutionPolicy Bypass -File tests/run-tests.ps1`

Expected: `context-memory protocol tests passed`.

- [ ] **Step 5: Commit the runtime foundation**

```powershell
git add requirements.txt scripts/context_memory_runtime.py tests/test_context_memory_runtime.py .gitignore
git commit -m "feat: add structured context memory runtime"
```

### Task 2: Private Journal And Worker Cursor

**Files:**
- Modify: `scripts/context_memory_journal.py`
- Modify: `scripts/context_memory_runtime.py`
- Create: `tests/test_context_memory_journal.py`

**Interfaces:**
- Produces: `redact_sensitive_text(text: str) -> tuple[str, int]`
- Produces: `append_event(db_path: Path, event: dict, config: dict) -> int`
- Produces: `get_worker_state(db_path: Path) -> dict`
- Produces: `update_worker_state(db_path: Path, **fields) -> None`
- Produces: `read_unprocessed_events(db_path: Path, limit: int) -> list[dict]`

- [ ] **Step 1: Add failing redaction, retention, and cursor tests**

```python
def test_redacts_authorization_and_api_keys_before_insert(self):
    event_id = journal.append_event(self.db, {
        "prompt": "Authorization: Bearer abc123\nOPENAI_API_KEY=sk-secret",
        "event": "user_prompt_submit",
    }, self.config)
    row = self.fetch_event(event_id)
    self.assertNotIn("abc123", row["prompt"])
    self.assertNotIn("sk-secret", row["prompt"])
    self.assertGreater(row["redaction_count"], 0)

def test_unprocessed_events_start_after_cursor(self):
    first = self.add_event("one")
    second = self.add_event("two")
    journal.update_worker_state(self.db, last_processed_event_id=first)
    self.assertEqual(
        [event["id"] for event in journal.read_unprocessed_events(self.db, 10)],
        [second],
    )
```

- [ ] **Step 2: Run tests and verify they fail on missing schema and functions**

Run: `python -m unittest tests.test_context_memory_journal -v`

Expected: FAIL because the journal has no redaction metadata or worker state.

- [ ] **Step 3: Implement schema migration, redaction, bounded pruning, and singleton worker state**

```sql
CREATE TABLE IF NOT EXISTS worker_state (
  singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
  last_processed_event_id INTEGER NOT NULL DEFAULT 0,
  last_run_utc TEXT NOT NULL DEFAULT '',
  last_status TEXT NOT NULL DEFAULT 'never_run',
  last_error TEXT NOT NULL DEFAULT '',
  last_model TEXT NOT NULL DEFAULT '',
  last_attempt_utc TEXT NOT NULL DEFAULT ''
);
```

Pruning must preserve all events after `last_processed_event_id`, even when
they exceed age or count limits.

- [ ] **Step 4: Run journal and protocol tests**

Run: `python -m unittest tests.test_context_memory_journal -v`

Expected: PASS.

Run: `powershell -NoProfile -ExecutionPolicy Bypass -File tests/run-tests.ps1`

Expected: PASS.

- [ ] **Step 5: Commit journal reliability**

```powershell
git add scripts/context_memory_journal.py scripts/context_memory_runtime.py tests/test_context_memory_journal.py
git commit -m "feat: add private journal cursor and retention"
```

### Task 3: State Validation And Atomic Writes

**Files:**
- Create: `scripts/context_memory_state.py`
- Create: `tests/test_context_memory_state.py`
- Modify: `scripts/fill_table_worker.py`

**Interfaces:**
- Produces: `validate_state_yaml(text: str, token_limit: int) -> dict`
- Produces: `approx_tokens(text: str) -> int`
- Produces: `atomic_write_state(path: Path, text: str, backup_limit: int) -> Path`

- [ ] **Step 1: Add failing state validation and replacement tests**

```python
def test_rejects_wrong_top_level_types(self):
    state = self.valid_state()
    state["next_actions"] = "not-a-list"
    with self.assertRaisesRegex(ValueError, "next_actions must be a list"):
        state_module.validate_state_yaml(yaml.safe_dump(state), 2000)

def test_rejects_state_above_token_limit(self):
    state = self.valid_state()
    state["dynamic_context"] = ["x" * 12000]
    with self.assertRaisesRegex(ValueError, "token limit"):
        state_module.validate_state_yaml(yaml.safe_dump(state), 100)
```

- [ ] **Step 2: Run tests and verify the module-missing failure**

Run: `python -m unittest tests.test_context_memory_state -v`

Expected: FAIL because `context_memory_state.py` does not exist.

- [ ] **Step 3: Implement strict type validation and same-directory atomic replacement**

```python
LIST_KEYS = (
    "stable_context", "dynamic_context", "open_questions",
    "decisions", "files", "next_actions",
)

def validate_state_yaml(text: str, token_limit: int) -> dict:
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("state.yaml must be a mapping")
    if data.get("schema_version") != 1:
        raise ValueError("schema_version must equal 1")
    for key in LIST_KEYS:
        if not isinstance(data.get(key), list):
            raise ValueError(f"{key} must be a list")
    if approx_tokens(text) > token_limit:
        raise ValueError(f"state.yaml exceeds token limit {token_limit}")
    return data
```

- [ ] **Step 4: Run state, worker dry-run, and protocol tests**

Run: `python -m unittest tests.test_context_memory_state -v`

Expected: PASS.

Run: `powershell -NoProfile -ExecutionPolicy Bypass -File tests/run-tests.ps1`

Expected: PASS.

- [ ] **Step 5: Commit state safety**

```powershell
git add scripts/context_memory_state.py scripts/fill_table_worker.py tests/test_context_memory_state.py
git commit -m "feat: validate and atomically replace memory state"
```

### Task 4: Retryable Fill-Table Worker

**Files:**
- Modify: `scripts/fill_table_worker.py`
- Create: `tests/test_fill_table_worker.py`

**Interfaces:**
- Produces: `run_worker(cwd: Path, adapter: str, live: bool, apply: bool, invoke_model: Callable | None = None) -> dict`
- Consumes: journal cursor and `context_memory_state` validation/write APIs.

- [ ] **Step 1: Add failing worker retry and cursor tests with an injected model callable**

```python
def test_invalid_routine_output_retries_then_uses_repair_model(self):
    calls = []
    outputs = iter(["not-json", "{bad", self.valid_model_json()])
    def invoke(adapter, model, prompt, config, cwd):
        calls.append(model)
        return next(outputs)
    report = worker.run_worker(self.repo, "codex-cli", True, True, invoke)
    self.assertEqual(calls, ["gpt-5-nano", "gpt-5-nano", "gpt-5-mini"])
    self.assertEqual(report["status"], "updated")

def test_failed_attempt_does_not_advance_cursor(self):
    before = self.worker_state()["last_processed_event_id"]
    with self.assertRaises(ValueError):
        worker.run_worker(self.repo, "codex-cli", True, True,
                          lambda *args: "invalid")
    self.assertEqual(self.worker_state()["last_processed_event_id"], before)
```

- [ ] **Step 2: Run tests and verify retry behavior is absent**

Run: `python -m unittest tests.test_fill_table_worker -v`

Expected: FAIL because `run_worker` and configured retry/fallback are absent.

- [ ] **Step 3: Refactor the CLI around `run_worker` and implement bounded retry/fallback**

The implementation must update `worker_state` on `no_change`, success, and
failure; use only events after the cursor; resolve journal paths from the git
root rather than the invocation subdirectory; and preserve current CLI flags.

- [ ] **Step 4: Run worker and full Python tests**

Run: `python -m unittest discover -s tests -p "test_*.py" -v`

Expected: PASS.

- [ ] **Step 5: Commit worker completion**

```powershell
git add scripts/fill_table_worker.py tests/test_fill_table_worker.py
git commit -m "feat: complete retryable fill table worker"
```

### Task 5: Hook Auto-Init And Detached Dispatch

**Files:**
- Modify: `context-memory-core.ps1`
- Modify: `context-memory-hook.ps1`
- Modify: `context-memory.ps1`
- Modify: `tests/run-tests.ps1`
- Create: `scripts/context_memory_dispatch.py`

**Interfaces:**
- Produces: `context_memory_dispatch.py record-and-dispatch --cwd --adapter --event-b64`
- Consumes: runtime initialization, journal, worker state, and managed Python.

- [ ] **Step 1: Extend the PowerShell suite with failing auto-init and dispatch tests**

```powershell
$AutoRepo = Join-Path $TempRoot "auto-repo"
New-Item -ItemType Directory -Force -Path $AutoRepo | Out-Null
& git -C $AutoRepo init --quiet
$payload = @{ cwd = $AutoRepo; hook_event_name = "UserPromptSubmit"; prompt = "first" } | ConvertTo-Json -Compress
$result = Invoke-Hook $payload @("-Adapter", "generic-json")
Assert-True (Test-Path (Join-Path $AutoRepo ".context-memory\state.yaml")) "hook did not auto-initialize"
Assert-True (($result.Stdout | ConvertFrom-Json).action -eq "inject") "first hook did not inject"
```

Also assert that a nested cwd initializes the root, a disabled marker skips,
and a compact event becomes immediately dispatch-eligible.

- [ ] **Step 2: Run the protocol suite and verify auto-init assertions fail**

Run: `powershell -NoProfile -ExecutionPolicy Bypass -File tests/run-tests.ps1`

Expected: FAIL because hook auto-init is not implemented.

- [ ] **Step 3: Implement runtime-backed auto-init, event recording, detached worker launch, and bounded diagnostics**

Use `Start-Process -WindowStyle Hidden` only after resolving the managed Python
and dispatch script to absolute paths. The hook must return before the worker
model call starts. Diagnostics must exclude prompt and environment values.

- [ ] **Step 4: Update Codex SessionStart matcher and validate every source**

```powershell
Set-HookEvent $hooks "SessionStart" "startup|resume|clear|compact" (New-CodexHookDef)
```

- [ ] **Step 5: Run protocol and Python tests**

Run: `powershell -NoProfile -ExecutionPolicy Bypass -File tests/run-tests.ps1`

Expected: PASS.

Run: `python -m unittest discover -s tests -p "test_*.py" -v`

Expected: PASS.

- [ ] **Step 6: Commit hook reliability**

```powershell
git add context-memory-core.ps1 context-memory-hook.ps1 context-memory.ps1 scripts/context_memory_dispatch.py tests/run-tests.ps1
git commit -m "feat: auto initialize and dispatch memory workers"
```

### Task 6: Managed Environment, Skill, And Doctor

**Files:**
- Modify: `install.ps1`
- Modify: `context-memory.ps1`
- Modify: `tests/run-tests.ps1`
- Modify: `README.md`

**Interfaces:**
- Produces: installer-managed `.venv` with `PyYAML==6.0.2`.
- Produces: managed skill copies under enabled agent user directories.
- Produces: `doctor` output for initialization origin, runtime, journal, worker, locks, and diagnostics.

- [ ] **Step 1: Add failing installer-shape and doctor-degradation tests**

Tests must use temporary `USERPROFILE` and a temporary tool copy, then assert
the generated hook matcher, skill destination, missing-runtime diagnostic, and
worker-error diagnostic.

- [ ] **Step 2: Run tests and verify managed-environment checks are missing**

Run: `powershell -NoProfile -ExecutionPolicy Bypass -File tests/run-tests.ps1`

Expected: FAIL on the new installer and doctor assertions.

- [ ] **Step 3: Implement venv bootstrap, pinned dependency installation, managed skill copy, and doctor checks**

```powershell
& $Python -m venv (Join-Path $InstallDir ".venv")
$ManagedPython = Join-Path $InstallDir ".venv\Scripts\python.exe"
& $ManagedPython -m pip install --disable-pip-version-check -r (Join-Path $InstallDir "requirements.txt")
```

The uninstaller removes only owned hook definitions and owned skill copies. It
does not delete project memory or the tool repository.

- [ ] **Step 4: Run the full test suite**

Run: `python -m unittest discover -s tests -p "test_*.py" -v`

Expected: PASS.

Run: `powershell -NoProfile -ExecutionPolicy Bypass -File tests/run-tests.ps1`

Expected: PASS.

- [ ] **Step 5: Commit installation and diagnostics**

```powershell
git add install.ps1 context-memory.ps1 tests/run-tests.ps1 README.md
git commit -m "feat: manage worker runtime and diagnostics"
```

### Task 7: Honest Benchmarks And Regression Tests

**Files:**
- Modify: `benchmarks/claude-code-usage-report.py`
- Modify: `benchmarks/simulate-token-savings.py`
- Create: `tests/test_benchmarks.py`
- Modify: `docs/benchmark-results.md`
- Modify: `README.md`

**Interfaces:**
- Produces: `replay_transcript_tokens` results with per-transcript reset and rows.
- Produces: explicitly labeled offline upper-bound estimates.

- [ ] **Step 1: Add a failing two-transcript reset test**

```python
def test_replay_resets_running_context_for_each_transcript(self):
    first = self.write_transcript("one.jsonl", ["a", "b"])
    second = self.write_transcript("two.jsonl", ["c"])
    result = report.replay_transcript_tokens([first, second], len)
    self.assertEqual(result["per_transcript"][1]["baseline_replay_total_tokens"], 0)
```

- [ ] **Step 2: Run the benchmark test and verify cumulative replay fails it**

Run: `python -m unittest tests.test_benchmarks -v`

Expected: FAIL because `running` currently spans transcript files.

- [ ] **Step 3: Reset replay state per transcript and revise all result labels and documentation**

Remove claims that replay savings prove quality-preserving measured savings.
Keep actual provider usage metadata separate from offline replacement bounds.

- [ ] **Step 4: Run all tests and representative benchmark commands**

Run: `python -m unittest discover -s tests -p "test_*.py" -v`

Expected: PASS.

Run: `python benchmarks/simulate-token-savings.py --turns 10 --chars-per-turn 3000`

Expected: JSON containing `offline_upper_bound` wording and no runtime error.

- [ ] **Step 5: Commit benchmark corrections**

```powershell
git add benchmarks docs/benchmark-results.md README.md tests/test_benchmarks.py
git commit -m "fix: report replay estimates per session"
```

### Task 8: Release Candidate And Upgrade Path

**Files:**
- Modify: `README.md`
- Modify: `protocol.md`
- Create: `CHANGELOG.md`
- Modify: `config.yaml`
- Modify: `templates/.context-memory/config.yaml`
- Modify: `context-memory-core.ps1`

**Interfaces:**
- Produces: documented `v0.2.0` pinned installer command.
- Produces: migration behavior for existing `v0.1.8` installations and project configs.

- [ ] **Step 1: Add migration assertions to the protocol suite**

Initialize a v1 fixture, run the updated installer/CLI, and assert existing
`state.yaml` content remains unchanged while missing config keys and database
schema are upgraded safely.

- [ ] **Step 2: Run the migration test and verify it fails before migration support**

Run: `powershell -NoProfile -ExecutionPolicy Bypass -File tests/run-tests.ps1`

Expected: FAIL on missing v0.2.0 defaults or schema migration.

- [ ] **Step 3: Implement config merge migration and write release documentation**

The README pinned command must use both:

```powershell
git -c advice.detachedHead=false -C $d checkout -q "refs/tags/v0.2.0^{commit}"
powershell -NoProfile -ExecutionPolicy Bypass -File "$d/install.ps1" -Branch v0.2.0
```

Document that the old `v0.1.8` command remains pinned and must be replaced once
by the new command. Do not make old tags mutable.

- [ ] **Step 4: Run the completion verification matrix**

Run: `python -m unittest discover -s tests -p "test_*.py" -v`

Expected: all Python tests PASS.

Run: `powershell -NoProfile -ExecutionPolicy Bypass -File tests/run-tests.ps1`

Expected: `context-memory protocol tests passed`.

Run: `git diff v0.1.8 --check`

Expected: no output and exit 0.

Run a clean temporary install, first-hook auto-init, three-event dispatch with a
stub model, disabled-marker skip, nested-cwd worker, and uninstall smoke test.

- [ ] **Step 5: Review and commit the release candidate**

```powershell
git add README.md protocol.md CHANGELOG.md config.yaml templates/.context-memory/config.yaml context-memory-core.ps1 tests
git commit -m "chore: prepare v0.2.0 release"
```

- [ ] **Step 6: Create the local annotated release tag only after every verification passes**

```powershell
git tag -a v0.2.0 -m "Agent Context Memory v0.2.0"
```

Do not push the branch or tag without an explicit publication request.
