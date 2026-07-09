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
} catch {
  # Hooks should fail open. Use `context-memory doctor` for visible diagnostics.
} finally {
  if ($errPath -and (Test-Path -LiteralPath $errPath)) {
    Remove-Item -LiteralPath $errPath -Force -ErrorAction SilentlyContinue
  }
}

exit 0
