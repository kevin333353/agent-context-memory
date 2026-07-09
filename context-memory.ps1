param(
  [Parameter(Position = 0)]
  [string]$Command = "help",
  [Parameter(Position = 1)]
  [string]$Target = "",
  [string]$Cwd = (Get-Location).Path,
  [int]$TokenLimit = 2000,
  [switch]$UpdateGitignore,
  [switch]$All
)

$ErrorActionPreference = "Stop"
$Utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = $Utf8NoBom
[Console]::OutputEncoding = $Utf8NoBom
$OutputEncoding = $Utf8NoBom

$ToolRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $ToolRoot "context-memory-core.ps1")

function Write-Check([string]$Level, [string]$Message) {
  switch ($Level) {
    "pass" { Write-Output "[PASS] $Message" }
    "warn" { Write-Output "[WARN] $Message" }
    "fail" { Write-Output "[FAIL] $Message" }
    default { Write-Output "[INFO] $Message" }
  }
}

function Invoke-ContextMemoryHelp {
  Write-Output @"
context-memory CLI

Usage:
  context-memory <command> [target] [options]

Commands:
  init              Create .context-memory files for this repo
  install claude    Install Claude Code hooks
  install codex     Install Codex hooks
  install all       Install Claude Code and Codex hooks
  uninstall all      Remove Claude Code and Codex context-memory hooks
  doctor            Check local setup and hook health
  status            Show current memory status
  validate          Validate memory files and state size
  resume            Print a new-chat resume prompt
  compact-state     Add a history marker when state.yaml exceeds the token target
  benchmark         Run synthetic token-savings benchmark
  help              Show this help

Options:
  -Cwd <path>             Project directory, default current directory
  -TokenLimit <number>    Target state.yaml token limit, default 2000
  -UpdateGitignore        During init, update .gitignore for team-safe files
"@
}

function Get-ProjectRoot([string]$StartDir) {
  try {
    $resolved = (Resolve-Path -LiteralPath $StartDir).Path
  } catch {
    $resolved = $StartDir
  }

  try {
    $gitRoot = & git -C $resolved rev-parse --show-toplevel 2>$null
    if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($gitRoot)) {
      return $gitRoot.Trim()
    }
  } catch {}

  return $resolved
}

function Get-ApproxTokenCount([string]$Text) {
  if ([string]::IsNullOrEmpty($Text)) {
    return 0
  }
  return [int][Math]::Ceiling($Text.Length / 4.0)
}

function Write-TextFile([string]$Path, [string]$Text) {
  $dir = Split-Path -Parent $Path
  if (-not [string]::IsNullOrWhiteSpace($dir)) {
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
  }
  [System.IO.File]::WriteAllText($Path, $Text, $Utf8NoBom)
}

function Set-JsonProperty($Obj, [string]$Name, $Value) {
  if ($Obj.PSObject.Properties[$Name]) {
    $Obj.$Name = $Value
  } else {
    $Obj | Add-Member -NotePropertyName $Name -NotePropertyValue $Value
  }
}

function Read-JsonObject([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path)) {
    return [pscustomobject]@{}
  }
  $raw = Get-Content -Raw -Encoding UTF8 -LiteralPath $Path
  if ([string]::IsNullOrWhiteSpace($raw)) {
    return [pscustomobject]@{}
  }
  return ($raw | ConvertFrom-Json)
}

function Write-JsonObject([string]$Path, $Obj) {
  $dir = Split-Path -Parent $Path
  New-Item -ItemType Directory -Force -Path $dir | Out-Null
  $json = $Obj | ConvertTo-Json -Depth 20
  Write-TextFile $Path ($json + [Environment]::NewLine)
}

function Test-ContextMemoryHook($Hook) {
  if (-not $Hook) {
    return $false
  }
  $command = [string]$Hook.command
  $argsText = ""
  if ($Hook.args) {
    $argsText = (@($Hook.args) -join " ")
  }
  return ($command -like "*context-memory-hook*" -or $argsText -like "*context-memory-hook*")
}

