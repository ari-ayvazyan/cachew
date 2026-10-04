"""Fan-out domain: question, sources, competing hypotheses, test grid, cost.

Question: which fan-out strategy is cheapest, and under what conditions?
Outcome of a test = which arm is cheapest at (model, N sub-agents, history size).

Every hypothesis is a machine-readable ``rule`` so anyone (the selector, the
judge, the skeptic) can compute its prediction without asking its author.
"""

from __future__ import annotations

from typing import Any

from cachew.patch import min_prewarm_waiters
from cachew.pricing import PRICES

OUTCOMES = ["compact_cache", "cache_only", "other"]  # other = naive or compact_only
CONF = 0.85  # a hypothesis claims its outcome with 85%: one surprise weakens, it doesn't kill
BRIEF_TOKENS = 5_000  # above Haiku 4.5's 4,096-token cache minimum
TASK_TOKENS, OUT_TOKENS = 30, 150

QUESTION = "Which fan-out strategy is cheapest, and when?"

# key -> (file, first line, last line)
SOURCES = {
    "readme_crossover": ("README.md", 99, 101),
    "readme_live_row": ("README.md", 111, 116),
    "pricing_model": ("cachew/pricing.py", 47, 76),
    "prewarm_gate": ("cachew/patch.py", 170, 184),
}

HYPOTHESES = [
    {"key": "always", "claim": "compact+cache always cheapest", "sources": ["readme_live_row"],
     "rule": {"type": "always", "outcome": "compact_cache"}},
    {"key": "raw", "claim": "raw-history cache always cheapest", "sources": ["readme_crossover"],
     "rule": {"type": "always", "outcome": "cache_only"}},
    {"key": "n8", "claim": "compaction wins iff N >= 8", "sources": ["readme_crossover"],
     "rule": {"type": "n_at_least", "n": 8}},
    {"key": "formula", "claim": "pricing formula picks winner (1 write)", "sources": ["pricing_model"],
     "rule": {"type": "formula", "writes": 1}},
    {"key": "formula_sf", "claim": "pricing formula, pre-warm only when it pays (1 write if N<3)",
     "sources": ["pricing_model", "prewarm_gate"], "rule": {"type": "formula", "writes": "single_flight"}},
    {"key": "none", "claim": "none of these is right", "sources": [],
     "rule": {"type": "uniform"}},
]

GRID = {
    "model": ["claude-haiku-4-5", "claude-opus-5-5"],
    "n": [2, 4, 8, 16],
    "history_tokens": [8_000, 16_000, 32_000, 64_000, 128_000],
}


def _claim(outcome: str) -> dict[str, float]:
    rest = (1 - CONF) / (len(OUTCOMES) - 1)
    return {o: CONF if o == outcome else rest for o in OUTCOMES}


def writes_per_arm(params: dict[str, Any], writes: float | str) -> float:
    """``"single_flight"``: the leader's write, plus a pre-warm only once
    enough siblings wait on it (cachew.patch.min_prewarm_waiters)."""
    if writes == "single_flight":
        return 2 if params["n"] - 1 >= min_prewarm_waiters(params["model"]) else 1
    return float(writes)


def arm_costs(params: dict[str, Any], writes: float | str) -> dict[str, float]:
    """Input-side USD per arm; outputs and tasks are identical across arms and cancel.

    ``writes``: cache writes per cached arm (1 in the textbook formula; an
    unconditional single-flight pre-warm makes it 2 for concurrent fan-outs;
    ``"single_flight"`` is the gated pre-warm).
    """
    writes = writes_per_arm(params, writes)
    p = PRICES[params["model"]]
    n, h, b = params["n"], params["history_tokens"], BRIEF_TOKENS
    cw, m = p.cache_write(), 1_000_000
    compaction = h * p.input + b * p.output
    return {
        "naive": n * h * p.input / m,
        "cache_only": (writes * h * cw + (n - 1) * h * p.cache_read) / m,
        "compact_only": (compaction + n * b * p.input) / m,
        "compact_cache": (compaction + writes * b * cw + (n - 1) * b * p.cache_read) / m,
    }


def to_outcome(arm: str) -> str:
    return arm if arm in ("compact_cache", "cache_only") else "other"


def predict(rule: dict[str, Any], params: dict[str, Any]) -> dict[str, float]:
    t = rule["type"]
    if t == "uniform":
        return {o: 1 / len(OUTCOMES) for o in OUTCOMES}
    if t == "always":
        return _claim(rule["outcome"])
    if t == "n_at_least":
        return _claim("compact_cache" if params["n"] >= rule["n"] else "cache_only")
    if t == "formula":
        costs = arm_costs(params, rule["writes"])
        return _claim(to_outcome(min(costs, key=costs.get)))
    raise ValueError(f"unknown rule {t}")


def estimate_live_usd(params: dict[str, Any]) -> float:
    """What this test would cost on the real API (all four arms + compaction + outputs)."""
    p = PRICES[params["model"]]
    inputs = sum(arm_costs(params, "single_flight").values()) + params["history_tokens"] * p.input / 1e6
    outputs = 4 * params["n"] * OUT_TOKENS * p.output / 1e6
    return round(inputs + outputs, 4)


def feasible(params: dict[str, Any]) -> tuple[bool, str]:
    if params["history_tokens"] > 150_000:
        return False, "history > context budget"
    if estimate_live_usd(params) > 1.0:
        return False, "live cost > $1 cap"
    return True, "ok"
