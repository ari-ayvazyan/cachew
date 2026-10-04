"""Which axis does each trial target? A cached Haiku fan-out answers; a dictionary audits.

One run = one fan-out through ``cachew``. Every sub-agent receives the same
knowledge brief (all of the run's trials, numbered) as a byte-identical prefix
and labels only its own slice of them. The patched adapter writes the prefix
once, pre-warms it for concurrent siblings, and the rest read it from cache.
The skeptic re-labels every trial whose drugs are in ``DRUGS`` and checks that
caching actually happened.
"""

from __future__ import annotations

import asyncio
from typing import Any

from cachew.compact import subagent_messages
from cachew.experiment import Runner, run_arm
from cachew.pricing import add_usage, cost_usd
from lab.domains.dmg import llm
from lab.domains.dmg.data import AXES

SUBAGENTS = 12
AXIS_RULES = (
    "p53: MDM2/MDM4/PPM1D inhibitors, p53 reactivators or TP53 gene therapy; acvr1: ACVR1/ALK2 inhibitors; "
    "pi3k: PI3K/AKT/mTOR inhibitors (e.g. paxalisib, everolimus); rtk: PDGFRA/EGFR/MET/FGFR/KIT inhibitors "
    "(e.g. dasatinib, avapritinib, nimotuzumab, cabozantinib); cell_cycle: CDK4/6 inhibitors; mapk: BRAF/MEK inhibitors"
)
SYSTEM = (
    "You are a sub-agent screening clinical trials for H3K27M diffuse midline glioma. The knowledge brief lists "
    "every trial as: #index | NCT id | title | drugs | genotype-related eligibility text. A trial targets an axis "
    "if a drug in any arm acts on it or eligibility requires an alteration in it. Axes: " + AXIS_RULES + ". "
    "Use only drug targets you are sure of; code-named drugs with unknown targets do not count. Radiation, "
    "chemotherapy, ONC201/dordaviprone, HDAC inhibitors, immunotherapy, CAR-T, vaccines and VEGF antibodies never "
    "count. Reply with only a JSON object mapping each NCT id you were asked about to its list of axes ([] if none)."
)


def brief(trials: list[dict[str, Any]]) -> str:
    return "\n".join(f"#{i + 1} | {t['nct']} | {t['title']} | {', '.join(t['drugs']) or '-'} | {t['eligibility_genes'][:200] or '-'}"
                     for i, t in enumerate(trials))