function Set-HookEvent($HooksObj, [string]$EventName, [string]$Matcher, $HookDef) {
  if (-not $HooksObj.PSObject.Properties[$EventName]) {
    $HooksObj | Add-Member -NotePropertyName $EventName -NotePropertyValue @()
  }

  $groups = @($HooksObj.$EventName)
  $newGroups = @()
  $inserted = $false

  foreach ($group in $groups) {
    if (-not $group) {
      continue
    }

    $existingHooks = @($group.hooks) | Where-Object { -not (Test-ContextMemoryHook $_) }
    $groupMatcher = ""
    if ($group.PSObject.Properties["matcher"]) {
      $groupMatcher = [string]$group.matcher
    }

    if ($groupMatcher -eq $Matcher) {
      $existingHooks = @($existingHooks) + @($HookDef)
      Set-JsonProperty $group "hooks" $existingHooks
      $inserted = $true
    }

    if (@($existingHooks).Count -gt 0) {
      $newGroups += $group
    }
  }

  if (-not $inserted) {
    $newGroups += [pscustomobject][ordered]@{
      matcher = $Matcher
      hooks = @($HookDef)
    }
  }

  Set-JsonProperty $HooksObj $EventName $newGroups
}

function Remove-HookEvent($HooksObj, [string]$EventName) {
  if (-not $HooksObj.PSObject.Properties[$EventName]) {
    return 0
  }

  $groups = @($HooksObj.$EventName)
  $newGroups = @()
  $removed = 0

  foreach ($group in $groups) {
    if (-not $group) {
      continue
    }

    $existingHooks = @($group.hooks)
    $keptHooks = @($existingHooks | Where-Object { -not (Test-ContextMemoryHook $_) })
    $removed += [Math]::Max(0, $existingHooks.Count - $keptHooks.Count)

    if ($keptHooks.Count -gt 0) {
      Set-JsonProperty $group "hooks" $keptHooks
      $newGroups += $group
    }
  }

  Set-JsonProperty $HooksObj $EventName $newGroups
  return $removed
}

function New-ClaudeHookDef {
  $hookPath = Join-Path $ToolRoot "context-memory-hook.ps1"
  return [pscustomobject][ordered]@{
    type = "command"
    command = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    args = @(
      "-NoProfile",
      "-ExecutionPolicy",
      "Bypass",
      "-File",
      $hookPath,
      "-Adapter",
      "claude-code"
    )
  }
}

