param(
  [ValidateSet("auto", "init", "inject", "post-compact")]
  [string]$Mode = "auto",
  [ValidateSet("auto", "generic-json", "plain-text", "claude-code", "codex-cli")]
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
  Write-Error "Unknown context-memory adapter: $Adapter"
  exit 1
}

& $adapterPath -Mode $Mode -InputRaw $InputRaw
exit $LASTEXITCODE
