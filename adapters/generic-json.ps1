param(
  [ValidateSet("auto", "init", "inject", "post-compact")]
  [string]$Mode = "auto",
  [string]$InputRaw = $null
)

$ErrorActionPreference = "SilentlyContinue"
. (Join-Path (Split-Path -Parent $PSScriptRoot) "context-memory-core.ps1")

$result = Invoke-ContextMemoryProtocol -Mode $Mode -InputRaw $InputRaw -AdapterName "generic-json"
$result | ConvertTo-Json -Depth 8 -Compress