function New-CodexHookDef {
  $hookPath = (Join-Path $ToolRoot "context-memory-hook.ps1").Replace("\", "/")
  $powerShellPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
  return [pscustomobject][ordered]@{
    type = "command"
    command = "`"$powerShellPath`" -NoProfile -ExecutionPolicy Bypass -File `"$hookPath`" -Adapter codex-cli"
  }
}

function Ensure-ProjectMemoryFiles([string]$ProjectRoot) {
  $memoryRoot = Initialize-ContextMemory $ProjectRoot
  $projectPath = Join-Path $memoryRoot "project.yaml"
  $handoffRoot = Join-Path $memoryRoot "handoff"
  New-Item -ItemType Directory -Force -Path $handoffRoot | Out-Null

  if (-not (Test-Path -LiteralPath $projectPath)) {
    $projectName = Split-Path -Leaf $ProjectRoot
    $projectText = @"
schema_version: 1
project:
  name: "$projectName"
  root: "."
  goal: ""
purpose: "Shared stable project memory for context-memory/v1. Commit this file when the facts are useful to the team."
source_of_truth:
  - "Repository files and tests override context memory."
  - "User instructions override stale memory."
team_context: []
handoff_policy:
  summary: "Use .context-memory/state.yaml for personal session state and .context-memory/handoff/*.md for cross-session or teammate handoff."
  do_not_store:
    - "Secrets"
    - "Full logs"
    - "Full transcripts"
    - "Large diffs"
agent_adapters:
  claude-code: "Installed by context-memory install claude."
  codex-cli: "Installed by context-memory install codex."
"@
    Write-TextFile $projectPath $projectText
  }

  $readmePath = Join-Path $handoffRoot "README.md"
  if (-not (Test-Path -LiteralPath $readmePath)) {
    $readmeText = @"
# Context Memory Handoff

Put cross-session or teammate handoff notes here.

Recommended file name: YYYY-MM-DD-owner-topic.md

Keep handoffs short and actionable:

- goal
- current state
- decisions
- files to read
- blockers
- next action
"@
    Write-TextFile $readmePath $readmeText
  }

  return $memoryRoot
}

function Update-ContextMemoryGitignore([string]$ProjectRoot) {
  $gitignorePath = Join-Path $ProjectRoot ".gitignore"
  $block = @"
# Context memory: commit shared schema/config/project/handoff; keep personal session state local.
!.context-memory/
!.context-memory/schema.yaml
!.context-memory/config.yaml
!.context-memory/project.yaml
!.context-memory/handoff/
!.context-memory/handoff/*.md
.context-memory/state.yaml
.context-memory/history.md
.context-memory/last-compact.md
.context-memory/events.sqlite
.context-memory/*.tmp
"@

  $existing = ""
  if (Test-Path -LiteralPath $gitignorePath) {
    $existing = Get-Content -Raw -Encoding UTF8 -LiteralPath $gitignorePath
  }
  $removedOldComment = $false
  if ($existing -match "(?m)^# session-local context memory.*\r?\n") {
    $existing = $existing -replace "(?m)^# session-local context memory.*\r?\n", ""
    $removedOldComment = $true
  }

  if ($existing -match "(?m)^\.context-memory/\s*$") {
    $existing = $existing -replace "(?m)^\.context-memory/\s*$", $block.TrimEnd()
    Write-TextFile $gitignorePath $existing
    return "updated"
  }

  if ($existing -notlike "*!.context-memory/schema.yaml*") {
    Write-TextFile $gitignorePath ($existing.TrimEnd() + "`n`n" + $block.TrimEnd() + "`n")
    return "added"
  }

  if ($removedOldComment) {
    Write-TextFile $gitignorePath $existing
    return "updated"
  }

  return "unchanged"
}

function Invoke-InitCommand {
  $projectRoot = Get-ProjectRoot $Cwd
  $memoryRoot = Ensure-ProjectMemoryFiles $projectRoot
  Write-Output "Initialized context memory at $memoryRoot"
  if ($UpdateGitignore) {
    $result = Update-ContextMemoryGitignore $projectRoot
    Write-Output ".gitignore $result"
  }
}

function Invoke-InstallCommand {
  $targetName = $Target.ToLowerInvariant()
  if ($targetName -eq "" -or $targetName -eq "all") {
    Invoke-InstallCommandFor "claude"
    Invoke-InstallCommandFor "codex"
    return
  }
  Invoke-InstallCommandFor $targetName
}

function Invoke-InstallCommandFor([string]$TargetName) {
  switch ($TargetName) {
    "claude" {
      $settingsPath = Join-Path $env:USERPROFILE ".claude\settings.json"
      $settings = Read-JsonObject $settingsPath
      if (-not $settings.PSObject.Properties["env"]) {
        Set-JsonProperty $settings "env" ([pscustomobject]@{})
      }
      Set-JsonProperty $settings.env "ENABLE_PROMPT_CACHING_1H" "1"
      if (-not $settings.PSObject.Properties["hooks"]) {
        Set-JsonProperty $settings "hooks" ([pscustomobject]@{})
      }
      $hook = New-ClaudeHookDef
      Set-HookEvent $settings.hooks "UserPromptSubmit" "" $hook
      Set-HookEvent $settings.hooks "SessionStart" "compact" $hook
      Set-HookEvent $settings.hooks "SubagentStart" "" $hook
      Set-HookEvent $settings.hooks "PostCompact" "" $hook
      Write-JsonObject $settingsPath $settings
      Write-Output "Installed Claude Code context-memory hooks in $settingsPath"
    }
    "codex" {
      $settingsPath = Join-Path $env:USERPROFILE ".codex\hooks.json"
      $settings = Read-JsonObject $settingsPath
      if (-not $settings.PSObject.Properties["hooks"]) {
        Set-JsonProperty $settings "hooks" ([pscustomobject]@{})
      }
      $hook = New-CodexHookDef
      Set-HookEvent $settings.hooks "UserPromptSubmit" "" $hook
      Set-HookEvent $settings.hooks "SessionStart" "compact" $hook
      Set-HookEvent $settings.hooks "SubagentStart" "" $hook
      Set-HookEvent $settings.hooks "PostCompact" "" $hook
      Write-JsonObject $settingsPath $settings
      Write-Output "Installed Codex context-memory hooks in $settingsPath"
    }
    default {
      throw "Unknown install target: $TargetName. Use claude, codex, or all."
    }
  }
}

function Invoke-UninstallCommand {
  $targetName = $Target.ToLowerInvariant()
  if ($targetName -eq "" -or $targetName -eq "all") {
    Invoke-UninstallCommandFor "claude"
    Invoke-UninstallCommandFor "codex"
    return
  }
  Invoke-UninstallCommandFor $targetName
}

function Invoke-UninstallCommandFor([string]$TargetName) {
  $events = @("UserPromptSubmit", "SessionStart", "SubagentStart", "PostCompact")
  switch ($TargetName) {
    "claude" {
      $settingsPath = Join-Path $env:USERPROFILE ".claude\settings.json"
      if (-not (Test-Path -LiteralPath $settingsPath)) {
        Write-Output "Claude Code settings not found; nothing to uninstall: $settingsPath"
        return
      }

      $settings = Read-JsonObject $settingsPath
      if (-not $settings.PSObject.Properties["hooks"]) {
        Write-Output "Claude Code hooks not found; nothing to uninstall: $settingsPath"
        return
      }

      $removed = 0
      foreach ($event in $events) {
        $removed += Remove-HookEvent $settings.hooks $event
      }

      if ($removed -gt 0) {
        Write-JsonObject $settingsPath $settings
        Write-Output "Removed $removed Claude Code context-memory hook(s) from $settingsPath"
        Write-Output "ENABLE_PROMPT_CACHING_1H was left unchanged."
      } else {
        Write-Output "No Claude Code context-memory hooks found in $settingsPath"
      }
    }
    "codex" {
      $settingsPath = Join-Path $env:USERPROFILE ".codex\hooks.json"
      if (-not (Test-Path -LiteralPath $settingsPath)) {
        Write-Output "Codex hooks file not found; nothing to uninstall: $settingsPath"
        return
      }

      $settings = Read-JsonObject $settingsPath
      if (-not $settings.PSObject.Properties["hooks"]) {
        Write-Output "Codex hooks not found; nothing to uninstall: $settingsPath"
        return
      }

      $removed = 0
      foreach ($event in $events) {
        $removed += Remove-HookEvent $settings.hooks $event
      }

      if ($removed -gt 0) {
        Write-JsonObject $settingsPath $settings
        Write-Output "Removed $removed Codex context-memory hook(s) from $settingsPath"
      } else {
        Write-Output "No Codex context-memory hooks found in $settingsPath"
      }
    }
    default {
      throw "Unknown uninstall target: $TargetName. Use claude, codex, or all."
    }
  }
}

function Invoke-ValidateCommand {
  $projectRoot = Get-ProjectRoot $Cwd
  $memoryRoot = Find-ContextMemoryRoot $projectRoot
  $failures = 0
  $warnings = 0

  if (-not $memoryRoot) {
    Write-Check "fail" "No .context-memory/state.yaml found from $projectRoot"
    exit 1
  }

  Write-Check "pass" "Memory root: $memoryRoot"
  $required = @("state.yaml", "schema.yaml", "config.yaml")
  foreach ($name in $required) {
    $path = Join-Path $memoryRoot $name
    if (Test-Path -LiteralPath $path) {
      Write-Check "pass" "$name exists"
    } else {
      Write-Check "fail" "$name missing"
      $failures++
    }
  }

  $statePath = Join-Path $memoryRoot "state.yaml"
  if (Test-Path -LiteralPath $statePath) {
    $state = Get-Content -Raw -Encoding UTF8 -LiteralPath $statePath
    $tokens = Get-ApproxTokenCount $state
    if ($tokens -gt $TokenLimit) {
      Write-Check "warn" "state.yaml is about $tokens tokens; target is <= $TokenLimit"
      $warnings++
    } else {
      Write-Check "pass" "state.yaml is about $tokens tokens"
    }

    foreach ($key in @("schema_version:", "project:", "current_focus:", "stable_context:", "dynamic_context:", "open_questions:", "decisions:", "files:", "next_actions:")) {
      if ($state -notmatch "(?m)^$([regex]::Escape($key))") {
        Write-Check "fail" "state.yaml missing top-level key $key"
        $failures++
      }
    }

    $longLines = @($state -split "`r?`n" | Where-Object { $_.Length -gt 2000 })
    if ($longLines.Count -gt 0) {
      Write-Check "warn" "state.yaml has $($longLines.Count) very long line(s); move large content to artifacts"
      $warnings++
    }

    foreach ($pattern in @("BEGIN TRANSCRIPT", "tool_result", "```diff", "```log")) {
      if ($state -like "*$pattern*") {
        Write-Check "warn" "state.yaml appears to contain large/raw content marker: $pattern"
        $warnings++
      }
    }
  }

  if ($failures -gt 0) {
    Write-Output "Validation failed: $failures failure(s), $warnings warning(s)."
    exit 1
  }
  Write-Output "Validation passed: $warnings warning(s)."
}

function Invoke-StatusCommand {
  $projectRoot = Get-ProjectRoot $Cwd
  $memoryRoot = Find-ContextMemoryRoot $projectRoot
  if (-not $memoryRoot) {
    Write-Output "No context memory found from $projectRoot"
    exit 1
  }

  $statePath = Join-Path $memoryRoot "state.yaml"
  $projectPath = Join-Path $memoryRoot "project.yaml"
  $state = Get-Content -Raw -Encoding UTF8 -LiteralPath $statePath
  $tokens = Get-ApproxTokenCount $state
  Write-Output "Project root: $projectRoot"
  Write-Output "Memory root:  $memoryRoot"
  Write-Output "State tokens: about $tokens"
  Write-Output "Project file: $(if (Test-Path -LiteralPath $projectPath) { 'present' } else { 'missing' })"

  foreach ($key in @("last_updated", "task", "status", "next_step")) {
    $pattern = '(?m)^\s*' + [regex]::Escape($key) + ':\s*"?(.+?)"?\s*$'
    $match = [regex]::Match($state, $pattern)
    if ($match.Success) {
      Write-Output "${key}: $($match.Groups[1].Value)"
    }
  }
}

function Invoke-DoctorCommand {
  $projectRoot = Get-ProjectRoot $Cwd
  $memoryRoot = Find-ContextMemoryRoot $projectRoot
  $failures = 0
  $warnings = 0

  Write-Output "context-memory doctor"
  Write-Output "Project root: $projectRoot"

  if ($memoryRoot) {
    Write-Check "pass" "Memory root found: $memoryRoot"
  } else {
    Write-Check "fail" "No .context-memory/state.yaml found"
    $failures++
  }

  $hookPath = Join-Path $ToolRoot "context-memory-hook.ps1"
  if (Test-Path -LiteralPath $hookPath) {
    Write-Check "pass" "Hook exists: $hookPath"
  } else {
    Write-Check "fail" "Hook missing: $hookPath"
    $failures++
  }

  $claudePath = Join-Path $env:USERPROFILE ".claude\settings.json"
  if (Test-Path -LiteralPath $claudePath) {
    try {
      $claude = Read-JsonObject $claudePath
      $claudeText = Get-Content -Raw -Encoding UTF8 -LiteralPath $claudePath
      if ($claudeText -like "*context-memory-hook.ps1*") {
        Write-Check "pass" "Claude Code hook references context-memory"
      } else {
        Write-Check "warn" "Claude Code hook not installed"
        $warnings++
      }
      if ($claudeText -like "*WindowsPowerShell\\v1.0\\powershell.exe*" -and $claudeText -like "*`"args`"*") {
        Write-Check "pass" "Claude Code hook uses exec-form args"
      } else {
        Write-Check "warn" "Claude Code hook may use shell command string; Windows Git Bash can fail"
        $warnings++
      }
      if ($claude.enabledPlugins -and $claude.enabledPlugins.PSObject.Properties["claude-mem@thedotmack"] -and $claude.enabledPlugins."claude-mem@thedotmack" -eq $true) {
        Write-Check "warn" "claude-mem@thedotmack is enabled; avoid double memory injection"
        $warnings++
      } else {
        Write-Check "pass" "claude-mem is not enabled"
      }
    } catch {
      Write-Check "fail" "Claude settings JSON is invalid: $($_.Exception.Message)"
      $failures++
    }
  } else {
    Write-Check "warn" "Claude settings not found: $claudePath"
    $warnings++
  }

  $codexPath = Join-Path $env:USERPROFILE ".codex\hooks.json"
  if (Test-Path -LiteralPath $codexPath) {
    try {
      $codexText = Get-Content -Raw -Encoding UTF8 -LiteralPath $codexPath
      $null = $codexText | ConvertFrom-Json
      if ($codexText -like "*context-memory-hook.ps1*") {
        Write-Check "pass" "Codex hook references context-memory"
      } else {
        Write-Check "warn" "Codex hook not installed"
        $warnings++
      }
    } catch {
      Write-Check "fail" "Codex hooks JSON is invalid: $($_.Exception.Message)"
      $failures++
    }
  } else {
    Write-Check "warn" "Codex hooks not found: $codexPath"
    $warnings++
  }

  if ($memoryRoot) {
    $payload = @{ cwd = $projectRoot; hook_event_name = "UserPromptSubmit"; prompt = "doctor smoke test" } | ConvertTo-Json -Compress
    try {
      $out = $payload | & powershell -NoProfile -ExecutionPolicy Bypass -File $hookPath -Adapter generic-json 2>&1 | Out-String
      if ($LASTEXITCODE -eq 0) {
        try {
          $hookJson = $out | ConvertFrom-Json
          if ($hookJson.context -and [string]$hookJson.context -like "*<CONTEXT_MEMORY_STATE*") {
            Write-Check "pass" "Hook smoke test injects context"
          } else {
            Write-Check "fail" "Hook smoke test did not inject context: $out"
            $failures++
          }
        } catch {
          if ($out -like "*<CONTEXT_MEMORY_STATE*" -or $out -like "*\u003cCONTEXT_MEMORY_STATE*") {
            Write-Check "pass" "Hook smoke test injects context"
          } else {
            Write-Check "fail" "Hook smoke test did not inject context: $out"
            $failures++
          }
        }
      } else {
        Write-Check "fail" "Hook smoke test did not inject context: $out"
        $failures++
      }
    } catch {
      Write-Check "fail" "Hook smoke test failed: $($_.Exception.Message)"
      $failures++
    }

    $sessionPayload = @{ cwd = $projectRoot; hook_event_name = "SessionStart"; source = "startup" } | ConvertTo-Json -Compress
    try {
      $sessionOut = $sessionPayload | & powershell -NoProfile -ExecutionPolicy Bypass -File $hookPath -Adapter claude-code 2>&1 | Out-String
      try {
        $sessionJson = $sessionOut | ConvertFrom-Json
        if ($sessionJson.hookSpecificOutput -and $sessionJson.hookSpecificOutput.hookEventName -eq "SessionStart" -and [string]$sessionJson.hookSpecificOutput.additionalContext -like "*<CONTEXT_MEMORY_STATE*") {
          Write-Check "pass" "Claude Code SessionStart smoke test injects context"
        } else {
          Write-Check "fail" "Claude Code SessionStart smoke test did not inject context: $sessionOut"
          $failures++
        }
      } catch {
        if ($sessionOut -like "*SessionStart*" -and ($sessionOut -like "*<CONTEXT_MEMORY_STATE*" -or $sessionOut -like "*\u003cCONTEXT_MEMORY_STATE*")) {
          Write-Check "pass" "Claude Code SessionStart smoke test injects context"
        } else {
          Write-Check "fail" "Claude Code SessionStart smoke test did not inject context: $sessionOut"
          $failures++
        }
      }
    } catch {
      Write-Check "fail" "Claude Code SessionStart smoke test failed: $($_.Exception.Message)"
      $failures++
    }

    try {
      $codexSessionOut = $sessionPayload | & powershell -NoProfile -ExecutionPolicy Bypass -File $hookPath -Adapter codex-cli 2>&1 | Out-String
      try {
        $codexSessionJson = $codexSessionOut | ConvertFrom-Json
        if ($codexSessionJson.hookSpecificOutput -and $codexSessionJson.hookSpecificOutput.hookEventName -eq "SessionStart" -and [string]$codexSessionJson.hookSpecificOutput.additionalContext -like "*<CONTEXT_MEMORY_STATE*") {
          Write-Check "pass" "Codex SessionStart smoke test injects context"
        } else {
          Write-Check "fail" "Codex SessionStart smoke test did not inject context: $codexSessionOut"
          $failures++
        }
      } catch {
        if ($codexSessionOut -like "*SessionStart*" -and ($codexSessionOut -like "*<CONTEXT_MEMORY_STATE*" -or $codexSessionOut -like "*\u003cCONTEXT_MEMORY_STATE*")) {
          Write-Check "pass" "Codex SessionStart smoke test injects context"
        } else {
          Write-Check "fail" "Codex SessionStart smoke test did not inject context: $codexSessionOut"
          $failures++
        }
      }
    } catch {
      Write-Check "fail" "Codex SessionStart smoke test failed: $($_.Exception.Message)"
      $failures++
    }
  }

  if ($failures -gt 0) {
    Write-Output "Doctor failed: $failures failure(s), $warnings warning(s)."
    exit 1
  }
  Write-Output "Doctor passed: $warnings warning(s)."
}

function Invoke-ResumeCommand {
  $projectRoot = Get-ProjectRoot $Cwd
  $memoryRoot = Find-ContextMemoryRoot $projectRoot
  if (-not $memoryRoot) {
    throw "No .context-memory/state.yaml found from $projectRoot"
  }

  $projectPath = ".context-memory/project.yaml"
  $statePath = ".context-memory/state.yaml"
  $handoffRoot = Join-Path $memoryRoot "handoff"

  $latestHandoff = $null
  if (Test-Path -LiteralPath $handoffRoot) {
    $latestHandoff = Get-ChildItem -LiteralPath $handoffRoot -Filter "*.md" -File | Sort-Object LastWriteTime -Descending | Select-Object -First 1
  }

  $handoffLine = $(if ($latestHandoff) { "3. .context-memory/handoff/$($latestHandoff.Name)" } else { "3. .context-memory/handoff/ 下最新的交接檔（若存在）" })
  $resumeLines = @(
    "請接續這個專案。",
    "",
    "優先使用已注入的 <CONTEXT_MEMORY_STATE> 理解目前狀態。",
    "如果沒有看到注入內容，請依序讀取：",
    "1. $projectPath",
    "2. $statePath",
    $handoffLine,
    "",
    "規則：",
    "- 不要預設重讀完整舊 transcript。",
    "- 若記憶和 repo 檔案衝突，以 repo 檔案為準，並更新記憶。",
    "- 大型 log、diff、測試輸出只保留路徑和摘要，不要貼進記憶表。",
    "- 先用 5 點以內說明：目前目標、已完成、下一步、風險、你要先讀哪些檔案。"
  )
  Write-Output ($resumeLines -join [Environment]::NewLine)
}

function Invoke-CompactStateCommand {
  $projectRoot = Get-ProjectRoot $Cwd
  $memoryRoot = Find-ContextMemoryRoot $projectRoot
  if (-not $memoryRoot) {
    throw "No .context-memory/state.yaml found from $projectRoot"
  }
  $statePath = Join-Path $memoryRoot "state.yaml"
  $historyPath = Join-Path $memoryRoot "history.md"
  $state = Get-Content -Raw -Encoding UTF8 -LiteralPath $statePath
  $tokens = Get-ApproxTokenCount $state
  if ($tokens -le $TokenLimit) {
    Write-Output "state.yaml is about $tokens tokens; no compaction needed."
    return
  }

  $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss zzz"
  Add-Content -LiteralPath $historyPath -Encoding UTF8 -Value "`n## State overflow snapshot - $stamp`n`nMoved manually by context-memory compact-state. Review state.yaml and keep only actionable current state.`n"
  Write-Output "state.yaml is about $tokens tokens, above $TokenLimit."
  Write-Output "A history marker was appended to $historyPath."
  Write-Output "Manual compaction required: move stale dynamic_context entries to history.md, keep current state actionable."
}

function Invoke-BenchmarkCommand {
  $script = Join-Path $ToolRoot "benchmarks\simulate-token-savings.py"
  if (-not (Test-Path -LiteralPath $script)) {
    throw "Benchmark script missing: $script"
  }
  & python $script
  exit $LASTEXITCODE
}

function Show-Help {
  Write-Output @"
context-memory CLI

Usage:
  context-memory <command> [target] [options]

Commands:
  init              Create .context-memory files for this repo
  install claude    Install Claude Code hooks
  install codex     Install Codex hooks
  install all       Install Claude Code and Codex hooks
  uninstall all      Remove Claude Code and Codex context-memory hooks
  doctor            Check local setup and hook health
  status            Show current memory status
  validate          Validate memory files and state size
  resume            Print a new-chat resume prompt
  compact-state     Add a history marker when state.yaml exceeds the token target
  benchmark         Run synthetic token-savings benchmark
  help              Show this help

Options:
  -Cwd <path>             Project directory, default current directory
  -TokenLimit <number>    Target state.yaml token limit, default 2000
  -UpdateGitignore        During init, update .gitignore for team-safe files
"@
}

switch ($Command.ToLowerInvariant()) {
  "init" { Invoke-InitCommand }
  "install" { Invoke-InstallCommand }
  "uninstall" { Invoke-UninstallCommand }
  "remove" { Invoke-UninstallCommand }
  "doctor" { Invoke-DoctorCommand }
  "status" { Invoke-StatusCommand }
  "validate" { Invoke-ValidateCommand }
  "resume" { Invoke-ResumeCommand }
  "compact-state" { Invoke-CompactStateCommand }
  "benchmark" { Invoke-BenchmarkCommand }
  "help" { Invoke-ContextMemoryHelp }
  "--help" { Invoke-ContextMemoryHelp }
  "-h" { Invoke-ContextMemoryHelp }
  default {
    Write-Output "Unknown command: $Command"
    Invoke-ContextMemoryHelp
    exit 1
  }
}