def slices(n_trials: int, n: int = SUBAGENTS) -> list[tuple[int, int]]:
    """1-based inclusive index ranges, one per sub-agent."""
    size = -(-n_trials // n)
    return [(a + 1, min(a + size, n_trials)) for a in range(0, n_trials, size)]


def tasks(trials: list[dict[str, Any]]) -> dict[str, str]:
    """One sub-agent per slice: each reads the whole (cached) brief and labels only its own trials."""
    return {f"sub:{a:03d}-{b:03d}": "Label exactly these trials (find each by its NCT id in the brief), every one of them, "
                                    "as a JSON object: " + ", ".join(t["nct"] for t in trials[a - 1:b])
            for a, b in slices(len(trials))}


def estimate_usd(trials: list[dict[str, Any]], n: int = SUBAGENTS) -> float:
    """Cached fan-out: one write (+ one pre-warm), n-1 reads, ~10 output tokens per trial; Haiku prices."""
    prefix = len(SYSTEM + brief(trials)) / 3.5  # ~tokens
    return round((prefix * (2 * 1.25 + (n - 1) * 0.1) + len(trials) * 10 * 5) / 1e6, 4)


def classify(trials: list[dict[str, Any]], max_tokens: int = 2500) -> tuple[dict[str, list[str]], list[dict[str, Any]], dict[str, Any]]:
    """Returns ({nct: axes}, call records, fan-out summary)."""
    llm.load_env()
    est = estimate_usd(trials)
    if llm.spent() + est > llm.BUDGET_USD:
        raise llm.BudgetExceeded(f"spent ${llm.spent():.4f} + est ${est} > ${llm.BUDGET_USD}")
    from cachew import patch

    patch.install_workspace_header()
    runner = Runner(llm.MODEL, "", max_tokens)
    text = brief(trials)
    task_map = tasks(trials)
    arm = asyncio.run(run_arm("dmg", runner, lambda t: subagent_messages(SYSTEM, text, t), task_map, True))
    llm.record(arm["cost_usd"])
    labels: dict[str, list[str]] = {}
    calls = []
    for c in arm["calls"]:
        rec = {"label": c["label"], "usage": c["usage"], "usd": round(cost_usd(c["usage"], llm.MODEL), 6), "prewarm": c["label"] == "prewarm"}
        if c["label"].startswith("sub:"):
            a, b = (int(x) for x in c["label"][4:].split("-"))
            mine = {t["nct"] for t in trials[a - 1:b]}
            try:
                got = llm.parse_json(c["text"])
                got = got if isinstance(got, dict) else {}
            except ValueError:
                got, rec["parse_error"] = {}, c["text"][:200]
            for nct in mine & set(got):
                labels[nct] = sorted({x for x in got[nct] if x in AXES})
            rec["asked"], rec["answered"] = len(mine), len(mine & set(got))
            rec["text"] = c["text"][:3000]
        calls.append(rec)
    total = add_usage(*(c["usage"] for c in arm["calls"]))
    uncached = cost_usd({**total, "input_tokens": total["input_tokens"] + total["cache_creation_input_tokens"] + total["cache_read_input_tokens"],
                         "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}, llm.MODEL)
    summary = {"subagents": len(task_map), "prefix_chars": len(SYSTEM) + len(text), "wall_s": arm["wall_s"],
               "writes": sum(1 for c in arm["calls"] if c["usage"]["cache_creation_input_tokens"] > 0),
               "readers": sum(1 for c in arm["calls"] if c["label"].startswith("sub:") and c["usage"]["cache_read_input_tokens"] > 0),
               "unanswered": [t["nct"] for t in trials if t["nct"] not in labels],
               "usd": round(arm["cost_usd"], 6), "usd_if_uncached": round(uncached, 6)}
    return {t["nct"]: labels.get(t["nct"], []) for t in trials}, calls, summary


# Independent drug dictionary for the skeptic: drug name fragment -> axis ("none" = targets no axis).
DRUGS = {
    "paxalisib": "pi3k", "gdc-0084": "pi3k", "everolimus": "pi3k", "temsirolimus": "pi3k", "sirolimus": "pi3k",
    "alpelisib": "pi3k", "samotolisib": "pi3k", "buparlisib": "pi3k",
    "palbociclib": "cell_cycle", "ribociclib": "cell_cycle", "abemaciclib": "cell_cycle",
    "dasatinib": "rtk", "crenolanib": "rtk", "avapritinib": "rtk", "imatinib": "rtk", "nimotuzumab": "rtk",
    "erlotinib": "rtk", "gefitinib": "rtk", "afatinib": "rtk", "osimertinib": "rtk", "crizotinib": "rtk",
    "cabozantinib": "rtk", "capmatinib": "rtk",
    "trametinib": "mapk", "dabrafenib": "mapk", "tovorafenib": "mapk", "selumetinib": "mapk", "binimetinib": "mapk",
    "vemurafenib": "mapk", "mirdametinib": "mapk",
    "idasanutlin": "p53", "navtemadlin": "p53", "milademetan": "p53", "brigimadlin": "p53", "eprenetapopt": "p53",
    "onc201": "none", "dordaviprone": "none", "panobinostat": "none", "radiation": "none", "temozolomide": "none",
    "bevacizumab": "none", "nivolumab": "none", "pembrolizumab": "none", "ipilimumab": "none", "car t": "none",
    "vorinostat": "none", "valproic": "none",
}


def dictionary_label(t: dict[str, Any]) -> set[str] | None:
    """Axes implied by known drugs; None when no drug is in the dictionary."""
    text = " ".join([t["title"], *t["drugs"]]).lower()
    found = {axis for drug, axis in DRUGS.items() if drug in text}
    return {a for a in found if a != "none"} if found else None
