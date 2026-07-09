# Agent Context Memory

Prompt-cache-aware context memory for long-running coding agents.

`context-memory/v1` keeps agent session state in a small YAML memory table and injects it through CLI hooks. Static rules stay in global instructions for prompt-cache reuse; dynamic state stays in `.context-memory/state.yaml`; large logs, diffs, and reports stay as files.

## What It Does

- Injects compact project memory into Claude Code and Codex CLI sessions.
- Keeps one protocol across agent frameworks through adapters.
- Supports `UserPromptSubmit`, `SessionStart`, `SubagentStart`, and `PostCompact`.
- Records lightweight events to SQLite for background summarization.
- Provides token-savings benchmarks for synthetic conversations and Claude Code transcripts.

## Install

Recommended Windows install path:

```powershell
git clone <repo-url> "$env:USERPROFILE\.agent-context-memory"
```

Optionally add it to `PATH`:

```powershell
[Environment]::SetEnvironmentVariable(
  "Path",
  [Environment]::GetEnvironmentVariable("Path", "User") + ";$env:USERPROFILE\.agent-context-memory",
  "User"
)
```

Open a new terminal, then verify:

```powershell
context-memory help
```

If `PATH` is not configured, call it directly:

```powershell
& "$env:USERPROFILE\.agent-context-memory\context-memory.cmd" help
```

## Project Setup

In each repo that should use context memory:

```powershell
context-memory init -Cwd <repo-root> -UpdateGitignore
context-memory validate -Cwd <repo-root>
```

This creates `.context-memory/` files. Commit only shared files:

```text
.context-memory/schema.yaml
.context-memory/config.yaml
.context-memory/project.yaml
.context-memory/handoff/*.md
```

Keep personal runtime files local:

```text
.context-memory/state.yaml
.context-memory/history.md
.context-memory/last-compact.md
.context-memory/events.sqlite
```

## Agent Hooks

Install local hooks:

```powershell
context-memory install claude
context-memory install codex
context-memory doctor -Cwd <repo-root>
```

On Windows, Claude Code hooks use exec-form `command` + `args` so Git Bash/MSYS is not inserted between Claude Code and PowerShell.

## New Session Resume

For a new chat in the same project:

```powershell
context-memory resume -Cwd <repo-root>
```

Paste the output into the new session. If hooks are installed, the session should also receive `<CONTEXT_MEMORY_STATE>` automatically.

## Benchmarks

Synthetic conversation replay:

```powershell
python "$env:USERPROFILE\.agent-context-memory\benchmarks\simulate-token-savings.py" --turns 100 --chars-per-turn 3000 --state "<repo-root>\.context-memory\state.yaml"
```

Claude Code transcript usage:

```powershell
python "$env:USERPROFILE\.agent-context-memory\benchmarks\claude-code-usage-report.py" --cwd <repo-root>
```

Recent measured results are documented in [docs/benchmark-results.md](docs/benchmark-results.md).

## Repository Layout

```text
adapters/                    Agent CLI output adapters
benchmarks/                  Token savings and Claude transcript reports
docs/                        Guide and benchmark documentation
scripts/                     SQLite journal and fill-table worker
skills/context-memory/       Codex skill instructions
templates/.context-memory/   Commit-safe project templates
tests/                       Protocol smoke tests
context-memory.ps1           CLI
context-memory-hook.ps1      Hook entrypoint
context-memory-core.ps1      Protocol core
protocol.md                  context-memory/v1 contract
```

## Design Rule

Static interpretation rules belong in global agent instructions or skills. Hook output should contain only dynamic state:

```xml
<CONTEXT_MEMORY_STATE protocol="context-memory/v1">
  <STATE_YAML>
    ...
  </STATE_YAML>
</CONTEXT_MEMORY_STATE>
```

This keeps the stable prompt prefix cacheable while the dynamic memory table remains small.

