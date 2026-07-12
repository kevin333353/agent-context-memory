function Get-ContextMemoryCompatiblePythonPath {
  $seen = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
  $commands = @(Get-Command python -CommandType Application -All -ErrorAction SilentlyContinue)

  foreach ($command in $commands) {
    $source = [string]$command.Source
    if ([string]::IsNullOrWhiteSpace($source) -or -not $seen.Add($source)) {
      continue
    }

    try {
      & $source -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)" *> $null
      if ($LASTEXITCODE -eq 0) {
        return $source
      }
    } catch {}
  }

  return $null
}
