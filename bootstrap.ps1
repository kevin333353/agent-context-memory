$ErrorActionPreference = "Stop"

$installerUrl = "https://raw.githubusercontent.com/kevin333353/agent-context-memory/main/install.ps1"
$installerPath = Join-Path $env:TEMP "agent-context-memory-install.ps1"

Invoke-WebRequest -UseBasicParsing -Uri $installerUrl -OutFile $installerPath

$installerArgs = @()
if ($env:ACM_REPO_URL) { $installerArgs += @("-RepoUrl", $env:ACM_REPO_URL) }
if ($env:ACM_BRANCH) { $installerArgs += @("-Branch", $env:ACM_BRANCH) }
if ($env:ACM_INSTALL_DIR) { $installerArgs += @("-InstallDir", $env:ACM_INSTALL_DIR) }
if ($env:ACM_PROJECT_DIR) { $installerArgs += @("-ProjectDir", $env:ACM_PROJECT_DIR) }
if ($env:ACM_NO_PATH -eq "1") { $installerArgs += "-NoPath" }
if ($env:ACM_NO_CLAUDE -eq "1") { $installerArgs += "-NoClaude" }
if ($env:ACM_NO_CODEX -eq "1") { $installerArgs += "-NoCodex" }
if ($env:ACM_NO_PROJECT_INIT -eq "1") { $installerArgs += "-NoProjectInit" }
if ($env:ACM_NO_DOCTOR -eq "1") { $installerArgs += "-NoDoctor" }

& powershell -NoProfile -ExecutionPolicy Bypass -File $installerPath @installerArgs
exit $LASTEXITCODE
