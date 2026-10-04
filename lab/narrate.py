"""Plain-language view of a study: one block per round, one sentence per step.

Built from artifacts only. Every sentence keeps the IDs it rests on, so the
board can stay readable while each claim still traces back to a file.
"""

from __future__ import annotations

import re
from types import ModuleType
from typing import Any

from lab.store import Store

STEPS = ["propose", "choose", "run", "audit", "decide"]
ROLE_OF = {"propose": "proposer", "choose": "selector", "run": "runner", "audit": "skeptic", "decide": "judge"}


def _lbl(d: ModuleType, table: str, key: str) -> str:
    return getattr(d, table, {}).get(key, key)


def _desc(d: ModuleType, params: dict[str, Any]) -> str:
    return d.describe(params) if hasattr(d, "describe") else d.short(params)


def _usd(x: float) -> str:
    return f"${x:.4f}" if x < 1 else f"${x:.2f}"


def _round_index(store: Store) -> dict[str, int]:
    """Which round every artifact belongs to."""
    idx: dict[str, int] = {}
    for t in store.all("T"):
        idx[t["id"]] = t["round"]
    for dd in store.all("D"):
        if dd["kind"] in ("select", "stop"):
            ts = [c for c in dd["cites"] if c.startswith("T-")]
            if ts:
                idx[dd["id"]] = idx[ts[0]]
    for x in store.all("X"):
        idx[x["id"]] = idx[x["decision"]]
    for r in store.all("R"):
        idx[r["id"]] = idx[r["spec"]]
    for k in store.all("K"):
        idx[k["id"]] = idx[k["run"]]
    for dd in store.all("D"):
        if dd["kind"] in ("update", "discard"):
            idx[dd["id"]] = idx[dd["cites"][0]]
    for h in store.all("H"):
        if h["post_hoc"] and h["after_run"] in idx:
            idx[h["id"]] = idx[h["after_run"]] + 1
    return idx


def _fanout(store: Store, d: ModuleType, r: dict[str, Any]) -> dict[str, Any]:
    if hasattr(d, "arm_view"):
        return d.arm_view(store, r)
    arms = []
    hidden = set(getattr(d, "hidden_parts", ()))
    for name, rel in r["raw"].items():
        if name in hidden:
            continue
        pts = store.raw(rel)["points"]
        kinds = [d.point_kind(p) for p in pts]
        info = r.get("arms", {}).get(name, {})
        tok = info.get("tokens") or {}
        arms.append({
            "name": name, "label": _lbl(d, "arm_labels", name), "usd": info.get("usd"), "wall_s": info.get("wall_s"),
            "tokens": sum(v for v in tok.values() if isinstance(v, (int, float))), "agents": kinds, "file": rel,
            "winner": name == r.get("winner"), **({"short": d.arm_short[name]} if name in getattr(d, "arm_short", {}) else {}),
        })
    win = next((a for a in arms if a["winner"]), None)
    base = next((a for a in arms if a["name"] == getattr(d, "baseline", None)), None)
    return {"arms": arms, "winner": win["label"] if win else r["outcome"],
            "saving": (1 - win["usd"] / base["usd"]) if win and base and base["usd"] else None}


