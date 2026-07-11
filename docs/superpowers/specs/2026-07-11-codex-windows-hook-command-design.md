# Codex Windows Hook Command Reliability Design

## Problem

Agent Context Memory v0.2.0 writes Codex hook commands as a quoted absolute
PowerShell executable followed by a quoted `-File` path. Codex CLI 0.144.1
runs command hooks through `cmd.exe /C` and passes the full hook command as one
process argument. On Windows, the embedded quotes reach `cmd.exe` as literal
`\"` characters. The executable is therefore not found and `SessionStart`
finishes with exit code 1.

The hook script itself is healthy: invoking the same script directly through
PowerShell exits 0. The fault is limited to the Codex Windows command launcher.

## Decision

Generate a Windows-specific Codex launcher in `commandWindows`. The launcher
will invoke Windows PowerShell with `-EncodedCommand`, using UTF-16LE Base64 as
required by Windows PowerShell. Its decoded script will:

1. set `$ProgressPreference` to `SilentlyContinue`;
2. invoke the absolute `context-memory-hook.ps1` path;
3. pass `-Adapter codex-cli`;
4. leave stdin attached so the hook can read Codex's JSON payload.

The outer command will contain no quoted paths and will be valid in both the
`cmd.exe` fallback and a PowerShell session environment:

```text
powershell.exe ... -EncodedCommand <base64>
```

This avoids `cmd.exe` quote corruption and continues to support tool paths
that contain spaces. The same launcher is stored in `command` as a
compatibility fallback and in `commandWindows` as the official Windows
override. A unique `statusMessage` retains the plaintext managed-hook marker
needed for idempotent update and uninstall behavior.

## Alternatives Rejected

- Remove all quotes from the current command. This only works while every path
  has no spaces and would fail for common Windows profile names.
- Add a `.cmd` wrapper. This moves the same path-quoting boundary into another
  managed artifact and increases installation and cleanup complexity.

## Testing

Add a regression assertion to the PowerShell protocol suite that loads the
installed Codex hook definition and runs its selected Windows command through
the same boundary used by Codex:

```text
cmd.exe /D /S /C <commandWindows>
powershell.exe -Command <commandWindows>
```

The test sends a valid `SessionStart` JSON object on stdin and requires exit
code 0 with no stderr. Before the implementation change, the test must fail
with the current quoted command. After the change, the full Python and
PowerShell suites must pass.

End-to-end verification will use Codex app-server 0.144.1 to start an
ephemeral thread and turn, then require the emitted `hook/completed` event to
report `status: completed`.

## Installation And Release

Reinstall the local Codex hook after the code change and verify the generated
`~/.codex/hooks.json`. Preserve unrelated hooks and existing project memory.
Publish a patch release because v0.2.0 installations already contain the
broken Windows launcher. Update the pinned README installer command and
changelog to the new patch version, then tag the final merged release commit.

## Scope

This change does not alter auto-initialization, memory state, background
workers, Claude Code hooks, or the context-memory protocol output.
