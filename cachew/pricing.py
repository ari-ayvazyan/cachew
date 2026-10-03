"""Turn Anthropic usage numbers into dollars.

Prices are USD per million tokens, Anthropic first-party API (cached
2026-09-25). Cache writes cost 1.25x base input (5-minute TTL) or 2x (1-hour);
cache reads have a per-model price.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    cache_read: float

    def cache_write(self, ttl: str = "5m") -> float:
        return self.input * (2.0 if ttl == "1h" else 1.25)


PRICES: dict[str, Price] = {
    "claude-opus-5-5": Price(input=4.00, output=20.00, cache_read=0.20),
    "claude-sonnet-5-5": Price(input=2.00, output=10.00, cache_read=0.20),
    "claude-haiku-4-5": Price(input=1.00, output=5.00, cache_read=0.10),
    "claude-fable-5-1": Price(input=10.00, output=50.00, cache_read=0.25),
}


def cost_usd(usage: dict, model: str, ttl: str = "5m") -> float:
    """Cost of one request (or a summed usage dict) in USD."""
    p = PRICES[model]
    return (
        (usage.get("input_tokens") or 0) * p.input
        + (usage.get("cache_creation_input_tokens") or 0) * p.cache_write(ttl)
        + (usage.get("cache_read_input_tokens") or 0) * p.cache_read
        + (usage.get("output_tokens") or 0) * p.output
    ) / 1_000_000


def add_usage(*usages: dict) -> dict:
    keys = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
    return {k: sum((u.get(k) or 0) for u in usages) for k in keys}


def model_fanout_cost(
    model: str,
    n_subagents: int,
    history_tokens: int,
    brief_tokens: int,
    task_tokens: int,
    output_tokens: int,
    compaction_output_tokens: int | None = None,
) -> dict[str, float]:
    """Analytic input+output cost of one fan-out under each strategy.

    naive          every sub-agent re-reads the full raw history, no caching
                   (Omnigent today with ``pass_history: true``)
    cache_only     raw history cached: 1 write + (N-1) reads
    compact_only   history compacted once, brief re-sent uncached N times
    compact_cache  history compacted once, brief written once, read N-1 times
    """
    p = PRICES[model]
    m = 1_000_000
    n, h, b, t, o = n_subagents, history_tokens, brief_tokens, task_tokens, output_tokens
    out = n * o * p.output / m
    compaction = (h * p.input + (compaction_output_tokens or b) * p.output) / m
    return {
        "naive": n * (h + t) * p.input / m + out,
        "cache_only": (h * p.cache_write() + (n - 1) * h * p.cache_read + n * t * p.input) / m + out,
        "compact_only": compaction + n * (b + t) * p.input / m + out,
        "compact_cache": compaction
        + (b * p.cache_write() + (n - 1) * b * p.cache_read + n * t * p.input) / m
        + out,
    }
