"""Compute and persist agent-context-memory's own savings.

Two honest sources, both comparing the tool's compact ``state.yaml`` against the
baseline of carrying the full running transcript:

- ``simulate``  offline upper-bound estimate (``benchmarks/simulate-token-savings.py``)
- ``ab``        real provider A/B, tool on vs off (``benchmarks/provider-ab-benchmark.py``)

The parse functions (pure, unit-tested) turn a benchmark's JSON into a
``UsageSavings`` row; the runners are thin subprocess wrappers around the
existing benchmark scripts so there is one source of truth for the math.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:  # running as a script: <ToolRoot>/scripts on sys.path
    from usage.store import UsageStore, UsageSavings
except ImportError:  # imported as scripts.usage.savings
    from scripts.usage.store import UsageStore, UsageSavings


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _tool_root() -> Path:
    # <ToolRoot>/scripts/usage/savings.py -> <ToolRoot>
    return Path(__file__).resolve().parent.parent.parent


# ---- pure parsers -------------------------------------------------------

def savings_from_simulator(
    result: dict, memory_root: Optional[str] = None,
    ts_utc: Optional[str] = None, dedupe_key: Optional[str] = None,
) -> UsageSavings:
    s = result.get("summary") or {}
    return UsageSavings(
        ts_utc=ts_utc or _now(),
        kind="simulate",
        saved_percent=float(s.get("saved_percent") or 0.0),
        baseline_tokens=int(s.get("baseline_total_tokens") or 0),
        memory_tokens=int(s.get("memory_total_tokens") or 0),
        memory_root=memory_root,
        detail=json.dumps(result.get("assumptions") or {}, ensure_ascii=False),
        dedupe_key=dedupe_key,
    )


def savings_from_ab(
    result: dict, memory_root: Optional[str] = None,
    ts_utc: Optional[str] = None, dedupe_key: Optional[str] = None,
) -> UsageSavings:
    s = result.get("summary") or {}
    qp = s.get("quality_pass")
    return UsageSavings(
        ts_utc=ts_utc or _now(),
        kind="ab",
        saved_percent=float(s.get("saved_percent") or 0.0),
        baseline_tokens=int(s.get("baseline_input_tokens") or 0),
        memory_tokens=int(s.get("memory_input_tokens") or 0),
        memory_root=memory_root,
        provider=result.get("provider"),
        task=result.get("task"),
        quality_pass=(1 if qp else 0) if qp is not None else None,
        detail=json.dumps(s, ensure_ascii=False),
        dedupe_key=dedupe_key,
    )


# ---- thin subprocess runners around the benchmark scripts ---------------

def run_simulator(state_path: Path, python: Optional[str] = None,
                  extra_args: Optional[list] = None) -> dict:
    script = _tool_root() / "benchmarks" / "simulate-token-savings.py"
    cmd = [python or sys.executable, str(script), "--state", str(state_path)]
    cmd += list(extra_args or [])
    out = subprocess.run(cmd, capture_output=True, text=True, check=True,
                         encoding="utf-8")
    return json.loads(out.stdout)


def run_ab(baseline_cwd: Path, memory_cwd: Path, provider: str = "claude",
           task: str = "recall", distractor_lines: int = 400,
           max_budget_usd: float = 2.0, python: Optional[str] = None) -> dict:
    script = _tool_root() / "benchmarks" / "provider-ab-benchmark.py"
    cmd = [python or sys.executable, str(script),
           "--provider", provider, "--task", task,
           "--baseline-cwd", str(baseline_cwd), "--memory-cwd", str(memory_cwd),
           "--distractor-lines", str(distractor_lines),
           "--max-budget-usd", str(max_budget_usd)]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True,
                         encoding="utf-8", timeout=1800)
    return json.loads(out.stdout)


# ---- CLI ----------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Persist tool-savings measurements")
    parser.add_argument("--db", required=True)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_sim = sub.add_parser("simulate", help="run simulator against a state.yaml")
    p_sim.add_argument("--state", required=True)
    p_sim.add_argument("--memory-root")

    p_ab = sub.add_parser("ab-record", help="persist an A/B result JSON file")
    p_ab.add_argument("--result", required=True)
    p_ab.add_argument("--memory-root")

    args = parser.parse_args()
    store = UsageStore(Path(args.db))
    try:
        if args.cmd == "simulate":
            res = run_simulator(Path(args.state))
            rec = savings_from_simulator(res, memory_root=args.memory_root)
            store.record_savings(rec)
            print(json.dumps({"recorded": "simulate",
                              "saved_percent": rec.saved_percent}))
        elif args.cmd == "ab-record":
            res = json.loads(Path(args.result).read_text(encoding="utf-8"))
            rec = savings_from_ab(res, memory_root=args.memory_root)
            store.record_savings(rec)
            print(json.dumps({"recorded": "ab",
                              "saved_percent": rec.saved_percent}))
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
