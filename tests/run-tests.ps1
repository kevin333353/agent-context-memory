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

try {
  $initPayload = @{ cwd = $TempRoot; hook_event_name = "UserPromptSubmit" } | ConvertTo-Json -Compress
  $init = Invoke-Hook $initPayload @("-Mode", "init")
  Assert-True ($init.ExitCode -eq 0) "init failed: $($init.Stderr)"
  Assert-True (Test-Path -LiteralPath (Join-Path $TempRoot ".context-memory\project.yaml")) "project file was not initialized"
  Assert-True (Test-Path -LiteralPath (Join-Path $TempRoot ".context-memory\handoff\README.md")) "handoff readme was not initialized"
  $projectText = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $TempRoot ".context-memory\project.yaml")
  Assert-True ($projectText.Contains('root: "."')) "project file should not hardcode an absolute root path"
  $handoffText = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $TempRoot ".context-memory\handoff\README.md")
  Assert-True ($handoffText.Contains("YYYY-MM-DD-owner-topic.md")) "handoff readme did not include filename convention"

  $cliInit = Invoke-Cli "init" "-Cwd" $TempRoot "-UpdateGitignore"
  Assert-True ($cliInit.ExitCode -eq 0) "cli init exited $($cliInit.ExitCode): $($cliInit.Stdout)"
  $gitignoreText = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $TempRoot ".gitignore")
  Assert-True ($gitignoreText.Contains("!.context-memory/schema.yaml")) "cli init did not add team-safe gitignore rules"

  $cliValidate = Invoke-Cli "validate" "-Cwd" $TempRoot
  Assert-True ($cliValidate.ExitCode -eq 0) "cli validate exited $($cliValidate.ExitCode): $($cliValidate.Stdout)"
  Assert-True ($cliValidate.Stdout.Contains("Validation passed")) "cli validate did not pass"

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
    $claudeSettingsText = Get-Content -Raw -Encoding UTF8 -LiteralPath $claudeSettingsPath
    $codexHooksText = Get-Content -Raw -Encoding UTF8 -LiteralPath $codexHooksPath
    Assert-True ($claudeSettingsText.Contains("context-memory-hook.ps1")) "claude hook did not reference context-memory"
    Assert-True ($codexHooksText.Contains("context-memory-hook.ps1")) "codex hook did not reference context-memory"
    $codexHooks = $codexHooksText | ConvertFrom-Json
    $codexCommand = [string]$codexHooks.hooks.UserPromptSubmit[0].hooks[0].command
    Assert-True ($codexCommand.Contains('-File "')) "codex hook should quote the -File path"

    $uninstallHooks = Invoke-Cli "uninstall" "all"
    Assert-True ($uninstallHooks.ExitCode -eq 0) "cli uninstall all exited $($uninstallHooks.ExitCode): $($uninstallHooks.Stdout)"
    $claudeSettingsText = Get-Content -Raw -Encoding UTF8 -LiteralPath $claudeSettingsPath
    $codexHooksText = Get-Content -Raw -Encoding UTF8 -LiteralPath $codexHooksPath
    Assert-True (-not $claudeSettingsText.Contains("context-memory-hook")) "claude uninstall left context-memory hook"
    Assert-True (-not $codexHooksText.Contains("context-memory-hook")) "codex uninstall left context-memory hook"
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
  if (Test-Path -LiteralPath $TempRoot) {
    Remove-Item -LiteralPath $TempRoot -Recurse -Force
  }
}
