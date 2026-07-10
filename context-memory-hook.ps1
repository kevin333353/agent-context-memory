param(
  [ValidateSet("auto", "init", "inject", "post-compact")]
  [string]$Mode = "auto",
  [string]$Adapter = "auto"
)

$ErrorActionPreference = "SilentlyContinue"
$Utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = $Utf8NoBom
[Console]::OutputEncoding = $Utf8NoBom
$OutputEncoding = $Utf8NoBom
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$InputRaw = [Console]::In.ReadToEnd()

function Write-HookDiagnostic([string]$Message) {
  try {
    $path = Join-Path $Root "logs\hook-diagnostics.log"
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $path) | Out-Null
    $safe = (($Message -replace "[`r`n`0]", " ").Trim())
    if ($safe.Length -gt 1000) {
      $safe = $safe.Substring(0, 1000)
    }
    $lines = @()
    if (Test-Path -LiteralPath $path) {
      $lines = @(Get-Content -Encoding UTF8 -LiteralPath $path | Select-Object -Last 199)
    }
    @($lines + "$([DateTime]::UtcNow.ToString('o')) $safe") | Set-Content -Encoding UTF8 -LiteralPath $path
  } catch {}
}

if ($Adapter -eq "auto") {
  $Adapter = "claude-code"
}

$adapterPath = Join-Path $Root ("adapters\" + $Adapter + ".ps1")
if (-not (Test-Path -LiteralPath $adapterPath)) {
  exit 0
}

try {
  $errPath = Join-Path $env:TEMP ("context-memory-hook-" + [Guid]::NewGuid().ToString("N") + ".err")
  & $adapterPath -Mode $Mode -InputRaw $InputRaw 2>$errPath
  $adapterExitCode = $LASTEXITCODE
  $hasStderr = (Test-Path -LiteralPath $errPath) -and ((Get-Item -LiteralPath $errPath).Length -gt 0)
  if ($adapterExitCode -ne 0 -or $hasStderr) {
    Write-HookDiagnostic "adapter=$Adapter exit_code=$adapterExitCode stderr_present=$hasStderr"
  }
} catch {
  Write-HookDiagnostic "adapter=$Adapter wrapper_exception=$($_.Exception.GetType().Name)"
} finally {
  if ($errPath -and (Test-Path -LiteralPath $errPath)) {
    Remove-Item -LiteralPath $errPath -Force -ErrorAction SilentlyContinue
  }
}

exit 0
