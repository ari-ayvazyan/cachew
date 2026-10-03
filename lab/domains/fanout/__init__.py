"""Fan-out cost domain for the research loop (see lab/loop.py for the contract)."""

from __future__ import annotations

import itertools
import random
from typing import Any

from lab.domains.fanout import hypotheses as hyp
from lab.domains.fanout.runner import execute, input_version
from lab.domains.fanout.skeptic import audit
from lab.store import Store

name = "fanout"
question = hyp.QUESTION
outcomes = hyp.OUTCOMES
sources = hyp.SOURCES
hypotheses = hyp.HYPOTHESES
predict = hyp.predict
feasible = hyp.feasible
estimate_usd = hyp.estimate_live_usd
code_paths = ["lab", "cachew"]
run, audit, input_version = execute, audit, input_version


def short(params: dict[str, Any]) -> str:
    m = params["model"].replace("claude-", "").split("-")[0]
    return f"{m} n{params['n']} h{params['history_tokens'] // 1000}k"


def propose_tests(tried: list[dict[str, Any]], round_no: int, k: int = 4) -> list[dict[str, Any]]:
    """Untried grid points, spread across models: the proposer does not score them."""
    seen = {tuple(sorted(t.items())) for t in tried}
    grid = [dict(zip(hyp.GRID, v)) for v in itertools.product(*hyp.GRID.values())]
    fresh = [g for g in grid if tuple(sorted(g.items())) not in seen]
    rng = random.Random(round_no)
    rng.shuffle(fresh)
    picked: list[dict[str, Any]] = []
    for model in hyp.GRID["model"]:  # at least one per model when possible
        picked += [g for g in fresh if g["model"] == model][: k // 2]
    return [{"params": g, "why": "untried region"} for g in picked[:k]]


def refine(store: Store, runs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Proposer's plan change: derive a new hypothesis from what the raw data showed.

    The textbook formula assumes 1 cache write per cached arm. If the raw data
    shows a different count, propose the formula with the observed count.
    """
    writes = []
    for r in runs:
        for arm in ("cache_only", "compact_cache"):
            pts = store.raw(r["raw"][arm])["points"]
            writes.append(sum(1 for x in pts if x["usage"]["cache_creation_input_tokens"] > 0))
    if not writes:
        return None
    w = round(sum(writes) / len(writes))
    existing = {(h["rule"]["type"], h["rule"].get("writes")) for h in store.all("H")}
    if ("formula", w) in existing:
        return None
    return {
        "key": f"formula_w{w}",
        "claim": f"pricing formula with {w} writes/arm (observed)",
        "sources": ["pricing_model"],
        "rule": {"type": "formula", "writes": w},
        "evidence": [r["id"] for r in runs],
    }


legend = {"full": "uncached", "write": "cache write", "read": "cache read", "prewarm": "pre-warm"}


def point_kind(p: dict[str, Any]) -> str:
    if p.get("prewarm"):
        return "prewarm"
    u = p["usage"]
    return "read" if u["cache_read_input_tokens"] else "write" if u["cache_creation_input_tokens"] else "full"


def glyph(p: dict[str, Any]) -> str:
    return {"full": "●", "write": "▲", "read": "○", "prewarm": "△"}[point_kind(p)]


token_legend = {"uncached": "uncached input", "write": "cache write", "read": "cache read", "output": "output"}


def tokens(u: dict[str, Any]) -> dict[str, int]:
    return {"uncached": u["input_tokens"], "write": u["cache_creation_input_tokens"],
            "read": u["cache_read_input_tokens"], "output": u["output_tokens"]}
