---
name: context-memory
description: Use when managing long-running agent sessions, compact/resume handoffs, memory tables, context compression, or a project with `.context-memory/state.yaml`. Treat hook-injected `<CONTEXT_MEMORY_STATE>` as dynamic compact memory; keep these static rules above chat history for prompt-cache reuse.
---

# Context Memory

## Core Rule

Use `.context-memory/state.yaml` as the compact project memory table. It is dynamic context, not source of truth. If it conflicts with repository files or explicit user instructions, trust the original source and update the memory table.

Static interpretation rules belong in this skill or global agent instructions. The hook-injected `<CONTEXT_MEMORY_STATE>` block should contain only dynamic table content so the stable guidance can sit above `chat_history` and benefit from prompt-cache prefix reuse.

The stable contract is `context-memory/v1`; individual agent CLIs are thin adapters. For adapter details, read `$env:USERPROFILE\.agent-context-memory\protocol.md` only when adding support for a new framework.

## Files

| File | Purpose |
|---|---|
| `.context-memory/state.yaml` | Current compact memory injected by hooks |
| `.context-memory/schema.yaml` | Field meanings and update rules |
| `.context-memory/config.yaml` | Fill-table model cascade, validation, and journal policy |
| `.context-memory/history.md` | Append-only compact/session summaries |
| `.context-memory/last-compact.md` | Most recent compact summary captured by hook |
| `.context-memory/events.sqlite` | Lightweight event journal for background summarization |
| `.context-memory/single-session-guard.json` | Local Claude session threshold and compact-boundary state |

## Workflow

1. If the project lacks `.context-memory/state.yaml` and the user asks for context memory, initialize it:
   `powershell -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\.agent-context-memory\context-memory-hook.ps1" -Mode init`
2. At the start of work, prefer the injected `<CONTEXT_MEMORY_STATE>` block over old transcripts.
3. Read full transcripts only when the memory table is missing, contradicted, or the user explicitly asks.
4. The managed background worker updates `state.yaml` after the configured event threshold. Update it manually only when the user explicitly asks or when repairing a diagnosed worker failure.
5. Keep stable interpretation rules above dynamic state. Put frequently changing details near the bottom to preserve prompt-cache prefixes.
6. Do not paste large logs or full conversations into memory. Store summaries plus file paths.
7. Use the configured fill-table cascade for background summarization: Claude Code routine `haiku`, repair `sonnet`; Codex routine `gpt-5.4-mini`, repair `gpt-5.4`.

## Single-Session Discipline

When the repository enables `single_session_guard`, keep the main Claude Code
thread compact before the threshold is reached:

1. Delegate large searches, broad code inspection, and independent investigations to subagents.
2. Write long test output, logs, diffs, and reports to artifacts; return only the path and a short actionable summary.
3. When the guard blocks a prompt, run the exact `/compact` command it provides, then resubmit the original prompt.
4. After compact, use the injected memory table and compact summary instead of replaying the discarded transcript.
5. Do not claim raw provider-input reduction is identical to billed-cost savings; prompt-cache pricing remains separate.

## Update Standard

Memory is useful only when it is specific. Write exact file paths, commands, dates, decisions, and blockers. Remove obsolete dynamic notes instead of accumulating stale context.

## Compact Handling

`PostCompact` hooks append compact summaries to `history.md` and `last-compact.md`. After compact or resume, reconcile those summaries into `state.yaml` before continuing substantial work.
