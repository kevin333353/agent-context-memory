# Agent Context Memory

面向長任務 coding agent 的 prompt-cache-aware 上下文記憶工具。

`context-memory/v1` 的核心想法是：不要讓 agent 每一輪都重讀完整聊天歷史，而是把目前任務狀態壓成一份小型 `.context-memory/state.yaml`，再透過 Claude Code / Codex CLI hook 注入。穩定規則放在全域指導層吃 prompt cache，動態狀態放在記憶表，大型 log、diff、report 則用檔案路徑交接。

## 解決什麼問題

- 長對話越跑越貴，每輪 input token 越來越大。
- compact 後模型容易忘記重要決策與下一步。
- subagent 或新 session 啟動時，常重複注入大量背景。
- 工具輸出、diff、log 一旦貼進聊天，就會長期佔用上下文。
- prompt cache 命中很高時，仍然很難判斷到底省了多少 token。

## 這套工具做什麼

- 透過 hook 把 `.context-memory/state.yaml` 注入 Claude Code / Codex CLI。
- 用同一套 `context-memory/v1` protocol 支援不同 agent CLI adapter。
- 支援 `UserPromptSubmit`、`SessionStart`、`SubagentStart`、`PostCompact`。
- 把 hook 事件寫進 `.context-memory/events.sqlite`，方便之後用背景 worker 整理記憶表。
- 提供 synthetic benchmark 與 Claude Code transcript usage report，量測 token savings。

## 安裝

### Windows 一行安裝

在 PowerShell 裡執行這一行：

```powershell
$d="$env:TEMP\agent-context-memory-installer"; if (Test-Path $d) { Remove-Item -Recurse -Force $d }; git clone --quiet https://github.com/kevin333353/agent-context-memory.git $d; git -c advice.detachedHead=false -C $d checkout -q "refs/tags/v0.2.0^{commit}"; powershell -NoProfile -ExecutionPolicy Bypass -File "$d\install.ps1" -Branch v0.2.0
```

這條命令會先用 `git clone` 下載固定版本的 installer，再用 `-File` 執行本機檔案；不要用 `iex` 直接執行遠端內容，Windows PowerShell 對 `param(...)`、UTF-8 BOM、中文輸出會比較容易踩到邊界問題。

如果要追最新 `main`：

```powershell
$d="$env:TEMP\agent-context-memory-installer"; if (Test-Path $d) { Remove-Item -Recurse -Force $d }; git clone --quiet https://github.com/kevin333353/agent-context-memory.git $d; powershell -NoProfile -ExecutionPolicy Bypass -File "$d\install.ps1"
```

這會自動完成：

- clone/update 到 `%USERPROFILE%\.agent-context-memory`
- 把工具目錄加入使用者 `PATH`
- 建立工具專用 `.venv` 並安裝固定版本的 PyYAML
- 安裝 Claude Code hooks
- 安裝 Codex hooks
- 安裝 Claude Code / Codex 的 context-memory skill
- 如果目前 terminal 位於某個 git repo 內，會順便初始化該 repo 的 `.context-memory/`
- 之後第一次進入其他 git repo 時，hook 會安全地自動初始化該 repo
- 若有初始化專案，最後執行 `validate` / `doctor` 做檢查

`v0.1.8` 安裝指令是固定版本 pin，不會自動變成新版。已使用舊指令的同事需要把 tag 與 `-Branch` 都改成 `v0.2.0`，再執行一次上面的新版指令。不要修改或重用舊 tag，否則同一條安裝命令將失去可重現性。

Auto-init 只會作用在有效 git repository root，不會在工具 repo、使用者家目錄、TEMP 或一般非 git 目錄建立檔案。若某個 repo 不應啟用，先在 repo root 建立 `.context-memory-disabled`。

如果只想安裝工具與 hooks，不想初始化目前專案：

```powershell
$d="$env:TEMP\agent-context-memory-installer"; if (Test-Path $d) { Remove-Item -Recurse -Force $d }; git clone --quiet https://github.com/kevin333353/agent-context-memory.git $d; git -c advice.detachedHead=false -C $d checkout -q "refs/tags/v0.2.0^{commit}"; powershell -NoProfile -ExecutionPolicy Bypass -File "$d\install.ps1" -Branch v0.2.0 -NoProjectInit
```

