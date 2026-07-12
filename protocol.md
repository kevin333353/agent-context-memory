# Context Memory Protocol v1

This protocol keeps project memory independent from any specific coding agent.
Agent CLIs are adapters; the stable contract is the protocol event and the
`.context-memory/` files.

## Project Files

| Path | Meaning |
|---|---|
| `.context-memory/state.yaml` | Dynamic memory table injected by hooks |
| `.context-memory/schema.yaml` | Field meanings and update rules |
| `.context-memory/config.yaml` | Fill-table model cascade, validation, and journal policy |
| `.context-memory/history.md` | Append-only compact/session summaries |
| `.context-memory/last-compact.md` | Most recent compact summary |
| `.context-memory/events.sqlite` | Lightweight event journal for background summarization |
| `.context-memory/metadata.json` | Local initialization origin and timestamp |
| `.context-memory/diagnostics.log` | Bounded local hook/worker diagnostics without prompt text |
| `.context-memory/single-session-guard.json` | Local Claude token threshold, compact boundary, and settings ownership state |

## Automatic Initialization

On `SessionStart` or `UserPromptSubmit`, an absent memory root is initialized
at the git repository root when auto-init is enabled. Tool, profile, temporary,
non-git, and `.context-memory-disabled` roots are skipped. Initialization is
idempotent and the first event can inject the new state in the same hook run.

## Input Event

Adapters normalize framework-specific hook payloads into:

```json
{
  "protocol": "context-memory/v1",
  "event": "user_prompt_submit",
  "cwd": "D:\\project",
  "transcript_path": "C:\\Users\\name\\.claude\\projects\\...\\session.jsonl",
  "source": "startup|resume|compact",
  "compact_summary": "optional"
}
```

Supported events:

| Event | Meaning |
|---|---|
| `user_prompt_submit` | Inject memory before a prompt is processed |
| `session_start` | Inject memory when a session starts or resumes |
| `subagent_start` | Inject memory when a child agent starts |
| `pre_compact` | Checkpoint pending memory before Claude compacts |
| `post_compact` | Persist a compact summary into memory history |

## Core Output

The core returns:

```json
{
  "protocol": "context-memory/v1",
  "action": "inject",
  "context": "<CONTEXT_MEMORY_STATE>...</CONTEXT_MEMORY_STATE>",
  "block": false,
  "block_reason": ""
}
```

Actions:

| Action | Meaning |
|---|---|
| `inject` | Adapter should feed `context` into the agent |
| `saved_compact` | Compact summary was persisted; no context injection needed |
| `pre_compact` | Pre-compact boundary was recorded and checkpoint attempted |
| `initialized` | A `.context-memory/` folder was created |
| `none` | No memory was found or no action applies |

## Prompt Placement

Keep static table guidance in the earliest stable instruction layer available to
the host agent, above chat history. The hook must inject only dynamic table
content so prompt-cache prefixes can reuse the stable guidance.

Recommended order:

1. `sys_prompt` / global agent instructions: context-memory rules and field meanings.
2. `chat_history`: normal conversation transcript.
3. Hook `additionalContext`: `<CONTEXT_MEMORY_STATE>` with the current `state.yaml`.
4. `user_input`: the newest user prompt.

The dynamic block format is:

```xml
<CONTEXT_MEMORY_STATE protocol="context-memory/v1">
Location: .context-memory/state.yaml
Schema: .context-memory/schema.yaml

<STATE_YAML>
...
</STATE_YAML>
</CONTEXT_MEMORY_STATE>
```

## Adapter Rule

New frameworks must only translate between their hook format and this protocol.
Do not fork the memory schema or duplicate core logic.

Claude Code's opt-in single-session guard is adapter-specific output behavior:
when `block` is true, the Claude adapter returns top-level `decision: "block"`
and an actionable `/compact` reason. Codex ignores these fields and retains its
existing event set.

## Fill-Table Model Policy

Default routine summarization should use a small model and only escalate on
validation failure or conflict:

| Adapter | Routine | Repair / compact rebuild |
|---|---|---|
| `claude-code` | `haiku` | `sonnet` |
| `codex-cli` | `gpt-5-nano` | `gpt-5-mini` |

Hooks record redacted, bounded events to `.context-memory/events.sqlite`. Once
the unprocessed event threshold is reached, they launch the managed fill-table
worker as a detached process. Normal hooks remain non-blocking. An enabled
Claude single-session guard is the explicit exception: `PreCompact` and a
threshold block synchronously attempt one locked checkpoint before compaction.
Checkpoint failure is fail-open for compaction and never blocks `/compact`.

Use `scripts/fill_table_worker.py --dry-run` style runs first. Only write
`state.yaml` with `--apply` after the generated YAML passes validation.
