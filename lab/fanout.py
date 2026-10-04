"""Cached fan-out for any topic: N sub-agents share one prefix, each gets a small task.

Every sub-agent receives ``system`` + ``brief`` as a byte-identical prefix and
only its own short task after the cache breakpoint
(``cachew.compact.subagent_messages``). The calls go through Omnigent's
Anthropic adapter with ``cachew.patch``: the leader writes the prefix once, a
single-flight pre-warm covers concurrent siblings, and the rest read it from
cache. ``cache_check`` is the matching skeptic check: it fails a run in which
the fan-out did not actually read the cache.

The API key comes from the environment or the git-ignored ``.env`` and is
never written anywhere. A ``Ledger`` keeps a hard spend cap per topic.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

MODEL = "claude-haiku-4-5"  # cheapest; note Haiku only caches prefixes of 4096+ tokens


class BudgetExceeded(RuntimeError):
    pass


class Ledger:
    """Real-API spend for one topic, persisted in a (git-ignored) JSON file."""

    def __init__(self, path: Path, budget_usd: float) -> None:
        self.path, self.budget = Path(path), budget_usd

    def spent(self) -> float:
        return json.loads(self.path.read_text())["usd"] if self.path.exists() else 0.0

    def check(self, est_usd: float) -> None:
        if self.spent() + est_usd > self.budget:
            raise BudgetExceeded(f"spent ${self.spent():.4f} + est ${est_usd:.4f} > ${self.budget}")

    def charge(self, usd: float) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"usd": round(self.spent() + usd, 6)}))


def load_env(path: str = ".env") -> None:
    env = Path(path)
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            k, _, v = line.partition("=")
            if k.strip() in ("ANTHROPIC_API_KEY", "ANTHROPIC_WORKSPACE_ID") and not os.environ.get(k.strip()):
                os.environ[k.strip()] = v.strip().strip('"').strip("'")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY not set (environment or .env)")


def estimate_usd(prefix_chars: int, n: int, out_tokens: int, model: str = MODEL) -> float:
    """Cached fan-out: two writes (leader + pre-warm), n-1 reads, plus output (~3.5 chars per token)."""
    from cachew.pricing import PRICES

    p, prefix = PRICES[model], prefix_chars / 3.5
    return round((prefix * (2 * p.cache_write() + (n - 1) * p.cache_read) + out_tokens * p.output) / 1e6, 4)


def _uncached(usage: dict[str, Any], model: str) -> float:
    from cachew.pricing import cost_usd

    inp = usage["input_tokens"] + usage["cache_creation_input_tokens"] + usage["cache_read_input_tokens"]
    return cost_usd({**usage, "input_tokens": inp, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}, model)


def run(system: str, brief: str, tasks: dict[str, str], model: str = MODEL, max_tokens: int = 2000,
        ledger: Ledger | None = None, live: bool = True) -> dict[str, Any]:
    """One cached fan-out. Returns {calls: [{label, usage, usd, prewarm, text}], summary}.

    ``live=False`` skips the key and the workspace header (e.g. against ``cachew.fake_api``).
    """
    from cachew import patch
    from cachew.compact import subagent_messages
    from cachew.experiment import Runner, run_arm
    from cachew.pricing import add_usage, cost_usd

    if ledger:
        ledger.check(estimate_usd(len(system) + len(brief), len(tasks), len(tasks) * max_tokens // 4, model))
    if live:
        load_env()
        patch.install_workspace_header()
    arm = asyncio.run(run_arm("fanout", Runner(model, "", max_tokens), lambda t: subagent_messages(system, brief, t), tasks, True))
    if ledger:
        ledger.charge(arm["cost_usd"])
    calls = [{"label": c["label"], "usage": c["usage"], "usd": round(cost_usd(c["usage"], model), 6),
              "prewarm": c["label"] == "prewarm", "text": c.get("text", "")} for c in arm["calls"]]
    subs = [c for c in calls if not c["prewarm"]]
    total = add_usage(*(c["usage"] for c in calls))
    return {"calls": calls, "summary": {
        "subagents": len(tasks), "prefix_chars": len(system) + len(brief), "wall_s": arm["wall_s"],
        "writes": sum(1 for c in calls if c["usage"]["cache_creation_input_tokens"] > 0),
        "readers": sum(1 for c in subs if c["usage"]["cache_read_input_tokens"] > 0),
        "usd": round(arm["cost_usd"], 6), "usd_if_uncached": round(_uncached(total, model), 6)}}


def ask(system: str, user: str, model: str = MODEL, max_tokens: int = 2000, ledger: Ledger | None = None) -> dict[str, Any]:
    """One call, no fan-out: returns {text, usage, usd}."""
    from cachew import patch
    from cachew.experiment import Runner
    from cachew.pricing import cost_usd

    if ledger:
        ledger.check(0.0)
    load_env()
    patch.install_workspace_header()
    out = asyncio.run(Runner(model, "", max_tokens).call([{"role": "system", "content": system}, {"role": "user", "content": user}]))
    usd = cost_usd(out["usage"], model)
    if ledger:
        ledger.charge(usd)
    return {**out, "usd": usd}


def cache_check(calls: list[dict[str, Any]], subagents: int, slack: int = 2, max_writes: int = 2) -> dict[str, Any]:
    """Skeptic check: all sub-agents ran, at least N-slack read the shared prefix, at most leader + pre-warm wrote it."""
    subs = [c for c in calls if not c.get("prewarm")]
    readers = sum(1 for c in subs if c["usage"].get("cache_read_input_tokens", 0) > 0)
    writes = sum(1 for c in calls if c["usage"].get("cache_creation_input_tokens", 0) > 0)
    ok = len(subs) == subagents and readers >= len(subs) - slack and writes <= max_writes
    return {"check": "cache_used", "ok": ok, "detail": f"{readers}/{len(subs)} sub-agents read the shared prefix, {writes} write(s)"}


def parse_json(text: str) -> Any:
    """First JSON value in the text (models sometimes wrap it in a code fence)."""
    start = min((i for i in (text.find("["), text.find("{")) if i >= 0), default=-1)
    if start < 0:
        raise ValueError("no JSON in model output")
    return json.JSONDecoder().raw_decode(text[start:])[0]
