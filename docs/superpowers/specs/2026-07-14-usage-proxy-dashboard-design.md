# Usage Proxy and Dashboard Design

## Goal

Give the user real, measured token usage and prompt-cache-hit data for their daily
Claude Code and Codex CLI work, surfaced in a local dashboard. Today the project only
*estimates* savings from synthetic benchmarks and offline transcript replay; this feature
adds a live, observed source of truth.

Both CLIs are used through **subscription** accounts (Claude Pro/Max OAuth, Codex via
ChatGPT login), which constrains how the data can be captured:

- **Claude Code** honors `ANTHROPIC_BASE_URL` in subscription mode, its responses still
  carry the full `usage` block, and a local transparent forwarding proxy is technically
  workable. Using the subscription OAuth token through a self-hosted proxy is a ToS gray
  area; the user has accepted this risk for **local, self-use-only** operation.
- **Codex** in ChatGPT-subscription mode routes to an internal backend and cannot be
  cleanly redirected via a custom base URL, so it is **not** proxied. Codex writes token
  usage to local session logs, which are read instead.

The result is a **hybrid**: Claude via proxy (live), Codex via local-log tailing, both
normalized into one store and one dashboard. Measurement never alters request content and
never degrades either CLI.

## Non-Goals

- No content compression or request rewriting (this is observability only; unlike
  headroom, the token savings continue to come from upstream context-memory hooks).
- No proxying of Codex. No OS-level transparent interception.
- No billing claims. Subscription usage has token counts but no per-token bill; any dollar
  figure shown is an illustrative Anthropic-API-price conversion, clearly labeled.
- No redistribution or sharing of OAuth tokens; the proxy is loopback-only.

## Architecture

```
Claude Code ──ANTHROPIC_BASE_URL──▶ [proxy: forward + tee + parse] ──▶ api.anthropic.com
                                            │
Codex CLI ──(unmodified)──▶ ~/.codex logs  │  ◀── [ingest.codex: tail + parse]
                                            ▼
                                     usage.sqlite (normalized usage_events)
                                            ▼
                            [dashboard: embedded HTML + /api/* JSON]
```

One Python application (`scripts/usage`, run as `python -m usage` with
`<tool-root>/scripts` on the path) runs as a background process and contains
internally isolated modules — **pure standard library, zero new dependencies**
(the project's only dependency remains PyYAML). This keeps the offline/Windows
install story intact and lets every module run under the existing stdlib
`unittest` suite with no `pip install`.

| Module | Responsibility | Deps |
|---|---|---|
| `store` | SQLite schema, normalized writes, aggregate queries (thread-safe) | sqlite3 |
| `anthropic_usage` | Parse Anthropic streaming/non-streaming `usage` | stdlib |
| `codex_ingest` | Tail `~/.codex` rollout logs, normalize, dedupe | stdlib |
| `proxy` | Forward Claude traffic verbatim (`http.server` + `http.client`), tee SSE, parse usage | stdlib |
| `dashboard` | Embedded HTML page + `/__acm/api/*` JSON | stdlib |
| `pricing` | Illustrative list-price conversion (labeled non-billing) | stdlib |
| `__main__` | Wire proxy + background Codex tailer; run | stdlib |
| `cli` (PowerShell) | `proxy start/stop/status`, `enable/disable claude` | existing `context-memory.ps1` |

The application uses the project's managed Python (`python-resolver.ps1` / managed
`.venv`). It is launched with `PYTHONPATH=<tool-root>/scripts` and `-m usage` so a
shadowing top-level `scripts` package (shipped by some distributions such as
Anaconda) cannot mask the project modules.

## Phase 0 — Discovery and Verification Spike

Before building the full system, a minimal spike confirms the two subscription-related
assumptions on the user's actual machine. If either fails, work stops and the finding is
reported rather than building on a false premise.

1. **Claude proxy reachability.** Stand up a ~30-line transparent forwarder, set
   `ANTHROPIC_BASE_URL` to it, run one real Claude Code prompt, and confirm: requests
   arrive at the forwarder; the OAuth `Authorization` header passes through and
   api.anthropic.com accepts it; the (streaming) response contains a `usage` block; and
   Claude Code behaves normally. Capture one redacted request/response sample.
2. **Codex log format.** Locate the Codex session/rollout files under `~/.codex`, capture
   a sample, and confirm the on-disk JSON carries per-turn `usage`
   (`prompt_tokens`, `completion_tokens`, `prompt_tokens_details.cached_tokens`).

Outputs: confirmed wire and log samples that become fixtures for the parsers.

## Proxy (Claude)

The proxy listens on `127.0.0.1:8788` and forwards every request path verbatim to
`https://api.anthropic.com`, preserving all request headers unchanged (`Authorization`,
`anthropic-version`, `anthropic-beta`, `content-type`, etc.). Claude Code is pointed at it
with `ANTHROPIC_BASE_URL=http://127.0.0.1:8788`; it appends `/v1/messages` itself. The
loopback hop is plain HTTP, so no TLS certificate is involved; the proxy makes its own
HTTPS call upstream.

For `/v1/messages`, usage is captured without buffering the response:

- **Streaming (SSE):** upstream chunks are yielded to Claude Code byte-for-byte while a
  side parser reads the SSE events, taking `input_tokens` / `cache_creation_input_tokens`
  / `cache_read_input_tokens` / `service_tier` from `message_start` and the final
  `output_tokens` from `message_delta`.
- **Non-streaming:** the JSON body's `usage` object is read after forwarding.

On completion the proxy writes one normalized `usage_events` row (source `claude`, ingest
`proxy`).

**Fail-open is mandatory.** Any error in the proxy — upstream failure, parse failure,
store failure — must still forward the request/response and must never break Claude Code. A
measurement failure at worst drops one row and writes a bounded diagnostic. The proxy adds
no request-blocking behavior of any kind.