如果要明確指定專案：

```powershell
$d="$env:TEMP\agent-context-memory-installer"; if (Test-Path $d) { Remove-Item -Recurse -Force $d }; git clone --quiet https://github.com/kevin333353/agent-context-memory.git $d; git -c advice.detachedHead=false -C $d checkout -q "refs/tags/v0.2.0^{commit}"; powershell -NoProfile -ExecutionPolicy Bypass -File "$d\install.ps1" -Branch v0.2.0 -ProjectDir "D:\your-project"
```

### Linux / macOS

目前公開的一鍵安裝器是 Windows PowerShell 版本。Linux / macOS 不要直接複製上面的 PowerShell 指令，bash 會找不到 `$env:TEMP`、`iwr`、`powershell`。

Linux / macOS 原生 `install.sh` 還在整理中；在那之前，建議先在 Windows 環境使用，或手動參考 `protocol.md` 接 adapter。

## 手動安裝

建議在 Windows 上 clone 到這個位置：

```powershell
git clone <repo-url> "$env:USERPROFILE\.agent-context-memory"
```

可選：把工具目錄加到使用者 `PATH`：

```powershell
[Environment]::SetEnvironmentVariable(
  "Path",
  [Environment]::GetEnvironmentVariable("Path", "User") + ";$env:USERPROFILE\.agent-context-memory",
  "User"
)
```

重新開一個 terminal 後確認：

```powershell
context-memory help
```

如果沒有設定 `PATH`，也可以直接執行：

```powershell
& "$env:USERPROFILE\.agent-context-memory\context-memory.cmd" help
```

## 專案初始化

在要使用 context memory 的 repo 裡執行：

```powershell
context-memory init -Cwd <repo-root> -UpdateGitignore
context-memory validate -Cwd <repo-root>
```

初始化後會產生 `.context-memory/`。建議提交給團隊共用的檔案：

```text
.context-memory/schema.yaml
.context-memory/config.yaml
.context-memory/project.yaml
.context-memory/handoff/*.md
```

建議保持本機、不提交的個人 session 檔案：

```text
.context-memory/state.yaml
.context-memory/history.md
.context-memory/last-compact.md
.context-memory/events.sqlite
```

## 安裝 Agent Hook

在每位使用者自己的機器上安裝 hook：

```powershell
context-memory install claude
context-memory install codex
context-memory doctor -Cwd <repo-root>
```

Windows 上 Claude Code hook 會使用 exec-form `command` + `args`，直接呼叫 Windows PowerShell，避免被 Git Bash/MSYS 包一層後出現 `add_item errno 1`。

如果 Codex 開新對話時顯示 `SessionStart hook (failed)`，但送出訊息後看到 `UserPromptSubmit hook (completed)` 與 `<CONTEXT_MEMORY_STATE>`，代表使用者訊息仍有成功注入。建議更新到 `v0.1.8` 以上並重新安裝 Codex hook：

```powershell
context-memory install codex
context-memory doctor -Cwd <repo-root>
```

## 停用 / 移除

如果後續不想再讓 Claude Code / Codex 注入 context memory，先移除 hooks：

```powershell
context-memory uninstall all
```

也可以只移除單一 agent：

```powershell
context-memory uninstall claude
context-memory uninstall codex
```

這個指令只會移除 `.claude/settings.json` 與 `.codex/hooks.json` 裡的 context-memory hooks，不會刪除專案內的 `.context-memory/`，也不會刪除全域工具目錄。這樣可以保留既有記憶、history 與 handoff，之後要重新啟用只要再執行：

```powershell
context-memory install all
```

若要完整清掉本機工具，可在移除 hooks 後手動刪除：

```powershell
Remove-Item -Recurse -Force "$env:USERPROFILE\.agent-context-memory"
```

若某個專案也不再需要記憶檔，再由專案 owner 決定是否移除該 repo 的 `.context-memory/` 與 `.gitignore` 中的 context-memory 規則。

## 開新 Session 怎麼接續

同一個專案開新聊天時：

```powershell
context-memory resume -Cwd <repo-root>
```

把輸出的中文 resume prompt 貼到新 session。若 hook 正常，新 session 也會自動看到 `<CONTEXT_MEMORY_STATE>`；resume prompt 的作用是提醒 agent 優先使用記憶表，不要重讀完整舊 transcript。

