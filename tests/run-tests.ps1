param()

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Hook = Join-Path $Root "context-memory-hook.ps1"
$Cli = Join-Path $Root "context-memory.ps1"
$Worker = Join-Path $Root "scripts\fill_table_worker.py"

function Assert-True([bool]$Condition, [string]$Message) {
  if (-not $Condition) {
    throw $Message
  }
}

function Invoke-Hook([string]$Payload, [string[]]$HookArgs) {
  $previousOutputEncoding = $OutputEncoding
  $utf8 = [System.Text.UTF8Encoding]::new($false)
  $OutputEncoding = $utf8
  [Console]::OutputEncoding = $utf8
  [Console]::InputEncoding = $utf8

  try {
    $stdout = $Payload | & powershell -NoProfile -ExecutionPolicy Bypass -File $Hook @HookArgs 2>&1 | Out-String
    $exitCode = $LASTEXITCODE
  } finally {
    $OutputEncoding = $previousOutputEncoding
  }

  return @{
    ExitCode = $exitCode
    Stdout = $stdout
    Stderr = ""
  }
}

function Invoke-Cli {
  param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$CliArgs
  )

  $stdout = & powershell -NoProfile -ExecutionPolicy Bypass -File $Cli @CliArgs 2>&1 | Out-String
  return @{
    ExitCode = $LASTEXITCODE
    Stdout = $stdout
  }
}

