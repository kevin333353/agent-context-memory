"""Illustrative price conversion for the dashboard.

Both CLIs run on subscription plans, so there is **no per-token bill**. These
numbers convert observed tokens at published Anthropic API list prices purely as
a reference — never as an actual charge. The dashboard labels every figure
accordingly.

Prices are USD per 1,000,000 tokens. ``cache_read`` is ~0.1x input; ``cache_write``
(cache creation) is ~1.25x input.
"""

from __future__ import annotations

from typing import Optional

# model prefix -> (input, output) per MTok. Longest matching prefix wins.
_PRICES = {
    "claude-opus-4": (5.00, 25.00),
    "claude-fable-5": (10.00, 50.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-sonnet-4": (3.00, 15.00),
    "claude-haiku-4": (1.00, 5.00),
}
_DEFAULT = (5.00, 25.00)  # assume Opus-tier if unknown


def _rates(model: Optional[str]) -> tuple[float, float]:
    if not model:
        return _DEFAULT
    best = None
    for prefix, rate in _PRICES.items():
        if model.startswith(prefix) and (best is None or len(prefix) > best[0]):
            best = (len(prefix), rate)
    return best[1] if best else _DEFAULT


def cache_savings_usd(model: Optional[str], cache_read_tokens: int) -> float:
    """Illustrative dollars saved by cache *reads* vs paying full input price.

    saved = cache_read_tokens * (input_rate - 0.1*input_rate) / 1e6
    """
    input_rate, _ = _rates(model)
    saved_rate = input_rate * 0.9  # full price minus the ~0.1x cache-read price
    return cache_read_tokens * saved_rate / 1_000_000.0


def notional_cost_usd(model: Optional[str], input_tokens: int, output_tokens: int,
                      cache_creation_tokens: int, cache_read_tokens: int) -> float:
    """Illustrative list-price cost of one record (reference only)."""
    input_rate, output_rate = _rates(model)
    return (
        input_tokens * input_rate
        + cache_creation_tokens * input_rate * 1.25
        + cache_read_tokens * input_rate * 0.1
        + output_tokens * output_rate
    ) / 1_000_000.0
