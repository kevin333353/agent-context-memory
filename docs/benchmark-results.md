# Benchmark Results

These numbers are offline estimates unless they come from Claude usage metadata. Provider usage metadata is the source of truth for billing.

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
