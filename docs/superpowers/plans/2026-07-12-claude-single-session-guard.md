# Claude Single-Session Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in, per-repository Claude Code guard that checkpoints context memory and coordinates compaction when provider input reaches 40k tokens.

**Architecture:** A Python guard module owns transcript parsing, threshold evaluation, local state, and reversible Claude settings. PowerShell exposes the CLI and hook protocol, while Claude's adapter emits supported block JSON; existing Codex behavior stays unchanged.

**Tech Stack:** Python 3.9+, PyYAML 6.0.2, Windows PowerShell 5.1, Claude Code hook JSON, `unittest`, GitHub CLI.

## Global Constraints

- The feature is disabled by default and enabled per repository.
- Default threshold is exactly `40000`; post-compact minimum growth is `10000`.
- Project-local `autoCompactWindow` is exactly `100000` tokens.
- Missing or invalid transcripts, usage, state, or checkpoints fail open.
- No prompt text is stored in `single-session-guard.json`.
- Codex hook installation and output remain unchanged.
- Existing user and project Claude settings are preserved.
- Release as v0.3.0 without moving any prior tag.

---

### Task 1: Guard Engine

**Files:**
- Create: `scripts/context_memory_session_guard.py`
- Create: `tests/test_context_memory_session_guard.py`

**Interfaces:**
- Produces: `latest_provider_usage(transcript: Path, after_offset: int = 0) -> dict | None`.
- Produces: `evaluate_guard(transcript: Path, state_path: Path, config: dict, prompt: str) -> dict`.
- Produces: `mark_compact_boundary(transcript: Path, state_path: Path, event: str) -> dict`.
- Produces: state keys `schema_version`, `transcript`, `compact_offset`, `post_compact_baseline_tokens`, `last_observed_tokens`, and `settings_ownership`.

- [ ] **Step 1: Write failing usage and threshold tests**

Create JSONL fixtures inside each temporary test directory and assert:

```python
def usage(input_tokens=2, creation=559, cache_read=44131):
    return {
        "input_tokens": input_tokens,
        "cache_creation_input_tokens": creation,
        "cache_read_input_tokens": cache_read,
    }

self.write_record({"requestId": "r1", "message": {"usage": usage()}})
result = guard.evaluate_guard(
    self.transcript,
    self.state_path,
    {
        "enabled": True,
        "threshold_tokens": 40000,
        "min_growth_after_compact_tokens": 10000,
        "block_on_threshold": True,
    },
    "continue",
)
self.assertTrue(result["should_block"])
self.assertEqual(result["observed_tokens"], 44692)
self.assertEqual(result["effective_threshold"], 40000)
```

Add separate tests for duplicate `requestId`, malformed final JSONL, missing usage, disabled config, `/compact` bypass, no prompt persistence, compact offset, first post-compact baseline, and effective threshold `max(40000, baseline + 10000)`.

- [ ] **Step 2: Verify the new test module fails**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_context_memory_session_guard -v
```

Expected: FAIL because `scripts.context_memory_session_guard` does not exist.

- [ ] **Step 3: Implement the minimal engine**

Implement streaming JSONL reads that track byte offsets, deduplicate request IDs, and return only the latest usage record. Use atomic JSON writes through a same-directory temporary file and `os.replace`.

The evaluation result must use this shape:

```python
{
    "enabled": True,
    "should_block": True,
    "reason": "threshold",
    "observed_tokens": 44692,
    "effective_threshold": 40000,
    "compact_offset": 0,
    "baseline_tokens": None,
}
```

Return `should_block=False` and a specific reason for `disabled`, `compact_command`, `missing_transcript`, `missing_usage`, and `below_threshold`. Invalid state is replaced with schema-versioned safe defaults.

- [ ] **Step 4: Run focused and full Python tests**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_context_memory_session_guard -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
```

Expected: guard tests and all existing Python tests PASS.

- [ ] **Step 5: Commit the guard engine**

```powershell
git add scripts/context_memory_session_guard.py tests/test_context_memory_session_guard.py
git commit -m "Add Claude single-session guard engine"
```

---

### Task 2: Configuration and Reversible CLI

