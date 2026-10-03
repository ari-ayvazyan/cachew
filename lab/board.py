"""Progress board: a meta view, rebuilt from artifacts after every step.

Less is more: one line per step, IDs instead of prose, every line says why
(``←`` cites). Details live in the artifact files; the HTML board lets you
click any ID or data point to see them.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType
from typing import Any

from lab import narrate
from lab.store import Store

TEMPLATE = Path(__file__).with_name("board_template.html")
BAR = 10


def _bar(w: float) -> str:
    n = round(w * BAR)
    return "█" * n + "░" * (BAR - n)


def _short(d: ModuleType, store: Store, spec_id: str) -> str:
    return d.short(store.get(spec_id)["params"])


def step_line(a: dict[str, Any], store: Store, d: ModuleType) -> str | None:
    """One terse line per artifact that changes the plan; None = not shown."""
    k = a["id"].split("-")[0]
    if k == "H" and a["post_hoc"]:
        return f"{a['id']} new: {a['claim']} ← {','.join(a['evidence'])}"
    if k == "R":
        return f"{a['id']} {_short(d, store, a['spec'])} → {a['outcome']}"
    if k == "K":
        ok = sum(c["ok"] for c in a["checks"])
        bad = [c["check"] for c in a["checks"] if not c["ok"]]
        return f"{a['id']} skeptic {'pass' if a['pass'] else 'FAIL'} {ok}/{len(a['checks'])}{' ' + ','.join(bad) if bad else ''} ← {a['run']}"
    if k == "D":
        kind = a["kind"]
        if kind == "select":
            row = next(r for r in a["table"] if r["T"] == a["picked"])
            return f"{a['id']} pick {a['picked']} {row['test']} · {row['eig']}b ${row['usd']} · of {len(a['table'])}"
        if kind == "update":
            ref = f" · ✗{','.join(a['refuted'])}" if a["refuted"] else ""
            return f"{a['id']} {a['next']}{(':' + a['stop']) if a['stop'] else ''}{ref} ← {','.join(a['cites'])}"
        return f"{a['id']} {kind}{(':' + a.get('stop', '')) if a.get('stop') else ''} · {a['why']} ← {','.join(a['cites'][:3])}"
    return None


def _beliefs(store: Store) -> dict[str, float]:
    ups = [d for d in store.all("D") if d["kind"] == "update"]
    return ups[-1]["weights_after"] if ups else {}


def _ledger(store: Store) -> list[dict[str, Any]]:
    path = store.root / "ledger.jsonl"
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


def _tok(d: ModuleType, usage: dict[str, Any]) -> dict[str, int]:
    return d.tokens(usage) if hasattr(d, "tokens") else {"tokens": sum(v for v in usage.values() if isinstance(v, int))}


def _add(a: dict[str, float], b: dict[str, float]) -> dict[str, float]:
    return {k: a.get(k, 0) + b.get(k, 0) for k in {*a, *b}}


def _points(store: Store, d: ModuleType) -> list[dict[str, Any]]:
    runs = []
    reviews = {k["run"]: k for k in store.all("K")}
    for r in store.all("R"):
        spec = store.get(r["spec"])
        lanes = []
        for part, rel in r["raw"].items():
            raw = store.raw(rel)
            pts = [{"k": d.point_kind(p), "label": p.get("label", str(i)), "t0": p.get("t0_ms", 0), "t1": p.get("t1_ms", 0),
                    "tok": _tok(d, p.get("usage", {})), "usd": round(p.get("usd", 0.0), 6)} for i, p in enumerate(raw["points"])]
            tok: dict[str, float] = {}
            for p in pts:
                tok = _add(tok, p["tok"])
            usd = r.get("arms", {}).get(part, {}).get("usd", r.get("compaction_usd") if part == "compaction" else None)
            lanes.append({"name": part, "file": rel, "usd": usd, "wall_s": raw.get("wall_s"), "tok": tok, "points": pts})
        runs.append({"id": r["id"], "test": d.short(spec["params"]), "why": spec["decision"], "outcome": r["outcome"],
                     "winner": r.get("winner"), "review": reviews.get(r["id"], {}).get("id"),
                     "pass": reviews.get(r["id"], {}).get("pass"), "lanes": lanes})
    return runs


def _time_by_role(store: Store) -> dict[str, float]:
    out: dict[str, float] = {}
    for e in _ledger(store):
        if e["event"] == "role":
            out[e["role"]] = round(out.get(e["role"], 0) + e["s"], 4)
    return out


def _tokens_total(runs: list[dict[str, Any]]) -> dict[str, float]:
    tok: dict[str, float] = {}
    for r in runs:
        for lane in r["lanes"]:
            tok = _add(tok, lane["tok"])
    return tok


def _trajectory(store: Store) -> list[dict[str, Any]]:
    hyps = store.all("H")
    initial = [h["id"] for h in hyps if not h["post_hoc"]]
    out = [{"round": 0, "d": None, "w": {h: round(1 / len(initial), 4) for h in initial}}] if initial else []
    for dd in store.all("D"):
        if dd["kind"] == "update":
            run = store.get(dd["cites"][0])
            out.append({"round": len(out), "d": dd["id"], "run": run["id"], "w": dd["weights_after"]})
    return out


def _fmt(n: float) -> str:
    return f"{n / 1e6:.2f}M" if n >= 1e6 else f"{n / 1e3:.0f}K" if n >= 1e3 else f"{n:.0f}"


def markdown(store: Store, d: ModuleType) -> str:
    m = store.meta()
    w = _beliefs(store)
    runs = _points(store, d)
    times = _time_by_role(store)
    tok = _tokens_total(runs)
    out = [f"# {m.get('question', '')}", "", f"**{m.get('status')}** · round {m.get('round')}/{m.get('rules', {}).get('max_rounds')} · now: {m.get('phase')}", ""]
    out.append("- time: " + " · ".join(f"{k} {v:.1f}s" for k, v in sorted(times.items(), key=lambda kv: -kv[1])))
    out.append("- tokens: " + " · ".join(f"{k} {_fmt(v)}" for k, v in sorted(tok.items(), key=lambda kv: -kv[1])))
    out += ["", "## Hypotheses"]
    for h in store.all("H"):
        b = w.get(h["id"], 0.0)
        mark = f" ✗ {h.get('refuted_by')}" if h["status"] == "refuted" else ""
        tag = " (post-hoc)" if h["post_hoc"] else ""
        out.append(f"- `{h['id']}` {_bar(b)} {b:.2f} {h['claim']}{tag}{mark}")
    out += ["", "## Fan-outs"]
    for r in runs:
        lanes = " · ".join(f"{l['name']} {''.join(d.glyph(p) for p in store.raw(l['file'])['points'])}" for l in r["lanes"] if l["name"] != "compaction")
        out.append(f"- `{r['id']}` {r['test']} → {r['outcome']} ({'✓' if r['pass'] else '✗'}{r['review']}) ← {r['why']}")
        out.append(f"  - {lanes}")
    out += ["", "## Steps (newest first)"]
    seen = set()
    for e in reversed(_ledger(store)):
        if e["event"] != "put" or e["id"] in seen:
            continue
        seen.add(e["id"])
        line = step_line(store.get(e["id"]), store, d)
        if line:
            out.append(f"- {line}")
    return "\n".join(out) + "\n"


def html(store: Store, d: ModuleType) -> str:
    m = store.meta()
    artifacts = {}
    for prefix in ("S", "H", "T", "X", "R", "K", "D", "HO"):
        for a in store.all(prefix):
            artifacts[a["id"]] = a
    steps = []
    for e in _ledger(store):
        if e["event"] == "put":
            a = artifacts[e["id"]]
            line = step_line(a, store, d)
            if line:
                steps.append({"id": e["id"], "role": a["author"], "line": line, "t": e["t"]})
    runs = _points(store, d)
    data = {
        "meta": m, "beliefs": _beliefs(store), "hypotheses": store.all("H"), "runs": runs,
        "steps": steps[::-1], "artifacts": artifacts, "legend": d.legend,
        "timeline": [e for e in _ledger(store) if e["event"] == "role"],
        "trajectory": _trajectory(store), "time_by_role": _time_by_role(store), "tokens": _tokens_total(runs),
        "token_legend": getattr(d, "token_legend", {}),
        "rounds": narrate.rounds(store, d), "belief_table": narrate.beliefs_table(store),
        "arm_labels": getattr(d, "arm_labels", {}), "point_labels": getattr(d, "point_labels", d.legend),
    }
    refresh = '<meta http-equiv="refresh" content="2">' if m.get("status") == "running" else ""
    blob = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    return TEMPLATE.read_text(encoding="utf-8").replace("{{REFRESH}}", refresh).replace("{{DATA}}", blob)


def render(store: Store, d: ModuleType) -> None:
    store.put_text("board.md", markdown(store, d))
    (store.root / "board.html").write_text(html(store, d), encoding="utf-8")