$TempRoot = Join-Path $env:TEMP ("context-memory-protocol-test-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $TempRoot | Out-Null
$oldAllowTempAutoInit = $env:CONTEXT_MEMORY_ALLOW_TEMP_AUTO_INIT
$oldDisableWorkerDispatch = $env:CONTEXT_MEMORY_DISABLE_WORKER_DISPATCH
$env:CONTEXT_MEMORY_ALLOW_TEMP_AUTO_INIT = "1"
$env:CONTEXT_MEMORY_DISABLE_WORKER_DISPATCH = "1"

try {
  $runtimeInstallOutput = & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "install.ps1") -SkipRepositorySync -InstallDir $Root -NoPath -NoClaude -NoCodex -NoProjectInit -NoDoctor 2>&1 | Out-String
  Assert-True ($LASTEXITCODE -eq 0) "managed runtime install failed: $runtimeInstallOutput"
  $managedPython = Join-Path $Root ".venv\Scripts\python.exe"
  Assert-True (Test-Path -LiteralPath $managedPython) "installer did not create managed Python"
  $null = & $managedPython -c "import yaml; print(yaml.__version__)" 2>&1
  Assert-True ($LASTEXITCODE -eq 0) "managed Python cannot import PyYAML"

  $autoRepo = Join-Path $TempRoot "auto-repo"
  $autoNested = Join-Path $autoRepo "src\feature"
  New-Item -ItemType Directory -Force -Path $autoNested | Out-Null
  & git -C $autoRepo init --quiet
  $autoPayload = @{ cwd = $autoNested; hook_event_name = "UserPromptSubmit"; prompt = "first prompt" } | ConvertTo-Json -Compress
  $auto = Invoke-Hook $autoPayload @("-Adapter", "generic-json")
  Assert-True ($auto.ExitCode -eq 0) "auto-init hook exited $($auto.ExitCode): $($auto.Stdout)"
  $autoJson = $auto.Stdout | ConvertFrom-Json
  Assert-True ($autoJson.action -eq "inject") "first hook did not inject after auto-init"
  Assert-True (Test-Path -LiteralPath (Join-Path $autoRepo ".context-memory\state.yaml")) "hook did not auto-initialize git root"
  Assert-True (-not (Test-Path -LiteralPath (Join-Path $autoNested ".context-memory"))) "hook initialized nested cwd instead of git root"
  $autoMetadata = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $autoRepo ".context-memory\metadata.json") | ConvertFrom-Json
  Assert-True ($autoMetadata.initialization_origin -eq "hook_auto") "auto-init origin was not recorded"
  $autoGitignore = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $autoRepo ".gitignore")
  Assert-True ($autoGitignore.Contains(".context-memory/events.sqlite")) "auto-init did not protect local memory files"
  $autoConfig = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $autoRepo ".context-memory\config.yaml")
  Assert-True ($autoConfig.Contains("auto_run: true")) "new project did not enable background worker"

  $autoJournalPath = Join-Path $autoRepo ".context-memory\events.sqlite"
  $countEvents = { param($Path) [int](& $managedPython -c "import sqlite3; c=sqlite3.connect(r'$Path'); print(c.execute('select count(*) from events').fetchone()[0]); c.close()") }
  $eventsBeforeWorkerChild = & $countEvents $autoJournalPath
  $oldWorkerChild = $env:CONTEXT_MEMORY_WORKER_CHILD
  try {
    $env:CONTEXT_MEMORY_WORKER_CHILD = "1"
    $workerChildHook = Invoke-Hook $autoPayload @("-Adapter", "generic-json")
  } finally {
    $env:CONTEXT_MEMORY_WORKER_CHILD = $oldWorkerChild
  }
  $eventsAfterWorkerChild = & $countEvents $autoJournalPath
  Assert-True ([string]::IsNullOrWhiteSpace($workerChildHook.Stdout)) "worker child hook should emit no context"
  Assert-True ($eventsAfterWorkerChild -eq $eventsBeforeWorkerChild) "worker child hook journaled its own prompt"

  $disabledRepo = Join-Path $TempRoot "disabled-repo"
  New-Item -ItemType Directory -Force -Path $disabledRepo | Out-Null
  & git -C $disabledRepo init --quiet
  New-Item -ItemType File -Force -Path (Join-Path $disabledRepo ".context-memory-disabled") | Out-Null
  $disabledPayload = @{ cwd = $disabledRepo; hook_event_name = "UserPromptSubmit"; prompt = "skip" } | ConvertTo-Json -Compress
  $disabled = Invoke-Hook $disabledPayload @("-Adapter", "generic-json")
  $disabledJson = $disabled.Stdout | ConvertFrom-Json
  Assert-True ($disabledJson.action -eq "none") "disabled repo should not inject"
  Assert-True (-not (Test-Path -LiteralPath (Join-Path $disabledRepo ".context-memory"))) "disabled repo was auto-initialized"

  $invalidRepo = Join-Path $TempRoot "invalid-input-repo"
  New-Item -ItemType Directory -Force -Path $invalidRepo | Out-Null
  & git -C $invalidRepo init --quiet
  $oldClaudeProjectDir = $env:CLAUDE_PROJECT_DIR
  try {
    $env:CLAUDE_PROJECT_DIR = $invalidRepo
    $invalidHook = Invoke-Hook "not-json" @("-Adapter", "generic-json")
  } finally {
    $env:CLAUDE_PROJECT_DIR = $oldClaudeProjectDir
  }
  $invalidHookJson = $invalidHook.Stdout | ConvertFrom-Json
  Assert-True ($invalidHookJson.action -eq "none") "invalid hook input should fail open without injection"
  Assert-True (-not (Test-Path -LiteralPath (Join-Path $invalidRepo ".context-memory"))) "invalid hook input triggered auto-init"

  try {
    $env:CLAUDE_PROJECT_DIR = $invalidRepo
    $emptyHook = Invoke-Hook "" @("-Adapter", "generic-json")
  } finally {
    $env:CLAUDE_PROJECT_DIR = $oldClaudeProjectDir
  }
  $emptyHookJson = $emptyHook.Stdout | ConvertFrom-Json
  Assert-True ($emptyHookJson.action -eq "none") "empty hook input should fail open without injection"
  Assert-True (-not (Test-Path -LiteralPath (Join-Path $invalidRepo ".context-memory"))) "empty hook input triggered auto-init"

  $autoStatePath = Join-Path $autoRepo ".context-memory\state.yaml"
  $autoStateOriginal = Get-Content -Raw -Encoding UTF8 -LiteralPath $autoStatePath
  $oversizedLine = "  - `"" + ("x" * 12000) + "`""
  $oversizedState = $autoStateOriginal -replace '(?m)^dynamic_context:\s*\[\]\s*$', "dynamic_context:`n$oversizedLine"
  $oversizedState | Set-Content -Encoding UTF8 -LiteralPath $autoStatePath
  $oversizedHook = Invoke-Hook $autoPayload @("-Adapter", "generic-json")
  $oversizedJson = $oversizedHook.Stdout | ConvertFrom-Json
  Assert-True ($oversizedJson.action -eq "none") "oversized state should not be injected"
  $autoStateOriginal | Set-Content -Encoding UTF8 -LiteralPath $autoStatePath

  $initPayload = @{ cwd = $TempRoot; hook_event_name = "UserPromptSubmit" } | ConvertTo-Json -Compress
  $init = Invoke-Hook $initPayload @("-Mode", "init")
  Assert-True ($init.ExitCode -eq 0) "init failed: $($init.Stderr)"
  Assert-True (Test-Path -LiteralPath (Join-Path $TempRoot ".context-memory\project.yaml")) "project file was not initialized"
  Assert-True (Test-Path -LiteralPath (Join-Path $TempRoot ".context-memory\handoff\README.md")) "handoff readme was not initialized"
  $manualMetadata = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $TempRoot ".context-memory\metadata.json") | ConvertFrom-Json
  Assert-True ($manualMetadata.initialization_origin -eq "manual") "manual init origin was not recorded"
  $projectText = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $TempRoot ".context-memory\project.yaml")
  Assert-True ($projectText -match '(?m)^\s*root:\s*["'']?\.["'']?\s*$') "project file should not hardcode an absolute root path"
  $handoffText = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $TempRoot ".context-memory\handoff\README.md")
  Assert-True ($handoffText.Contains("YYYY-MM-DD-owner-topic.md")) "handoff readme did not include filename convention"

  $cliInit = Invoke-Cli "init" "-Cwd" $TempRoot "-UpdateGitignore"
  Assert-True ($cliInit.ExitCode -eq 0) "cli init exited $($cliInit.ExitCode): $($cliInit.Stdout)"
  $cliVersion = Invoke-Cli "version"
  Assert-True ($cliVersion.ExitCode -eq 0) "cli version exited $($cliVersion.ExitCode): $($cliVersion.Stdout)"
  Assert-True ($cliVersion.Stdout.Trim() -eq "0.2.0") "cli version did not report 0.2.0"
  $gitignoreText = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $TempRoot ".gitignore")
  Assert-True ($gitignoreText.Contains("!.context-memory/schema.yaml")) "cli init did not add team-safe gitignore rules"
  Assert-True ($gitignoreText.Contains(".context-memory/metadata.json")) "cli init did not ignore local metadata"
  Assert-True ($gitignoreText.Contains(".context-memory/diagnostics.log")) "cli init did not ignore diagnostics"
  Assert-True ($gitignoreText.Contains(".context-memory/*.bak-*")) "cli init did not ignore state backups"

  $cliValidate = Invoke-Cli "validate" "-Cwd" $TempRoot
  Assert-True ($cliValidate.ExitCode -eq 0) "cli validate exited $($cliValidate.ExitCode): $($cliValidate.Stdout)"
  Assert-True ($cliValidate.Stdout.Contains("Validation passed")) "cli validate did not pass"

  $stateValidationPath = Join-Path $TempRoot ".context-memory\state.yaml"
  $validStateText = Get-Content -Raw -Encoding UTF8 -LiteralPath $stateValidationPath
  $invalidStateText = $validStateText -replace '(?m)^next_actions:\s*\[\]\s*$', 'next_actions: not-a-list'
  $invalidStateText | Set-Content -Encoding UTF8 -LiteralPath $stateValidationPath
  $cliInvalidValidate = Invoke-Cli "validate" "-Cwd" $TempRoot
  Assert-True ($cliInvalidValidate.ExitCode -ne 0) "cli validate accepted a non-list next_actions value"
  $validStateText | Set-Content -Encoding UTF8 -LiteralPath $stateValidationPath

  $cliStatus = Invoke-Cli "status" "-Cwd" $TempRoot
  Assert-True ($cliStatus.ExitCode -eq 0) "cli status exited $($cliStatus.ExitCode): $($cliStatus.Stdout)"
  Assert-True ($cliStatus.Stdout.Contains("Project file: present")) "cli status did not see project file"

  $cliResume = Invoke-Cli "resume" "-Cwd" $TempRoot
  Assert-True ($cliResume.ExitCode -eq 0) "cli resume exited $($cliResume.ExitCode): $($cliResume.Stdout)"
  Assert-True ($cliResume.Stdout.Contains(".context-memory") -and $cliResume.Stdout.Contains("state.yaml")) "cli resume did not include state path"

  $promptPayload = @{ cwd = $TempRoot; hook_event_name = "UserPromptSubmit"; prompt = "hello" } | ConvertTo-Json -Compress

  $generic = Invoke-Hook $promptPayload @("-Adapter", "generic-json")
  Assert-True ($generic.ExitCode -eq 0) "generic-json exited $($generic.ExitCode): $($generic.Stderr)"
  $genericJson = $generic.Stdout | ConvertFrom-Json
  Assert-True ($genericJson.protocol -eq "context-memory/v1") "generic-json missing protocol"
  Assert-True ($genericJson.action -eq "inject") "generic-json missing inject action"
  Assert-True ($genericJson.context.Contains("<CONTEXT_MEMORY_STATE")) "generic-json missing context"
  Assert-True (-not $genericJson.context.Contains("Treat it as a compact")) "generic-json should not inject static guidance"
  Assert-True (Test-Path -LiteralPath (Join-Path $TempRoot ".context-memory\config.yaml")) "config file was not initialized"
  Assert-True (Test-Path -LiteralPath (Join-Path $TempRoot ".context-memory\events.sqlite")) "journal db was not written"

  $plain = Invoke-Hook $promptPayload @("-Adapter", "plain-text")
  Assert-True ($plain.ExitCode -eq 0) "plain-text exited $($plain.ExitCode): $($plain.Stderr)"
  Assert-True ($plain.Stdout.Contains("<CONTEXT_MEMORY_STATE")) "plain-text missing context"

  $claude = Invoke-Hook $promptPayload @("-Adapter", "claude-code")
  Assert-True ($claude.ExitCode -eq 0) "claude-code exited $($claude.ExitCode): $($claude.Stderr)"
  $claudeJson = $claude.Stdout | ConvertFrom-Json
  Assert-True ($claudeJson.hookSpecificOutput.hookEventName -eq "UserPromptSubmit") "claude-code wrong hook event"
  Assert-True ($claudeJson.hookSpecificOutput.additionalContext.Contains("<CONTEXT_MEMORY_STATE")) "claude-code missing additionalContext"

  $sessionPayload = @{ cwd = $TempRoot; hook_event_name = "SessionStart"; source = "startup" } | ConvertTo-Json -Compress
  $claudeSession = Invoke-Hook $sessionPayload @("-Adapter", "claude-code")
  Assert-True ($claudeSession.ExitCode -eq 0) "claude-code SessionStart exited $($claudeSession.ExitCode): $($claudeSession.Stdout)"
  $claudeSessionJson = $claudeSession.Stdout | ConvertFrom-Json
  Assert-True ($claudeSessionJson.hookSpecificOutput.hookEventName -eq "SessionStart") "claude-code SessionStart wrong hook event"
  Assert-True ($claudeSessionJson.hookSpecificOutput.additionalContext.Contains("<CONTEXT_MEMORY_STATE")) "claude-code SessionStart missing additionalContext"

  $codex = Invoke-Hook $promptPayload @("-Adapter", "codex-cli")
  Assert-True ($codex.ExitCode -eq 0) "codex-cli exited $($codex.ExitCode): $($codex.Stderr)"
  $codexJson = $codex.Stdout | ConvertFrom-Json
  Assert-True ($codexJson.hookSpecificOutput.hookEventName -eq "UserPromptSubmit") "codex-cli wrong hook event"
  Assert-True ($codexJson.hookSpecificOutput.additionalContext.Contains("<CONTEXT_MEMORY_STATE")) "codex-cli missing additionalContext"

  $codexSession = Invoke-Hook $sessionPayload @("-Adapter", "codex-cli")
  Assert-True ($codexSession.ExitCode -eq 0) "codex-cli SessionStart exited $($codexSession.ExitCode): $($codexSession.Stdout)"
  $codexSessionJson = $codexSession.Stdout | ConvertFrom-Json
  Assert-True ($codexSessionJson.hookSpecificOutput.hookEventName -eq "SessionStart") "codex-cli SessionStart wrong hook event"
  Assert-True ($codexSessionJson.hookSpecificOutput.additionalContext.Contains("<CONTEXT_MEMORY_STATE")) "codex-cli SessionStart missing additionalContext"

  $unknown = Invoke-Hook $promptPayload @("-Adapter", "unknown-adapter")
  Assert-True ($unknown.ExitCode -eq 0) "unknown adapter should fail open"
  Assert-True ([string]::IsNullOrWhiteSpace($unknown.Stdout)) "unknown adapter should not emit output"

  $oldUserProfile = $env:USERPROFILE
  try {
    $env:USERPROFILE = $TempRoot
    $installHooks = Invoke-Cli "install" "all"
    Assert-True ($installHooks.ExitCode -eq 0) "cli install all exited $($installHooks.ExitCode): $($installHooks.Stdout)"
    $claudeSettingsPath = Join-Path $TempRoot ".claude\settings.json"
    $codexHooksPath = Join-Path $TempRoot ".codex\hooks.json"
    Assert-True (Test-Path -LiteralPath $claudeSettingsPath) "claude settings were not written"
    Assert-True (Test-Path -LiteralPath $codexHooksPath) "codex hooks were not written"
    $claudeSkillPath = Join-Path $TempRoot ".claude\skills\context-memory\SKILL.md"
    $codexSkillPath = Join-Path $TempRoot ".codex\skills\context-memory\SKILL.md"
    Assert-True (Test-Path -LiteralPath $claudeSkillPath) "claude context-memory skill was not installed"
    Assert-True (Test-Path -LiteralPath $codexSkillPath) "codex context-memory skill was not installed"
    $claudeSettingsText = Get-Content -Raw -Encoding UTF8 -LiteralPath $claudeSettingsPath
    $codexHooksText = Get-Content -Raw -Encoding UTF8 -LiteralPath $codexHooksPath
    Assert-True ($claudeSettingsText.Contains("context-memory-hook.ps1")) "claude hook did not reference context-memory"
    Assert-True ($codexHooksText.Contains("context-memory-hook.ps1")) "codex hook did not reference context-memory"
    $codexHooks = $codexHooksText | ConvertFrom-Json
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
    $env:CONTEXT_MEMORY_TEST_CODEX_COMMAND = $codexEffectiveCommand
    $env:CONTEXT_MEMORY_TEST_CODEX_PAYLOAD = $toolRepoSessionPayload
    $codexRunner = Join-Path $PSScriptRoot "run_codex_hook_command.py"
    $codexHookResult = (& $managedPython $codexRunner | Out-String) | ConvertFrom-Json
    Remove-Item Env:CONTEXT_MEMORY_TEST_CODEX_COMMAND -ErrorAction SilentlyContinue
    Remove-Item Env:CONTEXT_MEMORY_TEST_CODEX_PAYLOAD -ErrorAction SilentlyContinue
    Assert-True ([int]$codexHookResult.exit_code -eq 0) "Codex Windows hook command exited $($codexHookResult.exit_code): $($codexHookResult.stderr)"
    Assert-True (-not [string]::IsNullOrWhiteSpace($codexWindowsCommand)) "Codex hook did not define commandWindows"
    Assert-True ([string]::IsNullOrWhiteSpace([string]$codexHookResult.stdout)) "Codex ineligible-repo SessionStart should emit no output"
    Assert-True ([string]::IsNullOrWhiteSpace([string]$codexHookResult.stderr)) "Codex ineligible-repo SessionStart should emit no stderr"
    $codexSessionMatcher = [string]$codexHooks.hooks.SessionStart[0].matcher
    Assert-True ($codexSessionMatcher -eq "startup|resume|clear|compact") "codex SessionStart matcher did not cover every documented source"

    $eventsBeforeDoctor = & $countEvents $autoJournalPath
    $doctor = Invoke-Cli "doctor" "-Cwd" $autoRepo
    $eventsAfterDoctor = & $countEvents $autoJournalPath
    Assert-True ($doctor.ExitCode -eq 0) "doctor exited $($doctor.ExitCode): $($doctor.Stdout)"
    Assert-True ($doctor.Stdout.Contains("Initialization origin: hook_auto")) "doctor did not report auto-init origin"
    Assert-True ($doctor.Stdout.Contains("Worker status:")) "doctor did not report worker state"
    Assert-True ($eventsAfterDoctor -eq $eventsBeforeDoctor) "doctor smoke tests polluted the event journal"

    $uninstallHooks = Invoke-Cli "uninstall" "all"
    Assert-True ($uninstallHooks.ExitCode -eq 0) "cli uninstall all exited $($uninstallHooks.ExitCode): $($uninstallHooks.Stdout)"
    $claudeSettingsText = Get-Content -Raw -Encoding UTF8 -LiteralPath $claudeSettingsPath
    $codexHooksText = Get-Content -Raw -Encoding UTF8 -LiteralPath $codexHooksPath
    Assert-True (-not $claudeSettingsText.Contains("context-memory-hook")) "claude uninstall left context-memory hook"
    Assert-True (-not $codexHooksText.Contains("context-memory-hook")) "codex uninstall left context-memory hook"
    Assert-True (-not (Test-Path -LiteralPath $claudeSkillPath)) "claude uninstall left managed skill"
    Assert-True (-not (Test-Path -LiteralPath $codexSkillPath)) "codex uninstall left managed skill"
  } finally {
    $env:USERPROFILE = $oldUserProfile
  }

  $compactPayload = @{ cwd = $TempRoot; hook_event_name = "PostCompact"; compact_summary = "summary text" } | ConvertTo-Json -Compress
  $compact = Invoke-Hook $compactPayload @("-Adapter", "generic-json")
  Assert-True ($compact.ExitCode -eq 0) "post compact exited $($compact.ExitCode): $($compact.Stderr)"
  Assert-True (Test-Path -LiteralPath (Join-Path $TempRoot ".context-memory\last-compact.md")) "last compact file was not written"
  $journalPath = Join-Path $TempRoot ".context-memory\events.sqlite"
  $eventCount = python -c "import sqlite3; con=sqlite3.connect(r'$journalPath'); print(con.execute('select count(*) from events').fetchone()[0])"
  Assert-True ([int]$eventCount -ge 2) "journal did not record hook events"
  $promptCount = python -c "import sqlite3; con=sqlite3.connect(r'$journalPath'); print(con.execute('select count(*) from events where prompt = ?', ('hello',)).fetchone()[0])"
  Assert-True ([int]$promptCount -ge 1) "journal did not preserve prompt text"

  $workerOut = python $Worker --cwd $TempRoot --adapter claude-code --limit 3
  $workerJson = $workerOut | ConvertFrom-Json
  Assert-True ($workerJson.mode -eq "dry-run") "worker did not run in dry-run mode"
  Assert-True ($workerJson.model -eq "haiku") "worker did not select claude-code routine model"
  Assert-True ([int]$workerJson.events.Count -ge 1) "worker did not read journal events"

  Write-Output "context-memory protocol tests passed"
} finally {
  $env:CONTEXT_MEMORY_ALLOW_TEMP_AUTO_INIT = $oldAllowTempAutoInit
  $env:CONTEXT_MEMORY_DISABLE_WORKER_DISPATCH = $oldDisableWorkerDispatch
  if (Test-Path -LiteralPath $TempRoot) {
    Remove-Item -LiteralPath $TempRoot -Recurse -Force
  }
}