**Files:**
- Modify: `scripts/context_memory_runtime.py:27-140,248-312`
- Modify: `templates/.context-memory/config.yaml`
- Modify: `config.yaml`
- Modify: `context-memory.ps1:1-55,374-434,921-965`
- Modify: `tests/test_context_memory_runtime.py`
- Modify: `tests/run-tests.ps1`

**Interfaces:**
- Produces: `configure_single_session(project_root: Path, tool_root: Path, action: str, threshold_tokens: int) -> dict`.
- Produces CLI: `context-memory single-session enable|status|disable -ThresholdTokens 40000`.
- Consumes guard state ownership fields from Task 1.

- [ ] **Step 1: Add failing config migration and settings ownership tests**

Assert default/migrated config contains:

```python
{
    "enabled": False,
    "threshold_tokens": 40000,
    "min_growth_after_compact_tokens": 10000,
    "block_on_threshold": True,
    "auto_compact_window_tokens": 100000,
}
```

Test enablement against `.claude/settings.local.json` containing unrelated properties and a prior `autoCompactWindow`. Test disable restores the prior value only when the current value is still `100000`; when changed by the user, assert `settings_preserved=True` and leave it unchanged.

- [ ] **Step 2: Verify focused tests fail**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_context_memory_runtime -v
powershell -NoProfile -ExecutionPolicy Bypass -File .\tests\run-tests.ps1
```

Expected: FAIL because config schema 3 and the CLI command do not exist.

- [ ] **Step 3: Implement schema 3 and structured configuration**

Bump `schema_version` to `3`, deep-merge existing schema 2 files, and append `.context-memory/single-session-guard.json` to managed gitignore rules.

Add runtime subcommand:

```text
single-session --project-root PATH --tool-root PATH --action enable|status|disable --threshold-tokens 40000
```

It initializes the repository when enabling, atomically writes YAML/JSON, and prints a compact JSON result. Extend PowerShell parameters with `[int]$ThresholdTokens = 40000`, call the managed Python runtime, install Claude hooks on enable, and render actionable status text.

- [ ] **Step 4: Verify CLI behavior and preservation**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_context_memory_runtime -v
powershell -NoProfile -ExecutionPolicy Bypass -File .\tests\run-tests.ps1
```

Expected: PASS for enable/status/disable, idempotent enablement, unrelated settings preservation, safe restore, and config migration.

- [ ] **Step 5: Commit configuration and CLI**

```powershell
git add scripts/context_memory_runtime.py templates/.context-memory/config.yaml config.yaml context-memory.ps1 tests/test_context_memory_runtime.py tests/run-tests.ps1
git commit -m "Add single-session guard configuration"
```

---

### Task 3: Claude Hook State Machine

**Files:**
- Modify: `context-memory-core.ps1:55-105,320-580`
- Modify: `adapters/claude-code.ps1`
- Modify: `context-memory.ps1:394-510`
- Modify: `tests/run-tests.ps1`

**Interfaces:**
- Produces protocol fields: `guard`, `block`, and `block_reason`.
- Produces Claude block output with top-level `decision: "block"` and `reason`.
- Adds Claude-only `PreCompact`; preserves Codex's four existing events.

- [ ] **Step 1: Add failing exact-boundary hook tests**

Create a synthetic Claude transcript with provider usage below and above 40k. Enable the guard, invoke the exact installed hook command, and assert below-threshold output remains normal injection.

Above threshold assert:

```powershell
Assert-True ($blockedJson.decision -eq "block") "Claude guard did not block"
Assert-True ($blockedJson.reason.Contains("44,692")) "block reason omitted observed tokens"
Assert-True ($blockedJson.reason.Contains("/compact")) "block reason omitted compact command"
```

Assert a prompt beginning `/compact` is not blocked. Assert Claude settings contain `PreCompact`; Codex settings do not. Assert Claude uninstall removes managed `PreCompact` without touching unrelated hooks.

