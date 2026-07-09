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

## Workflow

1. If the project lacks `.context-memory/state.yaml` and the user asks for context memory, initialize it:
   `powershell -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\.agent-context-memory\context-memory-hook.ps1" -Mode init`
2. At the start of work, prefer the injected `<CONTEXT_MEMORY_STATE>` block over old transcripts.
3. Read full transcripts only when the memory table is missing, contradicted, or the user explicitly asks.
4. After meaningful changes, update `state.yaml`: current task, decisions, files touched, open questions, and next action.
5. Keep stable interpretation rules above dynamic state. Put frequently changing details near the bottom to preserve prompt-cache prefixes.
6. Do not paste large logs or full conversations into memory. Store summaries plus file paths.
7. Use the configured fill-table cascade for background summarization: Claude Code routine `haiku`, repair/rebuild `sonnet`; Codex routine `gpt-5-nano`, repair/rebuild `gpt-5-mini`.

## Update Standard

Memory is useful only when it is specific. Write exact file paths, commands, dates, decisions, and blockers. Remove obsolete dynamic notes instead of accumulating stale context.

## Compact Handling

`PostCompact` hooks append compact summaries to `history.md` and `last-compact.md`. After compact or resume, reconcile those summaries into `state.yaml` before continuing substantial work.
