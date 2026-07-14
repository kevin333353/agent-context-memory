# Benchmark Results

These numbers are offline estimates unless they come from provider usage
metadata. Provider usage metadata is the source of truth for token accounting;
cached-input pricing and subscription quotas vary by provider and plan.

## Provider A/B Pilot: 2026-07-13

This pilot compared a full historical transcript against an equivalent
`CONTEXT_MEMORY_STATE` injection. The state was 803 bytes (about 200 tokens),
the baseline project contained `.context-memory-disabled`, and the memory
project passed hook-isolation checks. The background summarizer was disabled
for these runs so its own model calls could not affect usage.

Both arms had to recover all eight approved configuration values. Every result
in the tables below passed that quality gate.

### Cross-Session Recall

`Observed removable delta` is the difference between provider-reported input
for the full-history and memory arms. It is more useful than the requested
fixture label because tokenizers differ between providers.

| Provider | Fixture history chars | Baseline input | Memory input | Observed removable delta | Total input reduction |
|---|---:|---:|---:|---:|---:|
| Claude Code Sonnet | 163,913 | 72,238 | 9,918 | 62,320 | 86.27% |
| Claude Code Sonnet | 413,791 | 171,042 | 9,918 | 161,124 | 94.20% |
| Claude Code Sonnet | 831,593 | 337,042 | 9,918 | 327,124 | 97.06% |
| Codex configured default | 163,913 | 51,652 | 30,466 | 21,186 | 41.02% |
| Codex configured default | 413,791 | 121,970 | 14,443 | 107,527 | 88.16% |
| Codex configured default | 831,593 | 233,970 | 10,529 | 223,441 | 95.50% |

The first Codex row is a single-run outlier: its memory arm received a much
larger fixed/cached context than the later runs. Treat it as a variance signal,
not as a stable 40k estimate. The 100k and 200k rows show the expected result:
as history dominates fixed agent context, total input reduction approaches the
history-compression ratio.

### Real Coding Task

For the 413,791-character history fixture, each agent had to change only
`atlas_config.py` and run its test suite. Both baseline and memory arms passed
the test, changed the configuration file, and left the test file unchanged.

| Provider | Baseline input | Memory input | Saved tokens | Total input reduction | Quality |
|---|---:|---:|---:|---:|---|
| Claude Code Sonnet | 806,197 | 248,621 | 557,576 | 69.16% | Test passed |
| Codex configured default | 771,880 | 58,511 | 713,369 | 92.42% | Test passed |

Tool calls, command output, and multi-turn agent reasoning remain in the
context, which is why real coding savings can be lower than a one-turn recall
test even when quality remains unchanged.

### Native Compact Status

No native-compact saving is claimed from this pilot. Codex app-server's
`thread/compact/start` RPC was verified on a short thread, but 60k and 160k
history turns did not finish within 180 seconds. Claude Code's print-mode
`/compact` did not produce a durable compact marker suitable for a controlled
comparison; the subsequent quality check failed. These are integration gaps to
resolve before treating native compact as a benchmark arm.

This is one controlled run per provider and case, not a statistical claim.
Public performance claims should repeat each arm in randomized order, report
median/p95 and cache state, and include representative repository tasks.

## Synthetic Replay: Offline Upper Bound

State size at measurement time: about 1.4k tokens.

| Scenario | Total Saved |
|---|---:|
| 10 turns, 3000 chars/turn | 36.25% |
| 30 turns, 3000 chars/turn | 78.04% |
| 100 turns, 3000 chars/turn | 92.63% |
| 50 turns, 6000 chars/turn | 93.32% |

Short sessions can look worse at the beginning because the memory table has a fixed startup cost. The savings become significant as raw chat history grows.

## Claude Code Transcript Replay: Offline Upper Bound

| Dataset | Replay Saved |
|---|---:|
| Latest main session | 96.42% |

The previously published `98.57%` four-transcript aggregate is withdrawn. The
old implementation carried running context across independent transcript
files. The corrected report resets replay state per transcript and emits
per-session rows before aggregating them.

Observed Claude usage metadata for the combined run:

| Metric | Value |
|---|---:|
| Requests | 810 |
| Input-side total tokens | 234,251,963 |
| Cache hit share | 96.66% |
| Weighted input equivalent estimate | 38,140,304.2 |
| Latest request memory context | 2,393 tokens |
| Latest request upper-bound replaceable context | 99.53% |

## Interpretation

Prompt cache and context memory measure different effects:

- Prompt cache reduces the cost of stable repeated prompt prefixes.
- Context memory reduces the need to replay long chat history.
- Artifact handoff prevents large logs, diffs, and reports from becoming permanent chat history.

The most reliable evaluation is layered: inspect provider usage metadata,
per-session replay estimates, memory context size, output tokens, and subagent
usage separately. These replay estimates measure input-context pressure only;
they do not establish task quality after compression or guaranteed billing
savings.
