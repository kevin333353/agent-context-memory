# Claude Single-Session Guard Design

## Goal

Reduce Claude Code input-context growth inside one interactive session without replacing Claude Code's native session UX. The feature is opt-in per repository and complements context-memory injection; it does not claim that hooks can delete or rewrite Claude's existing message history.

## User Interface

The CLI adds these commands:

```powershell
context-memory single-session enable -Cwd <repo-root> -ThresholdTokens 40000
context-memory single-session status -Cwd <repo-root>
context-memory single-session disable -Cwd <repo-root>
```

`enable` initializes context memory when necessary, enables the project guard, installs the managed Claude hooks, and configures Claude's project-local auto-compact window to `100000` tokens. `disable` removes the guard and restores the prior project-local auto-compact value when it is still safe to do so. `status` reports the configured threshold, most recently observed provider input size, post-compact baseline, next effective threshold, and whether the auto-compact fallback is managed.

## Configuration

`.context-memory/config.yaml` gains:

```yaml
single_session_guard:
  enabled: false
  threshold_tokens: 40000
  min_growth_after_compact_tokens: 10000
  block_on_threshold: true
  auto_compact_window_tokens: 100000
```

Runtime state is stored in `.context-memory/single-session-guard.json`. It is local, bounded, contains no prompt text, and is added to context-memory's managed `.gitignore` rules. It records the transcript identity, last compact byte offset, first post-compact provider-input baseline, last observed input tokens, and reversible ownership metadata for Claude's project-local auto-compact setting.

## Token Measurement

A new Python module reads the Claude JSONL path supplied in the hook payload. It prefers the latest unique provider `usage` record and computes:

```text
input_tokens + cache_creation_input_tokens + cache_read_input_tokens
```

This is the actual input-side size reported for the preceding Claude request. The guard does not treat it as billed cost. If usage metadata or the transcript is unavailable, malformed, truncated, or outside the project, the guard fails open and writes a bounded diagnostic instead of blocking.

The effective threshold is `max(threshold_tokens, post_compact_baseline + min_growth_after_compact_tokens)`. Before a post-compact baseline exists, it is `threshold_tokens`. This prevents a large static system/tool prompt from causing an immediate compact loop.

## Hook Flow

Claude receives managed hooks for `UserPromptSubmit`, `SessionStart`, `SubagentStart`, `PreCompact`, and `PostCompact`. Codex hooks remain unchanged because this feature targets Claude Code's interactive session behavior.

On `UserPromptSubmit`:

1. Run normal context-memory initialization, journaling, and injection.
2. When the guard is disabled, preserve the current output exactly.
3. When enabled, inspect the latest provider input size.
4. If below the effective threshold, inject context normally.
5. If at or above it, synchronously checkpoint pending journal events, then return Claude's supported `decision: block` response with the observed size and the exact `/compact` command to run.
6. Slash command input beginning with `/compact` is never blocked if Claude forwards it through the hook.

The block persists for ordinary prompts until a compact boundary is observed. The user then resubmits the original prompt after `/compact` completes.

On `PreCompact`, record the transcript offset and synchronously attempt a state checkpoint. Checkpoint errors and timeouts never cancel Claude's compaction.

On `PostCompact`, persist Claude's compact summary using the existing flow, clear the block, and wait for the first new provider usage record to establish the post-compact baseline. `SessionStart` with source `clear` resets the same guard state; source `compact` preserves the boundary written by compact hooks.

## Claude Settings Ownership

The feature writes only `autoCompactWindow: 100000` in `<repo>/.claude/settings.local.json` and preserves every unrelated property. Before changing it, the guard state stores whether the property existed and its prior value.

On disable, the CLI restores the previous value only when the current value still equals the managed value. If the user changed it after enablement, the CLI leaves it untouched and prints a warning. Existing global environment overrides such as `CLAUDE_CODE_AUTO_COMPACT_WINDOW` take precedence; `status` and `doctor` report that condition rather than pretending the project-local setting is active.

## Context Isolation Guidance

The installed Claude skill gains stable guidance to keep large searches, test logs, and report generation in subagents or artifacts, returning only paths and short summaries to the main session. This reduces context growth before the guard threshold without adding changing instructions to every hook payload.

## Failure Behavior

- Guard disabled: no behavior change from v0.2.2.
- Missing transcript or usage: allow the prompt and log a diagnostic.
- Checkpoint failure: still show the compact block; `/compact` remains usable.
- PreCompact failure: compaction proceeds.
- Invalid guard state: replace it with safe defaults and continue.
- Claude settings conflict: preserve the user's newer value and warn.
- Worker recursion: retain `CONTEXT_MEMORY_WORKER_CHILD` protections so checkpoint model calls do not recursively trigger the guard.

## Testing

Tests cover provider usage extraction, request deduplication, threshold evaluation, post-compact baseline learning, malformed/truncated JSONL, state reset, settings preservation/restoration, and fail-open behavior.

PowerShell protocol tests execute the exact managed Claude hook command with synthetic transcripts and verify normal injection below threshold, supported block JSON above threshold, `/compact` bypass, `PreCompact`/`PostCompact` transitions, Claude-only hook installation, uninstall cleanup, and unchanged Codex behavior.

Release verification includes all Python and PowerShell suites, parser and compile checks, an isolated profile/repository installation, exact hook-boundary execution, and Claude Code `doctor`. Because reaching 40k tokens with a paid live model is expensive and nondeterministic, release tests use real Claude usage-shaped JSONL fixtures rather than spending tokens solely to cross the threshold.

## Release

This is a feature release targeted as v0.3.0. README and changelog claims distinguish raw provider input size from billed cost and state clearly that the guard coordinates compaction rather than deleting history itself.
