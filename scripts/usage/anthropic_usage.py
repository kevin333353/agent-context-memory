"""Parse Anthropic Messages API usage into a normalized UsageRecord.

Two response shapes are handled:

- Non-streaming JSON: a message object with top-level ``model`` and ``usage``.
- Streaming SSE: ``message_start`` carries input / cache tokens, the model, and
  the service tier; the final ``output_tokens`` arrives on the last
  ``message_delta``. We feed decoded event payloads to the accumulator and read
  the running totals out at the end.

Pure stdlib; no network, no time source (the caller stamps ``ts_utc``).
"""

from __future__ import annotations

import json
from typing import Optional

from .store import UsageRecord


def _usage_fields(usage: dict) -> dict:
    return {
        "input_tokens": int(usage.get("input_tokens") or 0),
        "cache_creation_tokens": int(usage.get("cache_creation_input_tokens") or 0),
        "cache_read_tokens": int(usage.get("cache_read_input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "service_tier": usage.get("service_tier"),
    }


def record_from_message(
    message: dict,
    *,
    ts_utc: str,
    session_id: Optional[str] = None,
    latency_ms: Optional[int] = None,
    status: str = "ok",
) -> Optional[UsageRecord]:
    """Build a UsageRecord from a full (non-streaming) message object."""
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return None
    f = _usage_fields(usage)
    return UsageRecord(
        ts_utc=ts_utc,
        source="claude",
        ingest="proxy",
        model=message.get("model"),
        session_id=session_id,
        input_tokens=f["input_tokens"],
        output_tokens=f["output_tokens"],
        cache_creation_tokens=f["cache_creation_tokens"],
        cache_read_tokens=f["cache_read_tokens"],
        service_tier=f["service_tier"],
        latency_ms=latency_ms,
        status=status,
    )


class SSEUsageAccumulator:
    """Consumes decoded Anthropic SSE event payloads and tracks usage.

    Only ``message_start`` and ``message_delta`` matter. Everything else is
    ignored, so it is safe to feed every event on the stream.
    """

    def __init__(self) -> None:
        self.model: Optional[str] = None
        self.service_tier: Optional[str] = None
        self.input_tokens = 0
        self.cache_creation_tokens = 0
        self.cache_read_tokens = 0
        self.output_tokens = 0
        self.saw_usage = False

    def feed_event(self, payload: dict) -> None:
        etype = payload.get("type")
        if etype == "message_start":
            message = payload.get("message") or {}
            self.model = message.get("model") or self.model
            usage = message.get("usage")
            if isinstance(usage, dict):
                f = _usage_fields(usage)
                self.input_tokens = f["input_tokens"]
                self.cache_creation_tokens = f["cache_creation_tokens"]
                self.cache_read_tokens = f["cache_read_tokens"]
                # message_start seeds output_tokens (usually 1-few); message_delta
                # overwrites with the final cumulative value.
                self.output_tokens = f["output_tokens"]
                if f["service_tier"] is not None:
                    self.service_tier = f["service_tier"]
                self.saw_usage = True
        elif etype == "message_delta":
            usage = payload.get("usage")
            if isinstance(usage, dict):
                if usage.get("output_tokens") is not None:
                    self.output_tokens = int(usage["output_tokens"])
                # Some deltas also restate input/cache; keep the max seen.
                if usage.get("input_tokens") is not None:
                    self.input_tokens = max(self.input_tokens, int(usage["input_tokens"]))
                self.saw_usage = True

    def to_record(
        self,
        *,
        ts_utc: str,
        session_id: Optional[str] = None,
        latency_ms: Optional[int] = None,
        status: str = "ok",
    ) -> Optional[UsageRecord]:
        if not self.saw_usage:
            return None
        return UsageRecord(
            ts_utc=ts_utc,
            source="claude",
            ingest="proxy",
            model=self.model,
            session_id=session_id,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cache_creation_tokens=self.cache_creation_tokens,
            cache_read_tokens=self.cache_read_tokens,
            service_tier=self.service_tier,
            latency_ms=latency_ms,
            status=status,
        )


def iter_sse_events(text: str):
    """Yield decoded JSON payloads from an SSE text blob.

    An SSE event is a run of lines terminated by a blank line; ``data:`` lines
    (concatenated) carry the JSON. Malformed data lines are skipped.
    """
    for block in text.replace("\r\n", "\n").split("\n\n"):
        data_lines = [
            line[5:].lstrip() if line.startswith("data:") else None
            for line in block.split("\n")
        ]
        data = "".join(d for d in data_lines if d is not None)
        if not data or data == "[DONE]":
            continue
        try:
            yield json.loads(data)
        except (ValueError, TypeError):
            continue


def record_from_sse_text(
    text: str,
    *,
    ts_utc: str,
    session_id: Optional[str] = None,
    latency_ms: Optional[int] = None,
    status: str = "ok",
) -> Optional[UsageRecord]:
    """Convenience: parse a complete SSE blob into one UsageRecord."""
    acc = SSEUsageAccumulator()
    for payload in iter_sse_events(text):
        acc.feed_event(payload)
    return acc.to_record(
        ts_utc=ts_utc, session_id=session_id, latency_ms=latency_ms, status=status
    )
