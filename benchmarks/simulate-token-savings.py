#!/usr/bin/env python3
"""Offline context-memory token savings simulator.

This does not call a model. It estimates what would be sent if an agent either:
1. Re-injected the full running transcript every turn.
2. Re-injected a compact .context-memory/state.yaml block every turn.

If tiktoken is installed, cl100k_base is used. Otherwise a conservative mixed
Chinese/Latin heuristic is used.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path


def get_token_counter():
    try:
        import tiktoken  # type: ignore

        enc = tiktoken.get_encoding("cl100k_base")
        return "tiktoken:cl100k_base", lambda text: len(enc.encode(text))
    except Exception:
        return "heuristic:mixed-cjk", estimate_tokens


def estimate_tokens(text: str) -> int:
    # CJK characters often tokenize close to 1 char/token; Latin/code is closer
    # to 3-5 chars/token. This deliberately avoids pretending to be exact.
    cjk = len(re.findall(r"[\u3400-\u9fff]", text))
    non_cjk = len(text) - cjk
    return int(math.ceil(cjk + non_cjk / 4.0))


def build_memory_context(state_text: str, extra_dynamic_chars: int = 0) -> str:
    dynamic = ""
    if extra_dynamic_chars > 0:
        dynamic = "\ndynamic_context:\n  - " + ("x" * max(0, extra_dynamic_chars - 23))

    return (
        "<CONTEXT_MEMORY_STATE protocol=\"context-memory/v1\">\n"
        "Location: .context-memory/state.yaml\n"
        "Schema: .context-memory/schema.yaml\n\n"
        "<STATE_YAML>\n"
        f"{state_text.rstrip()}{dynamic}\n"
        "</STATE_YAML>\n"
        "</CONTEXT_MEMORY_STATE>\n"
    )


def make_turn(turn_no: int, chars_per_turn: int) -> str:
    header = (
        f"\n<turn n=\"{turn_no}\">\n"
        f"User: Implement and verify feature slice {turn_no}; include constraints, "
        "files, commands, errors, decisions, and follow-up questions.\n"
        f"Assistant: Completed investigation for slice {turn_no}. "
    )
    footer = (
        f"\nFiles touched: src/module_{turn_no}.ts, tests/module_{turn_no}.test.ts\n"
        f"Decision: keep behavior {turn_no} behind a small adapter boundary.\n"
        "</turn>\n"
    )
    body_len = max(0, chars_per_turn - len(header) - len(footer))
    # Use mixed prose/code-like text to approximate development conversations.
    chunk = (
        "Observed logs, compared implementation, added focused checks, "
        "ran verification, captured exact command output. "
        "function example() { return 'stable-contract'; } "
        "注意: 保留決策、阻塞、檔案路徑，不貼完整 transcript。 "
    )
    repeated = (chunk * ((body_len // len(chunk)) + 1))[:body_len]
    return header + repeated + footer


def run_simulation(args):
    token_source, count_tokens = get_token_counter()

    state_path = Path(args.state)
    state_text = state_path.read_text(encoding="utf-8") if state_path.exists() else ""

    static_prefix = "SYSTEM STATIC PREFIX\n" + ("Follow stable tool rules.\n" * args.static_lines)
    transcript = ""

    baseline_total = 0
    memory_total = 0
    baseline_peak = 0
    memory_peak = 0
    rows = []

    for turn in range(1, args.turns + 1):
        user_prompt = f"User prompt for turn {turn}: continue from current task."
        baseline_payload = static_prefix + transcript + "\n" + user_prompt

        memory_growth = args.memory_growth_chars_per_turn * max(0, turn - 1)
        memory_context = build_memory_context(state_text, memory_growth)
        memory_payload = static_prefix + memory_context + "\n" + user_prompt

        baseline_tokens = count_tokens(baseline_payload)
        memory_tokens = count_tokens(memory_payload)

        baseline_total += baseline_tokens
        memory_total += memory_tokens
        baseline_peak = max(baseline_peak, baseline_tokens)
        memory_peak = max(memory_peak, memory_tokens)

        rows.append(
            {
                "turn": turn,
                "baseline_tokens": baseline_tokens,
                "memory_tokens": memory_tokens,
                "saved_tokens": baseline_tokens - memory_tokens,
                "saved_percent": pct(baseline_tokens - memory_tokens, baseline_tokens),
            }
        )

        transcript += make_turn(turn, args.chars_per_turn)

    saved_total = baseline_total - memory_total

    return {
        "token_counter": token_source,
        "assumptions": {
            "turns": args.turns,
            "chars_per_turn_added_to_raw_transcript": args.chars_per_turn,
            "static_lines_in_both_modes": args.static_lines,
            "memory_growth_chars_per_turn": args.memory_growth_chars_per_turn,
            "state_path": str(state_path),
            "billing_note": "offline estimate; provider usage metadata is authoritative",
        },
        "summary": {
            "baseline_total_tokens": baseline_total,
            "memory_total_tokens": memory_total,
            "saved_total_tokens": saved_total,
            "saved_percent": pct(saved_total, baseline_total),
            "baseline_peak_tokens": baseline_peak,
            "memory_peak_tokens": memory_peak,
            "peak_saved_tokens": baseline_peak - memory_peak,
            "peak_saved_percent": pct(baseline_peak - memory_peak, baseline_peak),
        },
        "sample_turns": [rows[0], rows[len(rows) // 2], rows[-1]] if rows else [],
        "all_turns": rows if args.include_turns else None,
    }


def pct(part: int, whole: int) -> float:
    if whole <= 0:
        return 0.0
    return round((part / whole) * 100.0, 2)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", default=".context-memory/state.yaml")
    parser.add_argument("--turns", type=int, default=30)
    parser.add_argument("--chars-per-turn", type=int, default=3000)
    parser.add_argument("--static-lines", type=int, default=80)
    parser.add_argument("--memory-growth-chars-per-turn", type=int, default=80)
    parser.add_argument("--include-turns", action="store_true")
    args = parser.parse_args()

    result = run_simulation(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
