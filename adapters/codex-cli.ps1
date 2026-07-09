param(
  [ValidateSet("auto", "init", "inject", "post-compact")]
  [string]$Mode = "auto",
  [string]$InputRaw = $null
)

$ErrorActionPreference = "SilentlyContinue"
. (Join-Path (Split-Path -Parent $PSScriptRoot) "context-memory-core.ps1")

$result = Invoke-ContextMemoryProtocol -Mode $Mode -InputRaw $InputRaw -AdapterName "codex-cli"
if ($result.action -eq "inject" -and $result.context) {
  @{
    hookSpecificOutput = @{
      hookEventName = $result.framework_event
      additionalContext = $result.context
    }
  } | ConvertTo-Json -Depth 8 -Compress
} elseif ($result.action -eq "initialized") {
  Write-Output "Initialized $($result.memory_root)"
}
