"""Deterministic checks on what the agents produced. Problems go back to the PI to fix."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from autolab import merge
from autolab.study import Study


def _load(path: Path, problems: list[str]) -> Any:
    rel = "/".join(path.parts[-3:])
    if not path.exists():
        problems.append(f"{rel} is missing")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        problems.append(f"{rel} is not valid JSON: {exc}")
        return None


def plan(study: Study) -> list[str]:
    r, p = study.rdir(), []
    merge.literature(study)
    if study.round == 1 and not list((study.root / "literature").glob("R01-*.md")):
        p.append("no scout notes for R01 in literature/ (dispatch the scouts)")
    cand = _load(r / "candidates.json", p)
    hyp_ids: set[str] = set()
    exp_ids: set[str] = set()
    if cand is not None:
        hyps = cand.get("hypotheses") or []
        hyp_ids = {h.get("id") for h in hyps if isinstance(h, dict)}
        if not any(h.get("catch_all") for h in hyps if isinstance(h, dict)):
            p.append("candidates.json: needs a catch-all hypothesis (catch_all: true)")
        if len(hyps) < 2:
            p.append("candidates.json: needs at least 2 hypotheses")
        if any(not h.get("id") or not h.get("statement") for h in hyps if isinstance(h, dict)):
            p.append("candidates.json: every hypothesis needs id and statement")
        missing = {h["id"] for h in study.hypotheses()} - hyp_ids
        if missing:
            p.append(f"candidates.json: ledger hypotheses dropped: {sorted(missing)} (keep them with the same id)")
        exps = cand.get("experiments") or []
        exp_ids = {e.get("id") for e in exps if isinstance(e, dict)}
        if len(exp_ids) < 2:
            p.append("candidates.json: needs at least 2 candidate experiments")
    sel = _load(r / "selection.json", p)
    if sel is not None and exp_ids and sel.get("chosen") not in exp_ids:
        p.append(f"selection.json: chosen {sel.get('chosen')!r} is not one of {sorted(exp_ids)}")
    if sel is not None and not sel.get("why"):
        p.append("selection.json: needs 'why'")
    if not (r / "plan.md").exists():
        p.append("plan.md is missing")
    run_py = r / "experiment" / "run.py"
    if not run_py.exists():
        p.append("experiment/run.py is missing")
    else:
        for f in sorted((r / "experiment").rglob("*.py")):
            try:
                compile(f.read_text(encoding="utf-8"), str(f), "exec")
            except SyntaxError as exc:
                p.append(f"{f.relative_to(r).as_posix()} does not compile: line {exc.lineno}: {exc.msg}")
        if "--out" not in run_py.read_text(encoding="utf-8"):
            p.append("experiment/run.py must accept --out OUTDIR")
    pred = _load(r / "predictions.json", p)
    if pred is not None:
        covered = {x.get("hypothesis") for x in pred.get("predictions", []) if isinstance(x, dict)}
        alive = {h["id"] for h in study.live_hypotheses()} | hyp_ids
        refuted = {h["id"] for h in study.hypotheses() if h["status"] == "refuted"}
        if gap := sorted(alive - refuted - covered):
            p.append(f"predictions.json: no prediction for {gap}")
    return p


def analysis(study: Study) -> list[str]:
    r, p = study.rdir(), []
    if merge.reviews(study) is None:
        p.append("no skeptic reviews in reviews/ (dispatch the skeptics)")
    rev = _load(r / "review.json", p)
    if rev is not None and rev.get("verdict") not in ("pass", "pass_with_caveats", "fail"):
        p.append("review.json: verdict must be pass | pass_with_caveats | fail")
    ver = _load(r / "verdict.json", p)
    if ver is not None:
        alive = {h["id"] for h in study.live_hypotheses()}
        upd = [u for u in ver.get("updates", []) if isinstance(u, dict)]
        if gap := sorted(alive - {u.get("id") for u in upd}):
            p.append(f"verdict.json: no update for alive hypotheses {gap}")
        for u in upd:
            c = u.get("credence")
            if not isinstance(c, (int, float)) or not 0 <= c <= 1:
                p.append(f"verdict.json: {u.get('id')} credence must be a number in [0, 1]")
            if u.get("status", "alive") not in ("alive", "refuted", "supported"):
                p.append(f"verdict.json: {u.get('id')} status must be alive | refuted | supported")
        if ver.get("decision") not in ("continue", "conclude"):
            p.append("verdict.json: decision must be continue | conclude")
    if not (r / "review.md").exists():
        p.append("review.md is missing")
    return p
