if (-not $script:ContextMemoryEncodingInitialized) {
  $script:ContextMemoryEncodingInitialized = $true
  $Utf8NoBom = [System.Text.UTF8Encoding]::new($false)
  [Console]::InputEncoding = $Utf8NoBom
  [Console]::OutputEncoding = $Utf8NoBom
  $OutputEncoding = $Utf8NoBom
}

$script:ContextMemoryCoreRoot = Split-Path -Parent $PSCommandPath

function Read-ContextMemoryInput([string]$InputRaw) {
  $raw = $InputRaw
  if ([string]::IsNullOrEmpty($raw)) {
    $raw = [Console]::In.ReadToEnd()
  }

  if ([string]::IsNullOrWhiteSpace($raw)) {
    return @{ raw = ""; obj = $null }
  }

  try {
    return @{ raw = $raw; obj = ($raw | ConvertFrom-Json) }
  } catch {
    return @{ raw = $raw; obj = $null }
  }
}

function Get-ContextMemoryCwd($inputObj) {
  if ($inputObj -and $inputObj.cwd) {
    return [string]$inputObj.cwd
  }

  if ($env:CLAUDE_PROJECT_DIR) {
    return [string]$env:CLAUDE_PROJECT_DIR
  }

  return (Get-Location).Path
}

function Find-ContextMemoryRoot([string]$startDir) {
  if ([string]::IsNullOrWhiteSpace($startDir)) {
    return $null
  }

  try {
    $dir = [System.IO.DirectoryInfo]::new($startDir)
  } catch {
    return $null
  }

  while ($dir) {
    $candidate = Join-Path $dir.FullName ".context-memory\state.yaml"
    if (Test-Path -LiteralPath $candidate) {
      return (Join-Path $dir.FullName ".context-memory")
    }
    $dir = $dir.Parent
  }

  return $null
}

function ConvertTo-ContextMemoryEvent([string]$frameworkEvent) {
  switch ($frameworkEvent) {
    "UserPromptSubmit" { return "user_prompt_submit" }
    "SessionStart" { return "session_start" }
    "SubagentStart" { return "subagent_start" }
    "PostCompact" { return "post_compact" }
    default {
      if ([string]::IsNullOrWhiteSpace($frameworkEvent)) {
        return "user_prompt_submit"
      }
      return $frameworkEvent
    }
  }
}

function ConvertTo-FrameworkEvent([string]$protocolEvent) {
  switch ($protocolEvent) {
    "user_prompt_submit" { return "UserPromptSubmit" }
    "session_start" { return "SessionStart" }
    "subagent_start" { return "SubagentStart" }
    "post_compact" { return "PostCompact" }
    default { return $protocolEvent }
  }
}