The process is launched detached with `CREATE_NO_WINDOW | DETACHED_PROCESS` (the pattern
already used by the worker in `context_memory_dispatch.py`) so no console window appears.

## Codex Ingest (log tailer)

A background task watches the Codex session directory under `~/.codex`, parses newly
appended JSONL entries, and extracts per-turn usage. Progress is tracked by file offset and
event identity so records are not double-counted across polls or restarts.

**Normalization note:** OpenAI-shape `prompt_tokens` *includes* cached tokens, so the
store records `input_tokens = prompt_tokens - cached_tokens` and
`cache_read_tokens = cached_tokens`. Codex subscription has no cache-creation concept, so
`cache_creation_tokens` is recorded as `0`. Rows are written with source `codex`, ingest
`log`.

If the log path or format is not what Phase 0 observed, the tailer logs a bounded
diagnostic and idles rather than crashing.

## Storage: `usage.sqlite`

A global database at `%USERPROFILE%\.agent-context-memory\usage\usage.sqlite`, separate
from the per-repo `.context-memory/events.sqlite` because usage spans all repositories and
sessions. Single table:

```text
usage_events(
  id INTEGER PRIMARY KEY,
  ts_utc TEXT,                    -- ISO 8601
  source TEXT,                    -- 'claude' | 'codex'
  ingest TEXT,                    -- 'proxy' | 'log'
  session_id TEXT,                -- when derivable; else NULL
  model TEXT,
  input_tokens INTEGER,           -- uncached input only
  output_tokens INTEGER,
  cache_creation_tokens INTEGER,
  cache_read_tokens INTEGER,
  service_tier TEXT,
  latency_ms INTEGER,             -- proxy only; NULL for log
  status TEXT                     -- 'ok' | 'error' + short code
)
```

Derived metric: cache-hit ratio =
`cache_read_tokens / (input_tokens + cache_creation_tokens + cache_read_tokens)`.

The store contains only counts and identifiers — never prompt, response, or header
content. It is added to context-memory's managed `.gitignore` rules.

## Dashboard

Served by the same process under a namespaced prefix so it cannot collide with any
Anthropic API path:

- `GET /__acm/` — a single self-contained HTML page (inline CSS/JS, no external CDN,
  consistent with the project's offline/Windows posture).
- `GET /__acm/api/summary` — totals and cache-hit ratio over a selectable window.
- `GET /__acm/api/sessions` — per-session breakdown.
- `GET /__acm/api/events` — recent raw rows (paged).

Views: usage over time, cache-hit ratio, by-model, **Claude vs Codex** comparison,
per-session detail, and an illustrative dollar-savings figure computed from Anthropic API
list prices. The savings figure is explicitly labeled as a reference conversion, not a
bill, because both CLIs are on subscription plans.

Everything runs on the single proxy port (`8788`): `/v1/*` and any other path forward
upstream; only `/__acm/*` is served locally.

## CLI Integration

`context-memory.ps1` gains a `proxy` command group:

```powershell
context-memory proxy start          # launch acm-proxy detached (no window)
context-memory proxy stop
context-memory proxy status         # running?, port, DB path, row counts
context-memory proxy enable claude  # set user ANTHROPIC_BASE_URL to the proxy
context-memory proxy disable claude # restore prior ANTHROPIC_BASE_URL
```

`enable claude` records any prior `ANTHROPIC_BASE_URL` value and restores it on `disable`,
mirroring the reversible-ownership pattern used by the single-session guard for Claude
settings. `status` prints the dashboard URL (`http://127.0.0.1:8788/__acm/`).

This phase also fixes the pre-existing Claude hook console-window flash: `New-ClaudeHookDef`
in `context-memory.ps1` gains `-WindowStyle Hidden -NonInteractive -NoLogo` on the
PowerShell invocation, matching the intent of the Codex hook definition.

## Failure Behavior

- Proxy not running: Claude Code still works if `ANTHROPIC_BASE_URL` is unset; when set to
  a dead proxy, `enable`/`disable` and `status` make the state visible and recoverable.
- Upstream error: forwarded to Claude Code unchanged; a row with `status=error` is written
  when possible.
- Usage parse failure: request still forwarded; diagnostic logged; no row or an
  `error`-status row.
- Store write failure: request unaffected; diagnostic logged.
- Codex log missing/malformed: tailer idles with a diagnostic; Claude path unaffected.
- Dashboard error: read-only; cannot affect the forwarding path.

## Testing

- **Proxy:** streaming and non-streaming usage extraction from Anthropic-shaped fixtures;
  header pass-through; fail-open on upstream error, parse error, and store error;
  byte-identical forwarding of the response body.
- **Codex ingest:** usage extraction and `prompt_tokens - cached_tokens` normalization from
  captured log fixtures; offset/dedupe across restarts; malformed-line tolerance.
- **Store:** schema creation, normalized writes for both sources, cache-hit-ratio and
  aggregate queries.
- **Dashboard:** `/api/*` aggregates against a seeded DB; HTML renders without external
  requests.
- **CLI (PowerShell):** `enable`/`disable claude` round-trips `ANTHROPIC_BASE_URL`
  reversibly; `status` reporting; hidden-window hook definition; existing Claude/Codex hook
  behavior unchanged.

All fixtures use usage-shaped sample data captured in Phase 0 — no live paid requests are
required to run the suite.

## Release

Targeted as a minor feature release. README and changelog state plainly that this measures
*real observed* provider usage (distinct from the existing estimated benchmarks), that
Claude is proxied while Codex is read from local logs, that the proxy is loopback-only and
local-self-use, and that any dollar figure is an illustrative conversion rather than a bill.
