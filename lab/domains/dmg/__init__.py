"""H3K27M diffuse midline glioma domain: which targetable subgroup has the fewest matched trials?

Real public data (cBioPortal, ClinicalTrials.gov); Claude Haiku labels trials
(runner's instrument, audited by a drug dictionary) and proposes post-hoc
hypotheses (proposer). See lab/loop.py for the contract.
"""

from __future__ import annotations

import itertools
import json
import random
from typing import Any

from lab.domains.dmg import model as hyp, llm
from lab.domains.dmg.runner import execute, input_version, trial_points
from lab.domains.dmg.skeptic import audit
from lab.store import Store

name = "dmg"
question = hyp.QUESTION
outcomes = hyp.OUTCOMES
sources = hyp.SOURCES
hypotheses = hyp.HYPOTHESES
predict = hyp.predict
feasible = hyp.feasible
estimate_usd = hyp.estimate_usd
code_paths = ["lab"]
run, audit, input_version = execute, audit, input_version

AXIS_LABELS = {"p53": "p53 loss", "acvr1": "ACVR1", "pi3k": "PI3K/mTOR", "rtk": "RTK (PDGFRA…)",
               "cell_cycle": "Cell cycle (CDK4/6)", "mapk": "MAPK (BRAF/NF1)"}
GROUP_LABELS = {"dkfz": "DKFZ cohort", "others": "5 other cohorts", "cna": "copy-number cohorts", "pooled": "all cohorts"}


def short(params: dict[str, Any]) -> str:
    return f"{params['group']} {params['variant']} {params['trials']}"


def describe(params: dict[str, Any]) -> str:
    v = {"all": "all H3", "h31": "H3.1", "h33": "H3.3"}[params["variant"]]
    t = {"open": "open DMG trials", "since2015": "DMG trials since 2015", "hgg_open": "all open HGG trials"}[params["trials"]]
    return f"{GROUP_LABELS[params['group']]} · {v} · {t}"


def propose_tests(tried: list[dict[str, Any]], round_no: int, k: int = 9) -> list[dict[str, Any]]:
    """One untried test per (H3 variant, trial set) pair, cohort group at random; the proposer does not score them."""
    seen = {tuple(sorted(t.items())) for t in tried}
    grid = [dict(zip(hyp.GRID, v)) for v in itertools.product(*hyp.GRID.values())]
    fresh = [g for g in grid if tuple(sorted(g.items())) not in seen]
    random.Random(round_no).shuffle(fresh)
    picked: list[dict[str, Any]] = []
    for v, t in itertools.product(hyp.GRID["variant"], hyp.GRID["trials"]):
        picked += [g for g in fresh if (g["variant"], g["trials"]) == (v, t) and hyp.feasible(g)[0]][:1]
    return [{"params": g, "why": "untried variant × trial set"} for g in picked[:k]]


REFINE_SYSTEM = (
    "You are the proposer in a research loop on H3K27M diffuse midline glioma. Runs compare, per targetable axis, "
    "prevalence among patients vs. number of trials targeting it; outcome = axis with the largest gap = prevalence/(1+trials), "
    "or 'tie'. The current favourite hypothesis just failed to predict a result. Propose ONE new hypothesis that "
    "explains ALL runs shown, as JSON: {\"claim\": \"<=12 words\", \"rule\": R}. R is one of "
    "{\"type\":\"const\",\"axis\":A} | {\"type\":\"by_variant\",\"h31\":A,\"h33\":A} | {\"type\":\"by_cna\",\"cna\":A,\"no_cna\":A} | "
    "{\"type\":\"by_trials\",\"open\":A,\"since2015\":A,\"hgg_open\":A}, where A is one of " + ", ".join(hyp.OUTCOMES) + ". "
    "by_variant uses h33 for variant 'all'; by_cna uses 'cna' only for group 'cna'. Do not repeat an existing rule. Reply with JSON only."
)