function Initialize-ContextMemory([string]$cwd) {
  $memoryRoot = Join-Path $cwd ".context-memory"
  New-Item -ItemType Directory -Force -Path $memoryRoot | Out-Null

  $schemaPath = Join-Path $memoryRoot "schema.yaml"
  $statePath = Join-Path $memoryRoot "state.yaml"
  $historyPath = Join-Path $memoryRoot "history.md"
  $configPath = Join-Path $memoryRoot "config.yaml"
  $projectPath = Join-Path $memoryRoot "project.yaml"
  $handoffRoot = Join-Path $memoryRoot "handoff"
  $handoffReadmePath = Join-Path $handoffRoot "README.md"

  if (-not (Test-Path -LiteralPath $schemaPath)) {
    @"
schema_version: 1
fields:
  project:
    purpose: "專案名稱、根目錄、主要目標。"
  current_focus:
    purpose: "目前正在處理的工作、限制、下一步。"
  stable_context:
    purpose: "長期有效的架構、規則、偏好、決策。"
  dynamic_context:
    purpose: "最近幾輪改變的狀態、暫時假設、待確認事項。"
  open_questions:
    purpose: "需要使用者或外部資訊回答的問題。"
  decisions:
    purpose: "已做出的決策，含原因與日期。"
  files:
    purpose: "重要檔案、資料來源、狀態檔位置。"
  next_actions:
    purpose: "新的 session 或 compact 後應先做的具體步驟。"
update_rules:
  - "優先更新既有欄位，不要反覆新增同義欄位。"
  - "動態資訊放在檔案後段；穩定規則放在前段。"
  - "不要貼完整 transcript；只保留可執行的摘要與檔案路徑。"
  - "與原始碼、文件、使用者明確指令衝突時，以原始來源為準並更新本檔。"
"@ | Set-Content -LiteralPath $schemaPath -Encoding UTF8
  }

  if (-not (Test-Path -LiteralPath $statePath)) {
    @"
schema_version: 1
last_updated: ""
project:
  name: ""
  root: ""
  goal: ""
current_focus:
  task: ""
  status: ""
  next_step: ""
stable_context: []
dynamic_context: []
open_questions: []
decisions: []
files: []
next_actions: []
"@ | Set-Content -LiteralPath $statePath -Encoding UTF8
  }

  if (-not (Test-Path -LiteralPath $historyPath)) {
    "# Context Memory History`n" | Set-Content -LiteralPath $historyPath -Encoding UTF8
  }

  if (-not (Test-Path -LiteralPath $projectPath)) {
    $projectName = Split-Path -Leaf $cwd
    @"
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
"@ | Set-Content -LiteralPath $projectPath -Encoding UTF8
  }

  New-Item -ItemType Directory -Force -Path $handoffRoot | Out-Null
  if (-not (Test-Path -LiteralPath $handoffReadmePath)) {
    @"
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
"@ | Set-Content -LiteralPath $handoffReadmePath -Encoding UTF8
  }

  if (-not (Test-Path -LiteralPath $configPath)) {
    @"
schema_version: 1
fill_table:
  enabled: true
  update_mode: "background_summarizer"
  summary_interval_turns: 3
  inject_token_limit: 2000
  worker:
    auto_run: false
    status: "not_installed"
    note: "Hooks record events only. Add a background worker before automatically rewriting state.yaml."
  journal:
    enabled: true
    path: ".context-memory/events.sqlite"
    store_full_payload: false
    max_prompt_chars: 8000
  validation:
    require_valid_yaml: true
    retry_same_model_once: true
    fallback_on_invalid_yaml: true
  adapters:
    claude-code:
      routine_model: "haiku"
      routine_model_note: "Claude Code CLI alias; intended target is Claude Haiku 4.5."
      repair_model: "sonnet"
      repair_model_note: "Use Sonnet only for invalid YAML, conflicts, compact rebuilds, or schema migration."
      major_rebuild_model: "sonnet"
      max_budget_usd: 0.06
    codex-cli:
      routine_model: "gpt-5-nano"
      repair_model: "gpt-5-mini"
      major_rebuild_model: "gpt-5-mini"
      reasoning_effort: "low"
      max_budget_usd: 0.03
"@ | Set-Content -LiteralPath $configPath -Encoding UTF8
  }

  return $memoryRoot
}

function Write-ContextMemoryJournal($inputObj, [string]$memoryRoot, [string]$event, [string]$frameworkEvent, [string]$cwd, [string]$adapterName, [string]$action) {
  if (-not $memoryRoot) {
    return $false
  }

  $scriptPath = Join-Path $script:ContextMemoryCoreRoot "scripts\context_memory_journal.py"
  if (-not (Test-Path -LiteralPath $scriptPath)) {
    return $false
  }

  $prompt = ""
  $summary = ""
  if ($inputObj) {
    if ($inputObj.prompt) {
      $prompt = [string]$inputObj.prompt
    } elseif ($inputObj.message) {
      $prompt = [string]$inputObj.message
    } elseif ($inputObj.user_input) {
      $prompt = [string]$inputObj.user_input
    }

    if ($inputObj.compact_summary) {
      $summary = [string]$inputObj.compact_summary
    } elseif ($inputObj.summary) {
      $summary = [string]$inputObj.summary
    }
  }

  $dbPath = Join-Path $memoryRoot "events.sqlite"
  $journalEvent = @{
    adapter = $adapterName
    event = $event
    framework_event = $frameworkEvent
    action = $action
    cwd = $cwd
    prompt = $prompt
    summary = $summary
    store_full_payload = $false
    max_prompt_chars = 8000
  } | ConvertTo-Json -Depth 8 -Compress

  try {
    $eventBytes = [System.Text.Encoding]::UTF8.GetBytes($journalEvent)
    $eventB64 = [Convert]::ToBase64String($eventBytes)
    $null = & python $scriptPath --db $dbPath --event-b64 $eventB64 2>$null
    return ($LASTEXITCODE -eq 0)
  } catch {
    return $false
  }
}

function Get-ContextMemoryContext([string]$memoryRoot) {
  $statePath = Join-Path $memoryRoot "state.yaml"
  if (-not (Test-Path -LiteralPath $statePath)) {
    return $null
  }

  $stateText = Get-Content -Raw -Encoding UTF8 -LiteralPath $statePath
  if ([string]::IsNullOrWhiteSpace($stateText)) {
    return $null
  }

  $schemaPath = Join-Path $memoryRoot "schema.yaml"
  $schemaHint = ""
  if (Test-Path -LiteralPath $schemaPath) {
    $schemaHint = "Schema: .context-memory/schema.yaml`n"
  }

  return @"
<CONTEXT_MEMORY_STATE protocol="context-memory/v1">
Location: .context-memory/state.yaml
$schemaHint
<STATE_YAML>
$stateText
</STATE_YAML>
</CONTEXT_MEMORY_STATE>
"@
}