## Prompt 裡的理想分層

```text
Static system/developer/global instructions
Static context-memory interpretation rules
Tool and skill descriptions
Dynamic <CONTEXT_MEMORY_STATE>
Chat history
Latest user_input
```

重點是穩定規則要放前面並保持不變，讓 prompt cache 更容易命中；hook 注入只放動態記憶表，且保持小。

## Benchmark

Synthetic 多輪對話估算：

```powershell
python "$env:USERPROFILE\.agent-context-memory\benchmarks\simulate-token-savings.py" --turns 100 --chars-per-turn 3000 --state "<repo-root>\.context-memory\state.yaml"
```

Claude Code transcript usage 分析：

```powershell
python "$env:USERPROFILE\.agent-context-memory\benchmarks\claude-code-usage-report.py" --cwd <repo-root>
```

目前離線 input replay 上限估算：

| 情境 | 節省比例 |
|---|---:|
| Synthetic 10 輪，每輪 3000 chars | 36.25% |
| Synthetic 30 輪，每輪 3000 chars | 78.04% |
| Synthetic 100 輪，每輪 3000 chars | 92.63% |
| Synthetic 50 輪，每輪 6000 chars | 93.32% |
| Claude Code 最新主 session replay 上限 | 96.42% |

這些數字只估算 input context 壓力，不代表 compact 後的任務成功率，也不是帳單節省保證。舊版曾公布的跨四個 transcript `98.57%` 已撤回，原因是 replay accumulator 沒有在 session 邊界重設。

更完整的結果見 [docs/benchmark-results.md](docs/benchmark-results.md)。

## 專案結構

```text
adapters/                    Agent CLI output adapters
benchmarks/                  Token savings 與 Claude transcript 報告
docs/                        教學與 benchmark 文件
scripts/                     SQLite journal 與 fill-table worker
skills/context-memory/       Codex skill 指令
templates/.context-memory/   可提交的專案範本
tests/                       Protocol smoke tests
context-memory.ps1           CLI
bootstrap.ps1                一行安裝入口
install.ps1                  一鍵安裝器
context-memory-hook.ps1      Hook 入口
context-memory-core.ps1      Protocol core
protocol.md                  context-memory/v1 contract
```

## 設計原則

### 記憶表不是事實來源

`.context-memory/state.yaml` 是 compact memory，不是資料庫真相。若它和原始碼、文件、git、測試結果或使用者明確指令衝突，以原始來源為準，並更新記憶表。

### 穩定和動態要分離

```text
穩定規則 -> CLAUDE.md / AGENTS.md / skill
動態狀態 -> .context-memory/state.yaml
事件記錄 -> .context-memory/events.sqlite
大型內容 -> artifact files
```

### 不要把大內容貼進 prompt

大型 log、diff、測試輸出、完整 report 應該寫成檔案，再把路徑交給 agent 或 subagent。這比把內容貼進 chat history 更容易控制 token。

### Subagent 只吃任務邊界和路徑

省 token 的 subagent 模式是：controller 建立 task brief / report path / review package path，subagent 自己讀檔並回傳短摘要。不要把完整主線歷史複製給每個 subagent。

## 核心格式

Hook 注入的動態 block 長這樣：

```xml
<CONTEXT_MEMORY_STATE protocol="context-memory/v1">
Location: .context-memory/state.yaml
Schema: .context-memory/schema.yaml

<STATE_YAML>
...
</STATE_YAML>
</CONTEXT_MEMORY_STATE>
```

這個 block 只放動態狀態。欄位解釋、更新規則、衝突處理規則應該放在全域指導或 skill 裡，讓 prompt-cache prefix 更穩定。

## 常用指令

```powershell
context-memory init -Cwd <repo-root> -UpdateGitignore
context-memory install claude
context-memory install codex
context-memory uninstall all
context-memory doctor -Cwd <repo-root>
context-memory validate -Cwd <repo-root>
context-memory status -Cwd <repo-root>
context-memory resume -Cwd <repo-root>
context-memory benchmark
```

## 一句話總結

`context-memory/v1` 是 agent session state management：把長任務上下文拆成「穩定規則、動態記憶、大型 artifact」，讓 agent 讀正確狀態，而不是每輪重放完整聊天歷史。