def refine(store: Store, runs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Proposer's plan change: Haiku reads the accepted runs and proposes a rule in a fixed grammar."""
    rows = [{"run": r["id"], "params": store.get(r["spec"])["params"], "outcome": r["outcome"],
             "gaps": {a: {k: v[k] for k in ("altered", "n", "trials", "gap")} for a, v in r["arms"].items()}} for r in runs]
    existing = [h["rule"] for h in store.all("H")]
    resp = llm.ask(REFINE_SYSTEM, json.dumps({"runs": rows, "existing_rules": existing}), max_tokens=300)
    try:
        got = llm.parse_json(resp["text"])
        rule, claim = got["rule"], str(got["claim"])[:90]
        hyp.predict(rule, rows[-1]["params"])  # rejects unknown rule types
        if any(v not in hyp.OUTCOMES for k, v in rule.items() if k != "type") or rule in existing:
            return None
    except (ValueError, KeyError, TypeError):
        return None
    return {"key": f"haiku_{len(existing) + 1}", "claim": f"{claim} (Haiku)", "sources": ["measurement"],
            "rule": rule, "evidence": [r["id"] for r in runs]}


# -- board -----------------------------------------------------------------------
legend = {"patient": "patient", "trial": "trial", "call": "Haiku call"}


def point_kind(p: dict[str, Any]) -> str:
    return "call" if "usage" in p else "trial" if "nct" in p else "patient"


def glyph(p: dict[str, Any]) -> str:
    return {"patient": "·", "trial": "▫", "call": "◆"}[point_kind(p)]


token_legend = {"uncached": "input", "read": "cache read", "output": "output"}


def tokens(u: dict[str, Any]) -> dict[str, int]:
    if not u:
        return {}
    return {"uncached": u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0),
            "read": u.get("cache_read_input_tokens", 0), "output": u.get("output_tokens", 0)}


arm_labels = {"llm": "Haiku (trial labels)", "patients": "patients"}
outcome_labels = {**AXIS_LABELS, "tie": "no clear winner"}
check_labels = {"intervention_ran": "Patients are H3K27M; Haiku's trial labels agree with a drug dictionary",
                "controls": "Enough unique patients from the right cohorts",
                "no_leakage": "Inputs as specified, predictions registered first",
                "claims_match_raw": "Prevalence, trial counts and winner recomputed from raw"}
point_labels = {"alt": "altered in this pathway", "wt": "not altered"}
point_colors = {"alt": "var(--accent)", "wt": "var(--grid)"}
board_text = {"win_tag": "biggest gap", "result": "Biggest gap:", "detail_title": "result", "square": "one square = one patient",
              "kpi_tokens": "Haiku tokens", "kpi_cost": "Haiku cost", "kpi_cost_sub": "real API, labels used here (reruns are cached)",
              "tokens_sub": "Haiku tokens per run", "time_runner": "runner (data + Haiku)"}


def arm_view(store: Store, r: dict[str, Any]) -> dict[str, Any]:
    pts = store.raw(r["raw"]["patients"])["points"]
    trials = trial_points(store, r["raw"])
    params = store.get(r["spec"])["params"]
    arms = []
    for axis, g in sorted(r["arms"].items(), key=lambda kv: -kv[1]["gap"]):
        arms.append({
            "name": axis, "label": AXIS_LABELS[axis], "short": axis, "value": g["gap"], "value_fmt": f"{g['prevalence']:.0%}",
            "sub": f"{g['altered']}/{g['n']} patients · {g['trials']} matched trial{'s' * (g['trials'] != 1)} · gap {g['gap']:.2f}",
            "agents": ["alt" if axis in p["axes"] else "wt" for p in pts],
            "agent_info": [{"patient": p["patient"], "cohort": p["cohort"], "variant": p["variant"], "genes": p["genes"]} for p in pts],
            "trials": g["matched"], "file": r["raw"]["patients"], "winner": axis == r["winner"],
        })
    win = AXIS_LABELS[r["winner"]]
    lead = (f"{len(pts)} H3K27M patients vs {len(trials)} trials ({describe(params)}). Biggest gap: <b>{win}</b>"
            + (" (no clear winner: runner-up within 10%)" if r["outcome"] == "tie" else "") + ". Bar = gap = prevalence ÷ (1 + matched trials).")
    return {"arms": arms, "winner": win, "saving": None, "lead": lead, "ran_label": f"{len(pts)} patients · {len(trials)} trials",
            "sentence": f"Counted {len(pts)} patients and labelled {len(trials)} trials → biggest gap: {win}"}


def spent_usd(store: Store) -> float:
    """What the Haiku labels used by this study cost to produce (each call once, cached or not)."""
    from cachew.pricing import cost_usd

    calls = {}
    for r in store.all("R"):
        for c in store.raw(r["raw"]["llm"])["points"]:
            calls[tuple(c["trials"])] = c["usage"]
    return round(sum(cost_usd(u, llm.MODEL) for u in calls.values()), 6)