function Save-ContextMemoryCompactSummary($inputObj, [string]$memoryRoot) {
  if (-not $memoryRoot) {
    return $false
  }

  $summary = ""
  if ($inputObj) {
    if ($inputObj.compact_summary) {
      $summary = [string]$inputObj.compact_summary
    } elseif ($inputObj.summary) {
      $summary = [string]$inputObj.summary
    }
  }

  if ([string]::IsNullOrWhiteSpace($summary)) {
    return $false
  }

  New-Item -ItemType Directory -Force -Path $memoryRoot | Out-Null
  $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss zzz"
  $lastPath = Join-Path $memoryRoot "last-compact.md"
  $historyPath = Join-Path $memoryRoot "history.md"
  $entry = "## Compact summary - $stamp`n`n$summary`n"

  $entry | Set-Content -LiteralPath $lastPath -Encoding UTF8
  Add-Content -LiteralPath $historyPath -Value "`n$entry" -Encoding UTF8
  return $true
}

function Invoke-ContextMemoryProtocol {
  param(
    [ValidateSet("auto", "init", "inject", "post-compact")]
    [string]$Mode = "auto",
    [string]$InputRaw = $null,
    [string]$AdapterName = "unknown"
  )

  $stdin = Read-ContextMemoryInput $InputRaw
  $inputObj = $stdin.obj
  $cwd = Get-ContextMemoryCwd $inputObj

  $frameworkEvent = "UserPromptSubmit"
  if ($inputObj -and $inputObj.hook_event_name) {
    $frameworkEvent = [string]$inputObj.hook_event_name
  } elseif ($inputObj -and $inputObj.event) {
    $frameworkEvent = ConvertTo-FrameworkEvent ([string]$inputObj.event)
  }

  $event = ConvertTo-ContextMemoryEvent $frameworkEvent

  if ($Mode -eq "init") {
    $memoryRoot = Initialize-ContextMemory $cwd
    $journaled = Write-ContextMemoryJournal $inputObj $memoryRoot $event $frameworkEvent $cwd $AdapterName "initialized"
    return @{
      protocol = "context-memory/v1"
      action = "initialized"
      event = $event
      framework_event = $frameworkEvent
      cwd = $cwd
      memory_root = $memoryRoot
      context = $null
      journaled = $journaled
    }
  }

  $memoryRoot = Find-ContextMemoryRoot $cwd

  if ($Mode -eq "post-compact" -or ($Mode -eq "auto" -and $event -eq "post_compact")) {
    $saved = Save-ContextMemoryCompactSummary $inputObj $memoryRoot
    $action = $(if ($saved) { "saved_compact" } else { "none" })
    $journaled = Write-ContextMemoryJournal $inputObj $memoryRoot $event $frameworkEvent $cwd $AdapterName $action
    return @{
      protocol = "context-memory/v1"
      action = $action
      event = $event
      framework_event = $frameworkEvent
      cwd = $cwd
      memory_root = $memoryRoot
      context = $null
      journaled = $journaled
    }
  }

  if (-not $memoryRoot) {
    return @{
      protocol = "context-memory/v1"
      action = "none"
      event = $event
      framework_event = $frameworkEvent
      cwd = $cwd
      memory_root = $null
      context = $null
      journaled = $false
    }
  }

  $contextText = Get-ContextMemoryContext $memoryRoot
  if ([string]::IsNullOrWhiteSpace($contextText)) {
    $journaled = Write-ContextMemoryJournal $inputObj $memoryRoot $event $frameworkEvent $cwd $AdapterName "none"
    return @{
      protocol = "context-memory/v1"
      action = "none"
      event = $event
      framework_event = $frameworkEvent
      cwd = $cwd
      memory_root = $memoryRoot
      context = $null
      journaled = $journaled
    }
  }

  $journaled = Write-ContextMemoryJournal $inputObj $memoryRoot $event $frameworkEvent $cwd $AdapterName "inject"
  return @{
    protocol = "context-memory/v1"
    action = "inject"
    event = $event
    framework_event = $frameworkEvent
    cwd = $cwd
    memory_root = $memoryRoot
    context = $contextText
    journaled = $journaled
  }
}
