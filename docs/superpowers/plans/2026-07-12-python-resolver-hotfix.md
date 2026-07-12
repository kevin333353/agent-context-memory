# Python Resolver Hotfix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Windows installer and hook fallback select one usable Python 3.9+ executable when `PATH` contains multiple `python` applications, then publish v0.2.2.

**Architecture:** Add one internal PowerShell resolver shared by `install.ps1` and `context-memory-core.ps1`. It enumerates and case-insensitively deduplicates application paths, probes each candidate, and returns exactly one scalar path or `$null`; integration tests prepend two real command shims to `PATH` to reproduce the v0.2.1 failure.

**Tech Stack:** Windows PowerShell 5.1, Python 3.9+, `unittest`, Git, GitHub CLI.

## Global Constraints

- Preserve `.venv\Scripts\python.exe` as the runtime's first choice.
- Do not add public installer parameters or dependencies.
- Hooks remain fail-open when no compatible fallback runtime exists.
- Do not move or replace the published v0.2.0 or v0.2.1 tags.
- Publish the fix only as v0.2.2.

---

### Task 1: Shared Python Resolver and Regression Test

**Files:**
- Create: `python-resolver.ps1`
- Modify: `install.ps1:15-19,149-166`
- Modify: `context-memory-core.ps1:1-10,87-97`
- Modify: `tests/run-tests.ps1:50-64`

**Interfaces:**
- Produces: `Get-CompatiblePythonPath -> [string] | $null`.
- Consumes: PowerShell `Get-Command python -CommandType Application -All` and candidate `Source` paths.

- [ ] **Step 1: Write the duplicate-command regression setup**

In `tests/run-tests.ps1`, resolve one working bootstrap Python, create two temporary `python.cmd` shims that delegate to it, prepend both shim directories to `PATH`, and run the existing real installer inside `try/finally` so `PATH` is restored:

```powershell
$bootstrapPython = $null
foreach ($candidate in @(Get-Command python -CommandType Application -All -ErrorAction SilentlyContinue)) {
  try {
    & $candidate.Source -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)" *> $null
    if ($LASTEXITCODE -eq 0) {
      $bootstrapPython = [string]$candidate.Source
      break
    }
  } catch {}
}
Assert-True (-not [string]::IsNullOrWhiteSpace($bootstrapPython)) "test requires Python 3.9+"

$shimOne = Join-Path $TempRoot "python-shim-one"
$shimTwo = Join-Path $TempRoot "python-shim-two"
New-Item -ItemType Directory -Force -Path $shimOne,$shimTwo | Out-Null
$shimText = "@echo off`r`n`"$bootstrapPython`" %*`r`n"
$shimText | Set-Content -Encoding ASCII -LiteralPath (Join-Path $shimOne "python.cmd")
$shimText | Set-Content -Encoding ASCII -LiteralPath (Join-Path $shimTwo "python.cmd")

$originalPath = $env:Path
try {
  $env:Path = "$shimOne;$shimTwo;$originalPath"
  $runtimeInstallOutput = & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "install.ps1") -SkipRepositorySync -InstallDir $Root -NoPath -NoClaude -NoCodex -NoProjectInit -NoDoctor 2>&1 | Out-String
  Assert-True ($LASTEXITCODE -eq 0) "managed runtime install failed with duplicate Python commands: $runtimeInstallOutput"
} finally {
  $env:Path = $originalPath
}
```

- [ ] **Step 2: Run the PowerShell suite and verify the regression fails**

Run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tests\run-tests.ps1
```

Expected: FAIL from `install.ps1` because the call operator receives multiple space-separated Python paths.

- [ ] **Step 3: Add the minimal shared resolver**

Create `python-resolver.ps1`:

```powershell
function Get-CompatiblePythonPath {
  $seen = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
  foreach ($command in @(Get-Command python -CommandType Application -All -ErrorAction SilentlyContinue)) {
    $source = [string]$command.Source
    if ([string]::IsNullOrWhiteSpace($source) -or -not $seen.Add($source)) {
      continue
    }
    try {
      & $source -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)" *> $null
      if ($LASTEXITCODE -eq 0) {
        return $source
      }
    } catch {}
  }
  return $null
}
```

Dot-source it from both production scripts:

```powershell
$pythonResolver = Join-Path $PSScriptRoot "python-resolver.ps1"
if (-not (Test-Path -LiteralPath $pythonResolver)) {
  throw "找不到 Python resolver：$pythonResolver"
}
. $pythonResolver
```

Change `Install-ManagedPython` to call `Get-CompatiblePythonPath`, reject `$null` with `找不到可用的 Python 3.9 以上版本。`, and use the returned scalar for `-m venv`. Change `Get-ContextMemoryPythonPath` to return the managed Python first and otherwise return `Get-CompatiblePythonPath`.

