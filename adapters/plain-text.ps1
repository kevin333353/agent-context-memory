param(
  [ValidateSet("auto", "init", "inject", "post-compact")]
  [string]$Mode = "auto",
  [string]$InputRaw = $null
)

$ErrorActionPreference = "SilentlyContinue"
. (Join-Path (Split-Path -Parent $PSScriptRoot) "context-memory-core.ps1")

$result = Invoke-ContextMemoryProtocol -Mode $Mode -InputRaw $InputRaw -AdapterName "plain-text"
if ($result.action -eq "inject" -and $result.context) {
  Write-Output $result.context
} elseif ($result.action -eq "initialized") {
  Write-Output "Initialized $($result.memory_root)"
}
