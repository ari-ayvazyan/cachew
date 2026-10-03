"""Runner: executes one spec through Omnigent's real adapter (patched / unpatched).

Backend is the caching-aware fake API (cachew/fake_api.py): free, deterministic,
same caching rules as the real one. Every request is written to
``runs/R-xxx/raw/<arm>.json`` as one data point; the result only summarizes.
"""

from __future__ import annotations

import asyncio
import os
import random
import re
import shutil
import tempfile
import time
from typing import Any

from omnigent.llms.adapters import anthropic as adapter_mod

from cachew import patch
from cachew.compact import compaction_messages, pass_history_messages, subagent_messages
from cachew.fake_api import FakeAnthropic
from cachew.pricing import add_usage, cost_usd
from lab.provenance import data_version
from lab.domains.fanout.hypotheses import BRIEF_TOKENS, OUT_TOKENS, to_outcome
from lab.store import Store

SYSTEM = "You are a sub-agent of a research orchestrator."
ARMS = [("naive", "history", False), ("cache_only", "history", True), ("compact_only", "brief", False), ("compact_cache", "brief", True)]
MIN_CACHE = {"claude-haiku-4-5": 4096}  # others: 512
WORDS = "cache prefix brief agent token history research subagent model write read".split()


def _text(tokens: int, rng: random.Random) -> str:
    out, size = [], 0
    while size < tokens * 4:  # the fake counts ~4 chars per token
        w = rng.choice(WORDS)
        out.append(w)
        size += len(w) + 1
    return " ".join(out)


def inputs(params: dict[str, Any], seed: int) -> dict[str, Any]:
    rng = random.Random(seed)
    turns = 8
    history = [{"role": "system", "content": SYSTEM}]
    for i in range(turns):
        history.append({"role": "user", "content": f"Research step {i}."})
        history.append({"role": "assistant", "content": _text(params["history_tokens"] // turns, rng)})
    tasks = {f"sub{i:02d}": f"Task {i}: analyse area {i} only." for i in range(params["n"])}
    return {"history": history, "brief": _text(BRIEF_TOKENS, rng), "tasks": tasks}


def input_version(params: dict[str, Any], seed: int) -> str:
    return data_version(inputs(params, seed))


def _label(payload: dict[str, Any], prewarm: bool) -> str:
    if prewarm:
        return "prewarm"
    last = payload["messages"][-1]["content"]
    text = last if isinstance(last, str) else " ".join(b.get("text", "") for b in last)
    m = re.search(r"Task (\d+):", text)
    return f"sub{int(m.group(1)):02d}" if m else "other"


def _point(r: dict[str, Any], model: str, t_start: float) -> dict[str, Any]:
    payload = r["payload"]
    return {
        "t0_ms": round((r["t0"] - t_start) * 1000, 1),
        "t1_ms": round((r["t1"] - t_start) * 1000, 1),
        "label": _label(payload, r["prewarm"]),
        "prewarm": r["prewarm"],
        "cc_in_payload": "cache_control" in str(payload),
        "cc_on_task": "cache_control" in str(payload["messages"][-1]),
        "usage": r["usage"],
        "usd": cost_usd(r["usage"], model),
    }


async def _arm(params: dict[str, Any], data: dict[str, Any], source: str, patched: bool) -> dict[str, Any]:
    model = params["model"]
    state = tempfile.mkdtemp(prefix="lab_arm_")
    t_start = time.monotonic()
    os.environ["CACHEW_DIR"] = state
    fake = FakeAnthropic(min_tokens=MIN_CACHE.get(model, 512), output_tokens=OUT_TOKENS).install()  # fresh cache per arm
    patch.install() if patched else patch.uninstall()
    try:
        adapter = adapter_mod.AnthropicAdapter()
        conn = {"api_key": "fake"}

        def build(task: str) -> list[dict[str, Any]]:
            if source == "history":
                return pass_history_messages(data["history"], task)
            return subagent_messages(SYSTEM, data["brief"], task)

        await asyncio.gather(*(adapter.chat_completions(build(t), model, None, False, {}, connection_params=conn) for t in data["tasks"].values()))
        tasks_sha = data_version(sorted(data["tasks"].values()))
        wall = round(time.monotonic() - t_start, 3)
        return {"state_dir": os.path.basename(state), "tasks_sha": tasks_sha, "wall_s": wall, "points": [_point(r, model, t_start) for r in fake.requests]}
    finally:
        patch.uninstall()
        fake.uninstall()
        shutil.rmtree(state, ignore_errors=True)


async def _compaction(params: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    fake = FakeAnthropic(output_tokens=BRIEF_TOKENS).install()
    patch.uninstall()
    t_start = time.monotonic()
    try:
        adapter = adapter_mod.AnthropicAdapter()
        await adapter.chat_completions(compaction_messages(data["history"]), params["model"], None, False, {}, connection_params={"api_key": "fake"})
        wall = round(time.monotonic() - t_start, 3)
        return {"wall_s": wall, "points": [{**_point(fake.requests[0], params["model"], t_start), "label": "compaction"}]}
    finally:
        fake.uninstall()


def execute(store: Store, run_id: str, spec: dict[str, Any]) -> dict[str, Any]:
    params, model = spec["params"], spec["params"]["model"]
    data = inputs(params, spec["seed"])

    async def go() -> dict[str, Any]:
        out = {"compaction": await _compaction(params, data)}
        for name, source, patched in ARMS:
            out[name] = {**await _arm(params, data, source, patched), "patched": patched}
        return out

    raw = asyncio.run(go())
    comp_usd = raw["compaction"]["points"][0]["usd"]
    arms, files = {}, {}
    for name, source, patched in ARMS:
        pts = raw[name]["points"]
        u = add_usage(*(p["usage"] for p in pts))
        usd = cost_usd(u, model) + (comp_usd if source == "brief" else 0.0)
        arms[name] = {
            "usd": round(usd, 6),
            "wall_s": raw[name]["wall_s"],
            "tokens": u,
            "requests": len(pts),
            "readers": sum(1 for p in pts if p["usage"]["cache_read_input_tokens"] > 0 and not p["prewarm"]),
            "writes": sum(1 for p in pts if p["usage"]["cache_creation_input_tokens"] > 0),
        }
        files[name] = store.put_raw(run_id, name, {"arm": name, **raw[name]})
    files["compaction"] = store.put_raw(run_id, "compaction", raw["compaction"])
    winner = min(arms, key=lambda a: arms[a]["usd"])
    return {
        "spec": spec["id"],
        "backend": "fake_api (cachew/fake_api.py)",
        "data_version": data_version(data),
        "arms": arms,
        "compaction_usd": round(comp_usd, 6),
        "compaction_wall_s": raw["compaction"]["wall_s"],
        "winner": winner,
        "outcome": to_outcome(winner),
        "raw": files,
    }
