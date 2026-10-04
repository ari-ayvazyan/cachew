"""Skeptic: audits a run from its raw data points, never from the runner's summary.

Four questions, each answered by recomputing from ``runs/R-xxx/raw/*.json``:
did the intervention run, were the controls there, could anything leak,
and do the claimed numbers match the raw ones?
"""

from __future__ import annotations

from typing import Any

from cachew.pricing import add_usage, cost_usd
from lab.domains.fanout.hypotheses import predict, to_outcome
from lab.domains.fanout.runner import input_version
from lab.provenance import passage_still_matches
from lab.store import Store


def _check(name: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"check": name, "ok": bool(ok), "detail": detail}


def audit(store: Store, spec: dict[str, Any], result: dict[str, Any]) -> list[dict[str, Any]]:
    p = spec["params"]
    n, model = p["n"], p["model"]
    raw = {k: store.raw(v) for k, v in result["raw"].items()}
    arms = {k: v for k, v in raw.items() if k != "compaction"}
    subs = {a: [x for x in v["points"] if not x["prewarm"]] for a, v in arms.items()}
    out = []

    # 1. intervention ran: cached arms sent cache_control and mostly read; uncached arms never did
    bad = []
    for a, v in arms.items():
        sent = all(x["cc_in_payload"] for x in v["points"])
        none = not any(x["cc_in_payload"] for x in v["points"])
        readers = sum(1 for x in subs[a] if x["usage"]["cache_read_input_tokens"] > 0)
        if v["patched"] and (not sent or readers < n - 2):
            bad.append(f"{a}: cc={sent} readers={readers}/{n}")
        if not v["patched"] and (not none or readers):
            bad.append(f"{a}: unpatched but cached")
    out.append(_check("intervention_ran", not bad, "; ".join(bad) or f"cached arms read ≥{n - 2}/{n}"))

    # 2. controls: naive baseline present, same tasks and N in every arm
    shas = {v["tasks_sha"] for v in arms.values()}
    counts = {a: len(s) for a, s in subs.items()}
    ok = "naive" in arms and len(shas) == 1 and set(counts.values()) == {n}
    out.append(_check("controls", ok, f"naive={'naive' in arms} tasks_sha={len(shas)} n={sorted(set(counts.values()))}"))

    # 3. leakage: task never cached, every arm starts cold, inputs as specified, predictions pre-registered
    leaks = []
    if any(x["cc_on_task"] for v in arms.values() for x in v["points"]):
        leaks.append("task inside cached prefix")
    for a, v in arms.items():
        reads = any(x["usage"]["cache_read_input_tokens"] > 0 for x in v["points"])
        writes = any(x["usage"]["cache_creation_input_tokens"] > 0 for x in v["points"])
        if reads and not writes:
            leaks.append(f"{a}: read without own write (warm start)")
    if len({v["state_dir"] for v in arms.values()}) != len(arms):
        leaks.append("arms shared a state dir")
    if spec["data_version"] != result["data_version"] or input_version(p, spec["seed"]) != spec["data_version"]:
        leaks.append("inputs differ from spec")
    if spec["created"] >= result["created"]:
        leaks.append("predictions not pre-registered")
    stale = [h for h in spec["predictions"] if not all(passage_still_matches(store.get(s)) for s in store.get(h)["sources"])]
    if stale:
        leaks.append(f"sources changed: {','.join(stale)}")
    drift = [h for h, pr in spec["predictions"].items() if pr != predict(store.get(h)["rule"], p)]
    if drift:
        leaks.append(f"prediction drift: {','.join(drift)}")
    out.append(_check("no_leakage", not leaks, "; ".join(leaks) or "cold arms, task uncached, pre-registered"))

    # 4. claims match raw numbers
    comp = raw["compaction"]["points"][0]["usd"]
    usd = {a: cost_usd(add_usage(*(x["usage"] for x in v["points"])), model) + (comp if a.startswith("compact") else 0) for a, v in arms.items()}
    off = [a for a in usd if abs(usd[a] - result["arms"][a]["usd"]) > 1e-6]
    winner = min(usd, key=usd.get)
    ok = not off and winner == result["winner"] and to_outcome(winner) == result["outcome"]
    out.append(_check("claims_match_raw", ok, f"winner {winner}" + (f"; mismatch {off}" if off else "")))
    return out
