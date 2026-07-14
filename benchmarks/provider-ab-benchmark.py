#!/usr/bin/env python3
"""Provider-backed A/B benchmark helpers for Agent Context Memory."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


EXPECTED = {
    "project": "Atlas",
    "port": 4317,
    "retry_count": 4,
    "backoff_ms": [250, 500, 1000, 2000],
    "database_mode": "SQLite WAL",
    "feature_flag": "JOBSMITH_SAFE_EXPORT",
    "retention_days": 30,
    "owner": "Release Engineering",
}

APPROVED_FACTS = [
    "APPROVED DECISION: The project codename is Atlas.",
    "APPROVED DECISION: The service port is 4317.",
    "APPROVED DECISION: Retry exactly 4 times.",
    "APPROVED DECISION: Backoff delays in milliseconds are 250, 500, 1000, and 2000.",
    "APPROVED DECISION: Database mode is SQLite WAL.",
    "APPROVED DECISION: The feature flag is JOBSMITH_SAFE_EXPORT.",
    "APPROVED DECISION: Retain records for 30 days.",
    "APPROVED DECISION: The owner is Release Engineering.",
]

INSTRUCTION = (
    "Recover the approved project configuration. Authoritative data may be "
    "supplied in either of two equivalent forms: (1) a FULL HISTORICAL "
    "TRANSCRIPT where only lines labeled APPROVED DECISION are authoritative, "
    "or (2) an injected CONTEXT_MEMORY_STATE whose project.name, "
    "stable_context, decisions, and next_actions summarize those same approved "
    "decisions. For this controlled benchmark, the injected state is authorized "
    "evidence; do not request the full transcript when it is present. Ignore "
    "rejected archive items. Return exactly one JSON object with these keys and "
    "no markdown: project, port, retry_count, backoff_ms, database_mode, "
    "feature_flag, retention_days, owner."
)

CODING_INSTRUCTION = (
    "Update atlas_config.py to the approved project configuration. Authoritative "
    "data may be supplied in either of two equivalent forms: (1) a FULL "
    "HISTORICAL TRANSCRIPT where only lines labeled APPROVED DECISION are "
    "authoritative, or (2) an injected CONTEXT_MEMORY_STATE whose project.name, "
    "stable_context, decisions, and next_actions summarize those same approved "
    "decisions. For this controlled benchmark, the injected state is authorized "
    "evidence; do not request the full transcript when it is present. Ignore "
    "rejected archive items. Modify only atlas_config.py, do not modify tests, "
    "then run python -m unittest -v."
)


def generate_history(distractor_lines: int) -> str:
    slots = {
        max(1, ((index + 1) * distractor_lines) // 9): fact
        for index, fact in enumerate(APPROVED_FACTS)
    }
    rows: list[str] = []
    for number in range(1, distractor_lines + 1):
        rows.append(
            f"Archive item {number}: obsolete proposal port {5000 + number % 997}, "
            f"retry {1 + number % 9}, retention {1 + number % 90} days, "
            f"owner Team-{number}, database draft-{number}, and flag OLD_FLAG_{number}. "
            "This item was explicitly REJECTED and must not override approved data."
        )
        if number in slots:
            rows.append(slots[number])
    return "\n".join(rows)


def build_case_prompts(history: str, task: str = "recall") -> tuple[str, str]:
    if task == "recall":
        instruction = INSTRUCTION
    elif task == "coding":
        instruction = CODING_INSTRUCTION
    else:
        raise ValueError(f"Unsupported benchmark task: {task}")
    return f"{instruction}\nFULL HISTORICAL TRANSCRIPT:\n{history}", instruction


def _answer_object(answer: str) -> dict[str, Any] | None:
    cleaned = answer.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    try:
        value = json.loads(cleaned.strip())
    except (TypeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def answer_passes(answer: str) -> bool:
    return _answer_object(answer) == EXPECTED


def parse_claude_result(raw: str) -> dict[str, Any]:
    value = json.loads(raw)
    usage = value["usage"]
    return {
        "answer": value["result"],
        "quality_pass": answer_passes(value["result"]),
        "usage": {
            "input_tokens": usage.get("input_tokens", 0)
            + usage.get("cache_creation_input_tokens", 0)
            + usage.get("cache_read_input_tokens", 0),
            "cached_input_tokens": usage.get("cache_read_input_tokens", 0),
            "cache_creation_input_tokens": usage.get(
                "cache_creation_input_tokens", 0
            ),
            "output_tokens": usage.get("output_tokens", 0),
            "duration_ms": value.get("duration_ms"),
        },
    }


def parse_codex_result(raw: str) -> dict[str, Any]:
    thread_id = None
    answer = ""
    usage: dict[str, Any] = {}
    for line in raw.splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if event.get("type") == "thread.started":
            thread_id = event.get("thread_id")
        elif (
            event.get("type") == "item.completed"
            and event.get("item", {}).get("type") == "agent_message"
        ):
            answer = event["item"].get("text", "")
        elif event.get("type") == "turn.completed":
            usage = event.get("usage", {})
    return {
        "thread_id": thread_id,
        "answer": answer,
        "quality_pass": answer_passes(answer),
        "usage": usage,
    }


def summarize_case(baseline: dict[str, Any], memory: dict[str, Any]) -> dict[str, Any]:
    baseline_input = baseline["usage"]["input_tokens"]
    memory_input = memory["usage"]["input_tokens"]
    saved = baseline_input - memory_input
    return {
        "baseline_input_tokens": baseline_input,
        "memory_input_tokens": memory_input,
        "saved_tokens": saved,
        "saved_percent": round((saved / baseline_input) * 100, 2)
        if baseline_input
        else 0.0,
        "baseline_cached_input_tokens": baseline["usage"].get(
            "cached_input_tokens", 0
        ),
        "memory_cached_input_tokens": memory["usage"].get(
            "cached_input_tokens", 0
        ),
        "quality_pass": baseline["quality_pass"] and memory["quality_pass"],
    }


def resolve_executable(name: str, windows: bool | None = None) -> str:
    is_windows = os.name == "nt" if windows is None else windows
    if is_windows:
        return shutil.which(f"{name}.cmd") or shutil.which(name) or name
    return shutil.which(name) or name


def provider_subprocess_options() -> dict[str, Any]:
    return {"text": True, "encoding": "utf-8", "errors": "replace"}


def build_provider_command(
    provider: str, max_budget_usd: float, task: str = "recall"
) -> list[str]:
    if provider == "claude":
        command = [
            resolve_executable("claude"),
            "-p",
            "--output-format",
            "json",
            "--model",
            "sonnet",
        ]
        if task == "recall":
            command.extend(["--tools", "", "--no-session-persistence"])
        elif task == "coding":
            command.extend(
                [
                    "--dangerously-skip-permissions",
                    "--permission-mode",
                    "bypassPermissions",
                    "--no-session-persistence",
                ]
            )
        else:
            raise ValueError(f"Unsupported benchmark task: {task}")
        return command + ["--max-budget-usd", str(max_budget_usd)]
    if provider == "codex":
        command = [
            resolve_executable("codex"),
            "exec",
            "-",
            "--json",
            "--ephemeral",
            "--ignore-rules",
        ]
        if task == "recall":
            command.extend(["-s", "read-only"])
        elif task == "coding":
            command.extend(
                ["-s", "workspace-write", "--dangerously-bypass-approvals-and-sandbox"]
            )
        else:
            raise ValueError(f"Unsupported benchmark task: {task}")
        return command + [
            "--dangerously-bypass-hook-trust",
            "-c",
            'model_reasoning_effort="low"',
        ]
    raise ValueError(f"Unsupported provider: {provider}")


def run_provider(
    provider: str, cwd: Path, prompt: str, max_budget_usd: float, task: str = "recall"
) -> dict[str, Any]:
    command = build_provider_command(provider, max_budget_usd, task)

    completed = subprocess.run(
        command,
        cwd=cwd,
        input=prompt,
        capture_output=True,
        check=False,
        timeout=900,
        **provider_subprocess_options(),
    )
    if completed.returncode:
        raise RuntimeError(
            f"{provider} exited {completed.returncode}: {completed.stderr.strip()}"
        )
    if provider == "claude":
        return parse_claude_result(completed.stdout)
    return parse_codex_result(completed.stdout)


def run_case(
    provider: str,
    baseline_cwd: Path,
    memory_cwd: Path,
    distractor_lines: int,
    max_budget_usd: float,
    task: str = "recall",
) -> dict[str, Any]:
    history = generate_history(distractor_lines)
    baseline_prompt, memory_prompt = build_case_prompts(history, task)
    baseline = run_provider(
        provider, baseline_cwd, baseline_prompt, max_budget_usd, task
    )
    memory = run_provider(provider, memory_cwd, memory_prompt, max_budget_usd, task)
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "provider": provider,
        "task": task,
        "distractor_lines": distractor_lines,
        "history_chars": len(history),
        "baseline_prompt_chars": len(baseline_prompt),
        "memory_prompt_chars": len(memory_prompt),
        "baseline": baseline,
        "memory": memory,
        "summary": summarize_case(baseline, memory),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=("claude", "codex"), required=True)
    parser.add_argument("--baseline-cwd", type=Path, required=True)
    parser.add_argument("--memory-cwd", type=Path, required=True)
    parser.add_argument("--distractor-lines", type=int, required=True)
    parser.add_argument("--task", choices=("recall", "coding"), default="recall")
    parser.add_argument("--max-budget-usd", type=float, default=2.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    result = run_case(
        args.provider,
        args.baseline_cwd,
        args.memory_cwd,
        args.distractor_lines,
        args.max_budget_usd,
        args.task,
    )
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
