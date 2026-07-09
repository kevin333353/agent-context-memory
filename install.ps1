param(
  [string]$RepoUrl = "http://tfyhfc01:3000/KEVIN33335313/agent-context-memory.git",
  [string]$Branch = "main",
  [string]$InstallDir = (Join-Path $env:USERPROFILE ".agent-context-memory"),
  [string]$ProjectDir = "",
  [switch]$NoPath,
  [switch]$NoClaude,
  [switch]$NoCodex,
  [switch]$NoProjectInit,
  [switch]$NoDoctor
)

$ErrorActionPreference = "Stop"
$Utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = $Utf8NoBom
[Console]::OutputEncoding = $Utf8NoBom
$OutputEncoding = $Utf8NoBom

function Write-Step([string]$Message) {
  Write-Output "[context-memory] $Message"
}

function Test-CommandExists([string]$Name) {
  return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Get-GitRoot([string]$Path) {
  try {
    $root = & git -C $Path rev-parse --show-toplevel 2>$null
    if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($root)) {
      return $root.Trim()
    }
  } catch {}
  return $null
}

function Add-UserPath([string]$Dir) {
  $resolved = [System.IO.Path]::GetFullPath($Dir).TrimEnd("\")
  $current = [Environment]::GetEnvironmentVariable("Path", "User")
  $entries = @()
  if (-not [string]::IsNullOrWhiteSpace($current)) {
    $entries = @($current -split ";" | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
  }

  $exists = $false
  foreach ($entry in $entries) {
    try {
      if ([System.IO.Path]::GetFullPath($entry).TrimEnd("\").Equals($resolved, [StringComparison]::OrdinalIgnoreCase)) {
        $exists = $true
        break
      }
    } catch {
      if ($entry.TrimEnd("\").Equals($resolved, [StringComparison]::OrdinalIgnoreCase)) {
        $exists = $true
        break
      }
    }
  }

  if ($exists) {
    Write-Step "PATH 已包含 $resolved"
  } else {
    $newPath = $(if ([string]::IsNullOrWhiteSpace($current)) { $resolved } else { "$current;$resolved" })
    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
    Write-Step "已加入使用者 PATH：$resolved"
  }

  $processEntries = @($env:Path -split ";" | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
  $processHasDir = $false
  foreach ($entry in $processEntries) {
    if ($entry.TrimEnd("\").Equals($resolved, [StringComparison]::OrdinalIgnoreCase)) {
      $processHasDir = $true
      break
    }
  }
  if (-not $processHasDir) {
    $env:Path = "$env:Path;$resolved"
  }
}

function Install-Repository {
  if (-not (Test-CommandExists "git")) {
    throw "找不到 git，請先安裝 Git for Windows，或確認 git 已在 PATH。"
  }

  if (Test-Path -LiteralPath $InstallDir) {
    $gitDir = Join-Path $InstallDir ".git"
    if (-not (Test-Path -LiteralPath $gitDir)) {
      throw "安裝目錄已存在但不是 git repo：$InstallDir"
    }
    Write-Step "更新工具：$InstallDir"
    & git -C $InstallDir remote set-url origin $RepoUrl
    & git -C $InstallDir fetch origin $Branch
    if ($LASTEXITCODE -ne 0) {
      throw "git fetch 失敗。"
    }
    & git -C $InstallDir checkout $Branch
    if ($LASTEXITCODE -ne 0) {
      throw "git checkout $Branch 失敗。"
    }
    & git -C $InstallDir pull --ff-only origin $Branch
    if ($LASTEXITCODE -ne 0) {
      throw "git pull --ff-only 失敗；請確認安裝目錄沒有本機修改：$InstallDir"
    }
  } else {
    $parent = Split-Path -Parent $InstallDir
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    Write-Step "clone 工具到 $InstallDir"
    & git clone --branch $Branch $RepoUrl $InstallDir
    if ($LASTEXITCODE -ne 0) {
      throw "git clone 失敗。"
    }
  }
}

function Invoke-ContextMemory([string[]]$CliArgs) {
  $cli = Join-Path $InstallDir "context-memory.ps1"
  if (-not (Test-Path -LiteralPath $cli)) {
    throw "找不到 context-memory.ps1：$cli"
  }
  & powershell -NoProfile -ExecutionPolicy Bypass -File $cli @CliArgs
  if ($LASTEXITCODE -ne 0) {
    throw "context-memory $($CliArgs -join ' ') 失敗。"
  }
}

function Resolve-ProjectDir {
  if (-not [string]::IsNullOrWhiteSpace($ProjectDir)) {
    return (Resolve-Path -LiteralPath $ProjectDir).Path
  }

  if ($NoProjectInit) {
    return $null
  }

  $cwd = (Get-Location).Path
  $root = Get-GitRoot $cwd
  if ([string]::IsNullOrWhiteSpace($root)) {
    return $null
  }

  $installRoot = [System.IO.Path]::GetFullPath($InstallDir).TrimEnd("\")
  $projectRoot = [System.IO.Path]::GetFullPath($root).TrimEnd("\")
  if ($projectRoot.Equals($installRoot, [StringComparison]::OrdinalIgnoreCase)) {
    return $null
  }

  return $projectRoot
}

Write-Step "開始安裝 Agent Context Memory"
Install-Repository

if (-not $NoPath) {
  Add-UserPath $InstallDir
}

if (-not $NoClaude -and -not $NoCodex) {
  Write-Step "安裝 Claude Code 與 Codex hooks"
  Invoke-ContextMemory @("install", "all")
} elseif (-not $NoClaude) {
  Write-Step "安裝 Claude Code hooks"
  Invoke-ContextMemory @("install", "claude")
} elseif (-not $NoCodex) {
  Write-Step "安裝 Codex hooks"
  Invoke-ContextMemory @("install", "codex")
} else {
  Write-Step "略過 agent hooks 安裝"
}

$resolvedProject = Resolve-ProjectDir
if ($resolvedProject) {
  Write-Step "初始化目前 git 專案：$resolvedProject"
  Invoke-ContextMemory @("init", "-Cwd", $resolvedProject, "-UpdateGitignore")
  Invoke-ContextMemory @("validate", "-Cwd", $resolvedProject)
  if (-not $NoDoctor) {
    Invoke-ContextMemory @("doctor", "-Cwd", $resolvedProject)
  }
} else {
  Write-Step "未指定專案，且目前目錄不是可初始化的 git repo；只安裝全域工具與 hooks。"
  Write-Step "之後可在專案根目錄執行：context-memory init -UpdateGitignore"
}

Write-Step "安裝完成。新開 terminal 後可直接使用：context-memory help"
