# Codex Windows Hook Command Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Agent Context Memory Codex hooks complete successfully when Codex CLI 0.144.1 launches them through `cmd.exe /C` on Windows.

**Architecture:** Generate a quote-free outer Windows command that invokes Windows PowerShell with a UTF-16LE Base64 `-EncodedCommand`. Store it in both `command` and the official `commandWindows` override so current and older Windows Codex hook loaders use the same safe launcher.

**Tech Stack:** Windows PowerShell 5.1, Codex `hooks.json`, `cmd.exe`, PowerShell protocol tests, Codex app-server JSON-RPC.

## Global Constraints

- Preserve stdin so `context-memory-hook.ps1` receives Codex's JSON object.
- Preserve unrelated Claude/Codex hooks and all project `.context-memory` data.
- Emit no stderr for an eligible or ineligible successful hook invocation.
- Do not change auto-init, worker, state, journal, or Claude hook behavior.
- Publish the fix as `v0.2.1`; never move or replace the published `v0.2.0` tag.

---

### Task 1: Reproduce And Fix The Windows Codex Launcher

**Files:**
- Create: `tests/run_codex_hook_command.py`
- Modify: `tests/run-tests.ps1`
- Modify: `context-memory.ps1`

**Interfaces:**
- Consumes: `New-CodexHookDef` and the generated `~/.codex/hooks.json` event definitions.
- Produces: hook handlers with string properties `command` and `commandWindows`, both containing a quote-free encoded Windows PowerShell launcher.

- [ ] **Step 1: Write the failing runtime-boundary test**

Replace the old quoted `-File` assertion in `tests/run-tests.ps1` with a test that selects `commandWindows` when present, falls back to `command`, and executes it through Codex's real Windows boundary:

```powershell
$codexHook = $codexHooks.hooks.SessionStart[0].hooks[0]
$codexCommand = [string]$codexHook.command
$codexWindowsCommand = [string]$codexHook.commandWindows
$codexEffectiveCommand = if ([string]::IsNullOrWhiteSpace($codexWindowsCommand)) {
  $codexCommand
} else {
  $codexWindowsCommand
}
$toolRepoSessionPayload = @{
  cwd = $Root
  hook_event_name = "SessionStart"
  source = "startup"
} | ConvertTo-Json -Compress
$codexHookOutput = $toolRepoSessionPayload | & $env:ComSpec /D /S /C $codexEffectiveCommand 2>&1 | Out-String
$codexHookExitCode = $LASTEXITCODE
Assert-True ($codexHookExitCode -eq 0) "Codex Windows hook command exited ${codexHookExitCode}: $codexHookOutput"
Assert-True (-not [string]::IsNullOrWhiteSpace($codexWindowsCommand)) "Codex hook did not define commandWindows"
Assert-True ([string]::IsNullOrWhiteSpace($codexHookOutput)) "Codex ineligible-repo SessionStart should emit no output"
```

- [ ] **Step 2: Run the protocol test and verify RED**

Run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tests\run-tests.ps1
```

Expected: FAIL because the existing quoted launcher exits 1 with `is not recognized as an internal or external command`.

- [ ] **Step 3: Implement the minimal encoded launcher**

Update `New-CodexHookDef` in `context-memory.ps1`:

```powershell
function New-CodexHookDef {
  $hookPath = Join-Path $ToolRoot "context-memory-hook.ps1"
  $escapedHookPath = $hookPath.Replace("'", "''")
  $scriptText = "`$ProgressPreference = 'SilentlyContinue'; & '$escapedHookPath' -Adapter 'codex-cli'"
  $encodedScript = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($scriptText))
  $launcher = "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand $encodedScript"
  return [pscustomobject][ordered]@{
    type = "command"
    command = $launcher
    commandWindows = $launcher
  }
}
```

- [ ] **Step 4: Run focused and full tests and verify GREEN**

Run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tests\run-tests.ps1
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
git diff --check
```

Expected: protocol tests pass, 35 Python tests pass, and diff check emits no output.

- [ ] **Step 5: Commit the launcher fix**

```powershell
git add context-memory.ps1 tests/run-tests.ps1
git commit -m "fix: launch Codex hooks reliably on Windows"
```

### Task 2: Prepare Patch Release Metadata

**Files:**
- Modify: `VERSION`
- Modify: `CHANGELOG.md`
- Modify: `README.md`
- Modify: `install.ps1`
- Modify: `tests/run-tests.ps1`

**Interfaces:**
- Consumes: the safe launcher from Task 1.
- Produces: a reproducible `v0.2.1` installer command and matching CLI version output.

- [ ] **Step 1: Change the version expectation first**

Update the CLI version assertion in `tests/run-tests.ps1`:

```powershell
Assert-True ($cliVersion.Stdout.Trim() -eq "0.2.1") "cli version did not report 0.2.1"
```

- [ ] **Step 2: Run the protocol test and verify RED**

Run the protocol suite and expect failure because `VERSION` still contains `0.2.0`.

- [ ] **Step 3: Update release metadata**

Set `VERSION` to `0.2.1`. Add a `0.2.1 - 2026-07-11` changelog entry describing the Codex Windows launcher fix. Replace pinned `v0.2.0` installer references and hook troubleshooting guidance in `README.md` with `v0.2.1`. Update installer error messages to name `v0.2.1`.

- [ ] **Step 4: Verify release metadata**

Run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tests\run-tests.ps1
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
Select-String -Path README.md,install.ps1,VERSION,tests/run-tests.ps1 -Pattern 'refs/tags/v0.2.0','-Branch v0.2.0','Agent Context Memory v0.2.0','eq "0.2.0"'
git diff --check
```

Expected: all tests pass and the targeted stale-release search emits no output.

- [ ] **Step 5: Commit release metadata**

```powershell
git add VERSION CHANGELOG.md README.md install.ps1 tests/run-tests.ps1
git commit -m "chore: prepare v0.2.1 release"
```

### Task 3: Install, Verify, And Publish

**Files:**
- Generated user config: `%USERPROFILE%\.codex\hooks.json`
- No additional repository source files unless verification reveals a defect.

**Interfaces:**
- Consumes: `context-memory install codex` and Codex app-server 0.144.1.
- Produces: a local completed SessionStart hook, a merged PR, immutable `v0.2.1` tag, and GitHub Release.

- [ ] **Step 1: Reinstall the local Codex hook**

```powershell
context-memory install codex
```

Confirm every managed hook contains a non-empty `commandWindows` and no quoted paths in the outer launcher.

- [ ] **Step 2: Run the app-server end-to-end check**

Start an ephemeral thread and turn through `codex app-server --stdio`, capture `hook/completed`, and require:

```json
{"eventName":"sessionStart","status":"completed","entries":[]}
```

Interrupt the turn immediately after the hook event so verification does not depend on a model response.

- [ ] **Step 3: Run final repository verification**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tests\run-tests.ps1
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
git diff --check main..HEAD
git status -sb
```

Expected: all tests pass and the worktree is clean.

- [ ] **Step 4: Publish through a pull request**

Push `fix/codex-windows-hook-command`, open a PR targeting `main`, and merge only after confirming it is mergeable. Use a squash merge and delete the remote feature branch.

- [ ] **Step 5: Tag and release the merged commit**

Update local `main`, create annotated tag `v0.2.1` on the actual merged commit, push only that new tag, and create GitHub Release `Agent Context Memory v0.2.1` with the Windows hook fix and upgrade command in its notes.

- [ ] **Step 6: Verify published state**

Confirm the PR is merged, the remote annotated tag dereferences to `origin/main`, the release is published and not a draft/prerelease, and local `main` is clean and synchronized.