- [ ] **Step 4: Run focused PowerShell verification**

Run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tests\run-tests.ps1
```

Expected: PASS, including the installer run with both shims on `PATH`.

- [ ] **Step 5: Commit the resolver fix**

```powershell
git add python-resolver.ps1 install.ps1 context-memory-core.ps1 tests/run-tests.ps1
git commit -m "Fix installer Python command resolution"
```

---

### Task 2: v0.2.2 Release Metadata

**Files:**
- Modify: `tests/run-tests.ps1:155-158`
- Modify: `VERSION`
- Modify: `CHANGELOG.md:1-5`
- Modify: `README.md:30,53,60,66,144`
- Modify: `install.ps1:149-160`

**Interfaces:**
- Produces: `context-memory version` output `0.2.2` and pinned installer commands using `v0.2.2` in both tag references.
- Consumes: the resolver behavior from Task 1.

- [ ] **Step 1: Change the version assertion first**

```powershell
Assert-True ($cliVersion.Stdout.Trim() -eq "0.2.2") "cli version did not report 0.2.2"
```

- [ ] **Step 2: Verify the version assertion fails**

Run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tests\run-tests.ps1
```

Expected: FAIL because `VERSION` still contains `0.2.1`.

- [ ] **Step 3: Update release files**

Set `VERSION` to `0.2.2`. Add a `CHANGELOG.md` section dated `2026-07-12` explaining that Windows installation now handles multiple Python commands and ignores unusable aliases. Replace README pinned commands so both `refs/tags/v0.2.2^{commit}` and `-Branch v0.2.2` match, and update upgrade/troubleshooting prose. Change installer requirement messages from v0.2.1 to v0.2.2.

- [ ] **Step 4: Verify release metadata**

Run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tests\run-tests.ps1
Select-String -Path README.md,install.ps1,VERSION,tests/run-tests.ps1 -Pattern 'refs/tags/v0.2.1','-Branch v0.2.1','Agent Context Memory v0.2.1','eq "0.2.1"'
```

Expected: PowerShell suite PASS; stale release search returns no matches.

- [ ] **Step 5: Commit release metadata**

```powershell
git add VERSION CHANGELOG.md README.md install.ps1 tests/run-tests.ps1
git commit -m "Prepare Agent Context Memory v0.2.2"
```

---

### Task 3: Full Verification and Publication

**Files:**
- Verify only: all tracked source and test files.

**Interfaces:**
- Produces: merged PR, immutable `v0.2.2` tag, and published GitHub release.
- Consumes: clean commits from Tasks 1 and 2.

- [ ] **Step 1: Run the complete verification gate**

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
powershell -NoProfile -ExecutionPolicy Bypass -File .\tests\run-tests.ps1
.\.venv\Scripts\python.exe -m compileall -q scripts tests
$errors = $null; [System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path '.\install.ps1'), [ref]$null, [ref]$errors) | Out-Null; if ($errors.Count) { $errors | Format-List; exit 1 }
$errors = $null; [System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path '.\python-resolver.ps1'), [ref]$null, [ref]$errors) | Out-Null; if ($errors.Count) { $errors | Format-List; exit 1 }
git diff --check origin/main...HEAD
```

Expected: 35 Python tests pass, PowerShell protocol suite passes, compile/parser checks exit 0, and no whitespace errors.

- [ ] **Step 2: Push and open a draft PR**

```powershell
git push -u origin fix/python-resolver-v0.2.2
gh pr create --draft --base main --head fix/python-resolver-v0.2.2 --title "Fix Windows Python resolution and release v0.2.2" --body "Fixes Windows installation when PATH exposes multiple Python applications. Adds a shared Python 3.9+ resolver, duplicate-command regression coverage, and v0.2.2 release metadata."
```

Expected: GitHub returns the draft PR URL.

- [ ] **Step 3: Inspect PR state and merge**

```powershell
gh pr checks --watch
gh pr ready
gh pr merge --squash --delete-branch
```

Expected: all configured checks pass and the PR is merged into `main`.

- [ ] **Step 4: Tag the verified merge commit**

```powershell
git switch main
git pull --ff-only origin main
git tag -a v0.2.2 -m "Agent Context Memory v0.2.2"
git push origin v0.2.2
```

Expected: the new tag points at the merged v0.2.2 commit; existing tags are unchanged.

- [ ] **Step 5: Publish and verify the GitHub release**

```powershell
gh release create v0.2.2 --title "Agent Context Memory v0.2.2" --notes "Fixes Windows installation when multiple Python applications or WindowsApps aliases are present on PATH. Use the v0.2.2 pinned installer command from README.md to upgrade."
gh release view v0.2.2 --json url,tagName,isDraft,isPrerelease
```

Expected: `tagName` is `v0.2.2`, both flags are false, and GitHub returns a release URL.