- [ ] **Step 2: Verify PowerShell tests fail**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tests\run-tests.ps1
```

Expected: FAIL because the adapter never emits `decision: block` and `PreCompact` is absent.

- [ ] **Step 3: Implement event mapping and adapter output**

Map `PreCompact <-> pre_compact`. Call the guard Python module only for Claude `user_prompt_submit`, `pre_compact`, `post_compact`, and `session_start` source `clear|compact`.

When blocked, output:

```powershell
@{
  decision = "block"
  reason = $result.block_reason
  hookSpecificOutput = @{
    hookEventName = "UserPromptSubmit"
    additionalContext = $result.context
  }
} | ConvertTo-Json -Depth 8 -Compress
```

Keep current output byte-for-byte compatible when disabled. Add `PreCompact` only in Claude installation and removal lists.

- [ ] **Step 4: Verify hook and regression behavior**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tests\run-tests.ps1
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
```

Expected: block/bypass/transition tests PASS and existing Codex command tests remain green.

- [ ] **Step 5: Commit the hook state machine**

```powershell
git add context-memory-core.ps1 adapters/claude-code.ps1 context-memory.ps1 tests/run-tests.ps1
git commit -m "Coordinate Claude compaction from hooks"
```

---

### Task 4: Synchronous Checkpoint and Fail-Open Recovery

**Files:**
- Modify: `scripts/context_memory_dispatch.py:90-210`
- Modify: `context-memory-core.ps1:320-580`
- Modify: `tests/test_context_memory_dispatch.py`
- Modify: `tests/run-tests.ps1`

**Interfaces:**
- Produces: `run_worker_synchronously(memory_root: Path, adapter: str, tool_root: Path) -> dict`.
- Produces dispatch reason `pre_compact` for forced checkpoint events.
- Consumes the existing worker lock and `CONTEXT_MEMORY_WORKER_CHILD` recursion protection.

- [ ] **Step 1: Add failing synchronous dispatch tests**

Test `pre_compact` is due regardless of event threshold, synchronous worker return values propagate, lock contention returns `locked`, and `CONTEXT_MEMORY_DISABLE_WORKER_DISPATCH=1` prevents model invocation without breaking the hook.

- [ ] **Step 2: Verify dispatch tests fail**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_context_memory_dispatch -v
```

Expected: FAIL because synchronous checkpoint API and `pre_compact` reason are absent.

- [ ] **Step 3: Implement checkpoint execution**

Treat both `pre_compact` and `post_compact` as forced dispatch events. Add CLI subcommand:

```text
run-worker-now --memory-root PATH --adapter claude-code --tool-root PATH
```

Run through `run_worker_locked`; never start a detached child for this command. The PowerShell hook calls it after journaling and catches all failures into bounded diagnostics. PreCompact always exits 0.

- [ ] **Step 4: Verify checkpoint and hook fail-open paths**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_context_memory_dispatch -v
powershell -NoProfile -ExecutionPolicy Bypass -File .\tests\run-tests.ps1
```

Expected: PASS with success, disabled, locked, model failure, and missing runtime paths.

- [ ] **Step 5: Commit checkpoint behavior**

```powershell
git add scripts/context_memory_dispatch.py context-memory-core.ps1 tests/test_context_memory_dispatch.py tests/run-tests.ps1
git commit -m "Checkpoint memory before Claude compaction"
```

---

### Task 5: Doctor, Skill Guidance, and v0.3.0 Metadata

**Files:**
- Modify: `context-memory.ps1:614-855`
- Modify: `skills/context-memory/SKILL.md`
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `VERSION`
- Modify: `install.ps1`
- Modify: `tests/run-tests.ps1`

**Interfaces:**
- Produces doctor checks for guard config/state, Claude `PreCompact`, project-local auto-compact, and environment override precedence.
- Produces installed version `0.3.0` and pinned v0.3.0 installer commands.

- [ ] **Step 1: Add failing doctor and version assertions**

Assert enabled projects report threshold, effective threshold, managed `100000` fallback, and environment override warning. Change CLI version expectation to `0.3.0` before modifying `VERSION`.