def rounds(store: Store, d: ModuleType) -> list[dict[str, Any]]:
    idx = _round_index(store)
    hyps = {h["id"]: h for h in store.all("H")}
    out: dict[int, dict[str, Any]] = {}

    def rnd(n: int) -> dict[str, Any]:
        return out.setdefault(n, {"n": n, "steps": {}, "candidates": [], "run": None, "audit": None, "decision": None, "new_h": []})

    for h in hyps.values():
        if h["post_hoc"] and h["id"] in idx:
            rnd(idx[h["id"]])["new_h"].append(h["id"])

    for t in store.all("T"):
        rnd(t["round"])
    for n, r in out.items():
        ts = [t for t in store.all("T") if t["round"] == n]
        new = r["new_h"]
        text = f"Suggested {len(ts)} untested setups"
        if new:
            text = f"New hypothesis {', '.join(new)}: “{hyps[new[0]]['claim']}” (from the data of {', '.join(hyps[new[0]]['evidence'][-1:])}). " + text
        r["steps"]["propose"] = {"text": text, "ids": new + [t["id"] for t in ts]}

    for dd in store.all("D"):
        n = idx.get(dd["id"])
        if n is None:
            continue
        r = rnd(n)
        if dd["kind"] == "select":
            table = dd["table"]
            r["candidates"] = [{"T": t["T"], "desc": _desc(d, store.get(t["T"])["params"]), "eig": t["eig"], "usd": t["usd"],
                                "feasible": t["feasible"], "note": t["note"], "picked": t["T"] == dd["picked"]} for t in table]
            pick = next(t for t in r["candidates"] if t["picked"])
            others = len(table) - 1
            too_costly = sum(1 for t in table if not t["feasible"])
            extra = f"; {too_costly} too expensive" if too_costly else ""
            r["steps"]["choose"] = {"text": f"Chose {pick['desc']}: most to learn per dollar ({pick['eig']:.2f} bits for {_usd(pick['usd'])}), beat {others} others{extra}",
                                    "ids": [dd["id"], dd["picked"]]}
        elif dd["kind"] == "stop":
            r["steps"]["choose"] = {"text": f"Stopped, unresolved: {dd['why']}", "ids": [dd["id"]]}
            r["decision"] = {"kind": "stop", "stop": "unresolved", "text": "Stop: unresolved", "id": dd["id"], "refuted": []}
        elif dd["kind"] == "discard":
            r["steps"]["decide"] = {"text": "Did not count the run (it failed the audit); next round", "ids": [dd["id"]] + dd["cites"]}
            r["decision"] = {"kind": "discard", "text": "Run discarded", "id": dd["id"], "refuted": []}
        elif dd["kind"] == "update":
            parts = []
            m = re.search(r"leader (\S+)", dd.get("why", ""))
            fav = f" {m.group(1)}" if m else ""
            if dd["refuted"]:
                parts.append("Ruled out " + ", ".join(f"{h} (“{hyps[h]['claim']}”)" for h in dd["refuted"]))
            if dd["stop"] == "resolved":
                best = max(dd["weights_after"], key=dd["weights_after"].get)
                parts.append(f"Stop: {best} is {dd['weights_after'][best]:.0%} likely after enough fresh tests")
            elif dd["stop"]:
                parts.append(f"Stop, unresolved: {dd['why']}")
            elif dd["next"] == "refine":
                parts.append(f"The result contradicted the favourite{fav}, so rethink the hypotheses")
            else:
                parts.append(f"Favourite{fav} held, but not sure enough yet; test again")
            r["steps"]["decide"] = {"text": ". ".join(parts), "ids": [dd["id"]] + dd["cites"] + dd["refuted"]}
            kind = "stop" if dd["stop"] else dd["next"]
            label = {"stop": f"Stop: {dd['stop']}", "refine": "Rethink", "continue": "Test again"}[kind]
            r["decision"] = {"kind": kind, "stop": dd["stop"], "text": label, "id": dd["id"], "refuted": dd["refuted"]}

    for run in store.all("R"):
        r = rnd(idx[run["id"]])
        spec = store.get(run["spec"])
        f = _fanout(store, d, run)
        n_agents = spec["params"].get("n")
        base = _lbl(d, "arm_labels", getattr(d, "baseline", "")) or "baseline"
        save = f" ({f['saving']:.0%} below {base})" if f.get("saving") else ""
        r["run"] = {"id": run["id"], "desc": _desc(d, spec["params"]), "agents": n_agents, "outcome": _lbl(d, "outcome_labels", run["outcome"]), **f}
        text = f.get("sentence") or f"Ran {_desc(d, spec['params'])} ({len(f['arms'])} arms) → {f['winner']}{save}"
        r["steps"]["run"] = {"text": text,
                             "ids": [run["id"], run["spec"]]}

    for k in store.all("K"):
        r = rnd(idx[k["id"]])
        checks = [{"label": _lbl(d, "check_labels", c["check"]), "ok": c["ok"], "detail": c["detail"]} for c in k["checks"]]
        bad = [c["label"] for c in checks if not c["ok"]]
        r["audit"] = {"id": k["id"], "pass": k["pass"], "checks": checks}
        text = f"Raw data checks out: {len(checks)}/{len(checks)} checks passed" if k["pass"] else f"Rejected the run: {'; '.join(bad)} failed"
        r["steps"]["audit"] = {"text": text, "ids": [k["id"], k["run"]]}

    result = []
    for n in sorted(out):
        r = out[n]
        r["steps"] = [{"step": s, "role": ROLE_OF[s], **r["steps"][s]} for s in STEPS if s in r["steps"]]
        if r["run"]:
            refuted = (r["decision"] or {}).get("refuted", [])
            r["summary"] = f"{r['run']['desc']} → {r['run']['outcome']}" + (f" · ruled out {', '.join(refuted)}" if refuted else "")
        else:
            r["summary"] = (r["decision"] or {}).get("text", "in progress")
        result.append(r)
    return result


def beliefs_table(store: Store) -> dict[str, Any]:
    """Belief in every hypothesis after each round (start = equal priors)."""
    hyps = store.all("H")
    idx = _round_index(store)
    initial = [h["id"] for h in hyps if not h["post_hoc"]]
    cols = [{"label": "start", "round": 0, "w": {h: 1 / len(initial) for h in initial}}] if initial else []
    for dd in store.all("D"):
        if dd["kind"] == "update":
            cols.append({"label": f"round {idx[dd['id']]}", "round": idx[dd["id"]], "w": dd["weights_after"], "refuted": dd["refuted"]})
    return {"cols": cols, "born": {h["id"]: idx.get(h["id"], 0) for h in hyps}}
