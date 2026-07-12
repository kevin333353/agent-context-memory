param(
  [ValidateSet("auto", "init", "inject", "post-compact")]
  [string]$Mode = "auto",
  [string]$InputRaw = $null
)

$ErrorActionPreference = "SilentlyContinue"
. (Join-Path (Split-Path -Parent $PSScriptRoot) "context-memory-core.ps1")

$result = Invoke-ContextMemoryProtocol -Mode $Mode -InputRaw $InputRaw -AdapterName "claude-code"
if ($result.action -eq "inject" -and $result.context) {
  $output = @{
    hookSpecificOutput = @{
      hookEventName = $result.framework_event
      additionalContext = $result.context
    }
  }
  if ($result.block) {
    $output.decision = "block"
    $output.reason = $result.block_reason
  }
  $output | ConvertTo-Json -Depth 8 -Compress
} elseif ($result.action -eq "initialized") {
  Write-Output "Initialized $($result.memory_root)"
}