- [ ] **Step 2: Verify assertions fail**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tests\run-tests.ps1
```

Expected: FAIL on absent doctor output and old version `0.2.2`.

- [ ] **Step 3: Implement diagnostics, stable guidance, and release text**

Doctor distinguishes disabled, active, overridden, and invalid states. The Claude skill tells agents to use subagents/artifact paths for large searches, logs, test output, and reports, returning only short summaries to the main thread.

Update README with the three CLI commands, `/compact` recovery flow, `/autocompact 100k` explanation, raw-token versus billing caveat, and v0.3.0 pinned installer command. Update changelog, installer messages, and `VERSION` without changing historical release sections.

- [ ] **Step 4: Verify docs and metadata**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tests\run-tests.ps1
Select-String -Path README.md,install.ps1,VERSION,tests/run-tests.ps1 -Pattern 'refs/tags/v0.2.2','-Branch v0.2.2','Agent Context Memory v0.2.2','eq "0.2.2"'
```

Expected: suite PASS and stale pinned v0.2.2 release strings absent outside historical changelog/specs.

- [ ] **Step 5: Commit v0.3.0 metadata**

```powershell
git add context-memory.ps1 skills/context-memory/SKILL.md README.md CHANGELOG.md VERSION install.ps1 tests/run-tests.ps1
git commit -m "Prepare Agent Context Memory v0.3.0"
```

---

### Task 6: Full Verification, Sandbox, and Publication

**Files:**
- Verify: all changed source, tests, configuration, and documentation.

**Interfaces:**
- Produces: merged PR, immutable `v0.3.0` tag, and public GitHub release.

- [ ] **Step 1: Run the complete local gate**

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
powershell -NoProfile -ExecutionPolicy Bypass -File .\tests\run-tests.ps1
.\.venv\Scripts\python.exe -m compileall -q scripts tests
$files = 'install.ps1','context-memory.ps1','context-memory-core.ps1','context-memory-hook.ps1','adapters/claude-code.ps1','tests/run-tests.ps1'
foreach ($file in $files) {
  $tokens = $null; $errors = $null
  [System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path $file), [ref]$tokens, [ref]$errors) | Out-Null
  if ($errors.Count) { $errors | Format-List; exit 1 }
}
git diff --check origin/main...HEAD
```

Expected: all Python tests, PowerShell protocol tests, compile/parser checks, and whitespace checks PASS.

- [ ] **Step 2: Run isolated end-to-end installation**

Use a disposable TEMP root with an independent `USERPROFILE`, tool clone, `.venv`, git project, and Claude settings. Enable the guard, feed the exact installed `UserPromptSubmit` hook command a usage-shaped transcript at 44,692 input tokens, and verify block JSON includes `/compact`. Execute exact `PreCompact` and `PostCompact` hooks, then verify the resubmitted prompt is allowed, auto-compact is `100000`, doctor passes, and disable safely restores settings. Verify all recursive cleanup targets resolve under TEMP before removal.

- [ ] **Step 3: Push and open draft PR**

```powershell
git push -u origin feat/claude-single-session-guard
gh pr create --draft --base main --head feat/claude-single-session-guard --title "Add Claude single-session guard and release v0.3.0" --body "Adds an opt-in 40k Claude session guard, reversible project-local 100k auto-compaction, PreCompact checkpointing, exact hook-boundary tests, diagnostics, and v0.3.0 release metadata."
```

- [ ] **Step 4: Review, merge, and reverify main**

```powershell
gh pr checks
gh pr ready
gh pr merge --squash --delete-branch
git switch main
git pull --ff-only origin main
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
powershell -NoProfile -ExecutionPolicy Bypass -File .\tests\run-tests.ps1
```

Expected: PR is merged and the exact main commit passes both suites.

- [ ] **Step 5: Tag, publish, and verify remote release**

```powershell
git tag -a v0.3.0 -m "Agent Context Memory v0.3.0"
git push origin v0.3.0
gh release create v0.3.0 --title "Agent Context Memory v0.3.0" --notes "Adds an opt-in Claude single-session guard with 40k provider-input detection, reversible 100k project auto-compaction, PreCompact checkpointing, PostCompact recovery, and fail-open diagnostics."
gh release view v0.3.0 --json url,tagName,isDraft,isPrerelease
```

Expected: tag dereferences to the verified main commit; release is public, non-draft, and non-prerelease.
