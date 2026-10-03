"""The research loop.

One round:

    proposer  -> >=2 candidate tests (T), maybe a new hypothesis (H) if the judge asked to refine
    selector  -> scores every T by expected info gain / cost / feasibility, picks one (D:select)
    selector  -> pre-registers every live hypothesis' prediction in the spec (X)
    runner    -> executes X, writes raw data points (R)
    skeptic   -> audits R from raw data (K)
    judge     -> belief update citing R and K, refutes, decides continue / refine / stop (D:update)

Roles talk only through handoffs (HO) that carry IDs. Beliefs are recomputed
from accepted runs every time, so any number on the board traces to run IDs.

A domain module provides: question, outcomes, sources, hypotheses, predict,
feasible, estimate_usd, code_paths, propose_tests, refine, input_version,
run, audit, short.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from types import ModuleType
from typing import Any

from lab import board
from lab.infogain import expected_info_gain, normalize
from lab.provenance import code_version, passage
from lab.roles import JUDGE, PROPOSER, RUNNER, SELECTOR, SKEPTIC, assert_independent
from lab.store import Store


class Study:
    def __init__(self, domain: ModuleType, root: Path, *, max_rounds: int = 8, resolve_at: float = 0.95,
                 refute_below: float = 0.02, confirm_after: int = 2, min_eig: float = 0.01) -> None:
        self.d = domain
        self.store = Store(root)
        self.rules = {"max_rounds": max_rounds, "resolve_at": resolve_at, "refute_below": refute_below,
                      "confirm_after": confirm_after, "min_eig": min_eig}

    # -- shared views (computed from artifacts only) ---------------------------
    def reviews(self) -> dict[str, dict[str, Any]]:
        return {k["run"]: k for k in self.store.all("K")}

    def accepted(self) -> list[dict[str, Any]]:
        reviews = self.reviews()
        return [r for r in self.store.all("R") if reviews.get(r["id"], {}).get("pass")]

    def beliefs(self, runs: list[dict[str, Any]] | None = None) -> dict[str, float]:
        """Equal priors, times each hypothesis' likelihood of every accepted outcome."""
        hyps = self.store.all("H")
        w = {h["id"]: 1.0 for h in hyps}
        for r in self.accepted() if runs is None else runs:
            params = self.store.get(r["spec"])["params"]
            for h in hyps:
                w[h["id"]] *= self.d.predict(h["rule"], params)[r["outcome"]]
        return normalize(w)

    def alive(self) -> list[dict[str, Any]]:
        return [h for h in self.store.all("H") if h["status"] == "alive"]

    def handoff(self, frm: str, to: str, **ids: Any) -> str:
        return self.store.put("HO", {"from": frm, "to": to, **ids}, frm)

    def phase(self, text: str) -> None:
        self.store.set_meta(phase=text)
        t = time.perf_counter()
        board.render(self.store, self.d)
        self.store.log({"event": "role", "role": "board", "round": self.store.meta().get("round", 0), "s": round(time.perf_counter() - t, 4)})

    # -- setup -----------------------------------------------------------------
    def setup(self, fresh: bool = False) -> None:
        if self.store.meta():
            if not fresh:
                raise FileExistsError(f"{self.store.root} already holds a study; pick a new folder or pass fresh")
            shutil.rmtree(self.store.root)
            self.store = Store(self.store.root)
        self.store.set_meta(question=self.d.question, domain=self.d.name, rules=self.rules, round=0, status="running")
        self.src = {k: self.store.put("S", {"key": k, **passage(*v)}, PROPOSER) for k, v in self.d.sources.items()}
        for h in self.d.hypotheses:
            self.add_hypothesis(h, after_run=None)
        self.phase("setup: sources + hypotheses")

    def add_hypothesis(self, h: dict[str, Any], after_run: str | None) -> str:
        return self.store.put("H", {
            "key": h["key"], "claim": h["claim"], "rule": h["rule"],
            "sources": [self.src[s] for s in h["sources"]], "evidence": h.get("evidence", []),
            "status": "alive", "post_hoc": after_run is not None, "after_run": after_run,
        }, PROPOSER)

    # -- roles -----------------------------------------------------------------
    def propose(self, round_no: int, last: dict[str, Any] | None) -> str:
        new_h = None
        if last and last.get("next") == "refine":
            runs = self.accepted()
            h = self.d.refine(self.store, runs)
            if h:
                new_h = self.add_hypothesis(h, after_run=runs[-1]["id"])
        tried = [self.store.get(x)["params"] for x in (s["id"] for s in self.store.all("X"))]
        t_ids = [self.store.put("T", {"round": round_no, **t, "basis": last["id"] if last else None}, PROPOSER)
                 for t in self.d.propose_tests(tried, round_no)]
        return self.handoff(PROPOSER, SELECTOR, proposals=t_ids, new_hypothesis=new_h,
                            hypotheses=[h["id"] for h in self.alive()], basis_runs=[r["id"] for r in self.accepted()])

    def select(self, ho_id: str) -> tuple[str, str | None]:
        ho = self.store.get(ho_id)
        assert_independent(self.store, ho["proposals"], SELECTOR)
        weights = self.beliefs()
        hyps = {h: weights[h] for h in ho["hypotheses"]}
        live = normalize(hyps)
        rows = []
        for t in ho["proposals"]:
            params = self.store.get(t)["params"]
            ok, why = self.d.feasible(params)
            preds = {h: self.d.predict(self.store.get(h)["rule"], params) for h in live}
            eig = expected_info_gain(live, preds)
            usd = self.d.estimate_usd(params)
            rows.append({"T": t, "test": self.d.short(params), "eig": round(eig, 3), "usd": usd, "feasible": ok,
                         "note": why, "score": round(eig / (usd + 0.05), 3) if ok else 0.0})
        rows.sort(key=lambda r: -r["score"])
        cites = ho["basis_runs"]
        good = [r for r in rows if r["feasible"] and r["eig"] >= self.rules["min_eig"]]
        if len(rows) < 2 or not good:
            d = self.store.put("D", {"kind": "stop", "stop": "unresolved", "cites": cites + ho["proposals"],
                                     "why": "<2 tests" if len(rows) < 2 else "no test splits hypotheses", "table": rows}, SELECTOR)
            return d, None
        pick = good[0]
        runner_up = rows[1] if rows[0] is pick else rows[0]
        why = f"max EIG/$ {pick['eig']}b @ ${pick['usd']}; next {runner_up['T']} {runner_up['eig']}b"
        d = self.store.put("D", {"kind": "select", "picked": pick["T"], "cites": cites + ho["proposals"],
                                 "weights": {h: round(w, 4) for h, w in weights.items()}, "table": rows, "why": why}, SELECTOR)
        # pre-register predictions before anything runs
        params = self.store.get(pick["T"])["params"]
        seed = int(d.split("-")[1])
        x = self.store.put("X", {
            "proposal": pick["T"], "decision": d, "params": params, "seed": seed,
            "predictions": {h: self.d.predict(self.store.get(h)["rule"], params) for h in live},
            "code_version": code_version(self.d.code_paths), "data_version": self.d.input_version(params, seed),
        }, SELECTOR)
        return d, x

    def run(self, x_id: str) -> str:
        spec = self.store.get(x_id)
        h_ids = list(spec["predictions"])
        src = sorted({s for h in h_ids for s in self.store.get(h)["sources"]})
        ho = self.handoff(SELECTOR, RUNNER, hypotheses=h_ids, sources=src, spec=x_id,
                          code_version=spec["code_version"], data_version=spec["data_version"], result_file=None)
        rid = self.store.next_id("R")
        result = self.d.run(self.store, rid, spec)
        self.store.put("R", {**result, "handoff": ho}, RUNNER, aid=rid)
        return self.handoff(RUNNER, SKEPTIC, hypotheses=h_ids, sources=src, spec=x_id, code_version=spec["code_version"],
                            data_version=result["data_version"], result_file=self.store.rel(self.store.path(rid)), run=rid)

    def audit(self, ho_id: str) -> str:
        ho = self.store.get(ho_id)
        assert_independent(self.store, [ho["run"]], SKEPTIC)
        result = self.store.get(ho["run"])
        checks = self.d.audit(self.store, self.store.get(ho["spec"]), result)
        k = self.store.put("K", {"run": ho["run"], "pass": all(c["ok"] for c in checks), "checks": checks}, SKEPTIC)
        return self.handoff(SKEPTIC, JUDGE, **{**{x: ho[x] for x in ("hypotheses", "sources", "spec", "code_version", "data_version", "result_file", "run")}, "review": k})

    def judge(self, ho_id: str, round_no: int) -> dict[str, Any]:
        ho = self.store.get(ho_id)
        rid, kid = ho["run"], ho["review"]
        assert_independent(self.store, [rid, *ho["hypotheses"]], JUDGE)
        cites = [rid, kid]
        if not self.store.get(kid)["pass"]:
            return self.store.get(self.store.put("D", {"kind": "discard", "cites": cites, "next": "continue",
                                                       "why": "skeptic failed: run not counted"}, JUDGE))
        before = self.beliefs([r for r in self.accepted() if r["id"] != rid])
        after = self.beliefs()
        outcome = self.store.get(rid)["outcome"]
        preds = self.store.get(ho["spec"])["predictions"]
        leader = max(preds, key=lambda h: before[h])
        surprise = preds[leader][outcome] < 0.5
        refuted = [h["id"] for h in self.alive() if after[h["id"]] < self.rules["refute_below"]]
        for h in refuted:
            self.store.patch(h, JUDGE, status="refuted", refuted_by=rid)
        nxt, stop, why = ("refine" if surprise else "continue"), None, f"{outcome}; leader {leader} " + ("missed" if surprise else "held")
        best = max((h for h in self.alive() if h["key"] != "none"), key=lambda h: after[h["id"]], default=None)
        none = next((h["id"] for h in self.store.all("H") if h["key"] == "none"), None)
        if best and after[best["id"]] >= self.rules["resolve_at"]:
            confirmations = [r for r in self.accepted() if best["id"] in self.store.get(r["spec"])["predictions"]]
            if len(confirmations) >= self.rules["confirm_after"]:
                nxt, stop, why = "stop", "resolved", f"{best['id']} at {after[best['id']]:.2f} after {len(confirmations)} pre-registered tests"
            else:
                why += f"; {best['id']} needs {self.rules['confirm_after'] - len(confirmations)} more pre-registered test(s)"
        if not stop and round_no >= self.rules["max_rounds"]:
            nxt, stop, why = "stop", "unresolved", "round budget spent"
        if not stop and none and after[none] >= 0.5 and not surprise:
            nxt, stop, why = "stop", "unresolved", "'none of these' leads, nothing to refine"
        return self.store.get(self.store.put("D", {
            "kind": "update", "cites": cites, "outcome": outcome, "surprise": surprise, "refuted": refuted,
            "weights_before": {h: round(w, 4) for h, w in before.items()},
            "weights_after": {h: round(w, 4) for h, w in after.items()},
            "next": nxt, "stop": stop, "why": why,
        }, JUDGE))

    # -- driver ----------------------------------------------------------------
    def timed(self, role: str, round_no: int, fn: Any, *args: Any) -> Any:
        """Run one role's step and log how long it took (the board's time view)."""
        t = time.perf_counter()
        try:
            return fn(*args)
        finally:
            self.store.log({"event": "role", "role": role, "round": round_no, "s": round(time.perf_counter() - t, 4)})

    def go(self, fresh: bool = False) -> dict[str, Any]:
        self.setup(fresh)
        last: dict[str, Any] | None = None
        for round_no in range(1, self.rules["max_rounds"] + 1):
            self.store.set_meta(round=round_no)
            self.phase(f"round {round_no}: proposing tests")
            ho = self.timed(PROPOSER, round_no, self.propose, round_no, last)
            self.phase(f"round {round_no}: selecting")
            d, x = self.timed(SELECTOR, round_no, self.select, ho)
            if x is None:
                last = self.store.get(d)
                break
            spec = self.store.get(x)
            self.phase(f"{self.store.next_id('R')} running {self.d.short(spec['params'])} · why {spec['decision']}")
            ho = self.timed(RUNNER, round_no, self.run, x)
            self.phase(f"round {round_no}: skeptic audit")
            ho = self.timed(SKEPTIC, round_no, self.audit, ho)
            self.phase(f"round {round_no}: judging")
            last = self.timed(JUDGE, round_no, self.judge, ho, round_no)
            if last.get("next") == "stop":
                break
        outcome = (last or {}).get("stop") or "unresolved"
        self.store.set_meta(status=f"stopped: {outcome}", final=(last or {}).get("id"))
        self.phase(f"done · {outcome} · {last['id'] if last else ''}")
        return last or {}
