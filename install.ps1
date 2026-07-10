param(
  [string]$RepoUrl = "https://github.com/kevin333353/agent-context-memory.git",
  [string]$Branch = "main",
  [string]$InstallDir = (Join-Path $env:USERPROFILE ".agent-context-memory"),
  [string]$ProjectDir = "",
  [switch]$NoPath,
  [switch]$NoClaude,
  [switch]$NoCodex,
  [switch]$NoProjectInit,
  [switch]$NoDoctor,
  [switch]$SkipRepositorySync
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

function Test-GitRef([string]$RepoDir, [string]$Ref) {
  & git -C $RepoDir rev-parse --verify --quiet $Ref *> $null
  return ($LASTEXITCODE -eq 0)
}

function Sync-RequestedGitRef {
  Write-Step "同步版本：$Branch"
  & git -C $InstallDir fetch --quiet --prune --tags origin
  if ($LASTEXITCODE -ne 0) {
    throw "git fetch 失敗。"
  }

  $remoteBranchRef = "refs/remotes/origin/$Branch"
  $tagCommitRef = "refs/tags/$Branch^{commit}"

  if (Test-GitRef $InstallDir $remoteBranchRef) {
    & git -C $InstallDir checkout -q $Branch
    if ($LASTEXITCODE -ne 0) {
      & git -C $InstallDir checkout -q -B $Branch "origin/$Branch"
      if ($LASTEXITCODE -ne 0) {
        throw "git checkout $Branch 失敗。"
      }
    }
    & git -C $InstallDir pull --ff-only --quiet origin $Branch
    if ($LASTEXITCODE -ne 0) {
      throw "git pull --ff-only 失敗；請確認安裝目錄沒有本機修改：$InstallDir"
    }
    return
  }

  if (Test-GitRef $InstallDir $tagCommitRef) {
    & git -c advice.detachedHead=false -C $InstallDir checkout -q $tagCommitRef
    if ($LASTEXITCODE -ne 0) {
      throw "git checkout tag $Branch 失敗。"
    }
    return
  }

  throw "找不到 branch 或 tag：$Branch"
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
    Sync-RequestedGitRef
  } else {
    $parent = Split-Path -Parent $InstallDir
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    Write-Step "clone 工具到 $InstallDir"
    & git clone --quiet $RepoUrl $InstallDir
    if ($LASTEXITCODE -ne 0) {
      throw "git clone 失敗。"
    }
    Sync-RequestedGitRef
  }
}

function Install-ManagedPython {
  if (-not (Test-CommandExists "python")) {
    throw "找不到 Python。Agent Context Memory v0.2.0 需要 Python 3.9 以上。"
  }
  $python = (Get-Command python -CommandType Application).Source
  & $python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)"
  if ($LASTEXITCODE -ne 0) {
    throw "Python 版本過舊。Agent Context Memory v0.2.0 需要 Python 3.9 以上。"
  }

  $venvRoot = Join-Path $InstallDir ".venv"
  $managedPython = Join-Path $venvRoot "Scripts\python.exe"
  if (-not (Test-Path -LiteralPath $managedPython)) {
    Write-Step "建立工具專用 Python virtual environment"
    & $python -m venv $venvRoot
    if ($LASTEXITCODE -ne 0) {
      throw "建立 Python virtual environment 失敗：$venvRoot"
    }
  }

  $requirements = Join-Path $InstallDir "requirements.txt"
  if (-not (Test-Path -LiteralPath $requirements)) {
    throw "找不到 Python dependencies：$requirements"
  }
  Write-Step "安裝固定版本 Python dependencies"
  & $managedPython -m pip install --quiet --disable-pip-version-check -r $requirements
  if ($LASTEXITCODE -ne 0) {
    throw "安裝 Python dependencies 失敗。"
  }
  & $managedPython -c "import yaml"
  if ($LASTEXITCODE -ne 0) {
    throw "Managed Python 無法載入 PyYAML。"
  }
  Write-Step "Managed Python ready：$managedPython"
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
if ($SkipRepositorySync) {
  if (-not (Test-Path -LiteralPath (Join-Path $InstallDir "context-memory.ps1"))) {
    throw "SkipRepositorySync 需要既有工具 source checkout：$InstallDir"
  }
  Write-Step "略過 repository 同步，使用既有 source checkout：$InstallDir"
} else {
  Install-Repository
}
Install-ManagedPython

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
  Write-Step "本次初始化專案數：0。已安裝全域工具與 hooks。"
  Write-Step "第一次進入符合條件的 git repo 時，hook 會自動初始化；也可手動執行 context-memory init -UpdateGitignore。"
}

Write-Step "安裝完成。新開 terminal 後可直接使用：context-memory help"
