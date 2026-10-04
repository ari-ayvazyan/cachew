"""Which genotype-defined axis does each trial target? Haiku labels, a dictionary audits.

Haiku is the measuring instrument here (it reads titles, drugs and eligibility
text). The skeptic does not trust it: it re-labels every trial whose drugs are
in ``DRUGS`` and requires agreement.
"""

from __future__ import annotations

import json
from typing import Any

from lab.domains.dmg import llm
from lab.domains.dmg.data import AXES

BATCH = 20
SYSTEM = (
    "You label clinical trials for H3K27M diffuse midline glioma. For each trial, list which of these axes it "
    "TARGETS: " + ", ".join(AXES) + ". An axis counts only if a drug in the trial inhibits/restores that pathway "
    "(p53: MDM2/MDM4/PPM1D inhibitors or p53 reactivators; acvr1: ACVR1/ALK2 inhibitors; pi3k: PI3K/AKT/mTOR "
    "inhibitors; rtk: PDGFRA/EGFR/MET/FGFR/KIT inhibitors; cell_cycle: CDK4/6 inhibitors; mapk: BRAF/MEK inhibitors) "
    "or eligibility requires an alteration in it. Radiation, chemotherapy, ONC201/dordaviprone, HDAC inhibitors, "
    "immunotherapy, CAR-T, vaccines, VEGF antibodies and devices target no axis: return []. "
    "Use only targets you are sure of; if a drug is a code name or its target is unknown to you, it targets no axis. "
    "Check every arm: one targeted drug in any arm is enough (e.g. everolimus in a multi-arm trial counts as pi3k). "
    "Reply with only a JSON object mapping NCT id to a list of axes."
)

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


def classify(trials: list[dict[str, Any]]) -> tuple[dict[str, list[str]], list[dict[str, Any]]]:
    """Returns ({nct: axes}, llm call records)."""
    labels: dict[str, list[str]] = {}
    calls = []
    for i in range(0, len(trials), BATCH):
        chunk = [{k: t[k] for k in ("nct", "title", "drugs", "eligibility_genes")} for t in trials[i:i + BATCH]]
        r = llm.ask(SYSTEM, json.dumps(chunk, ensure_ascii=False))
        got = llm.parse_json(r["text"])
        for t in chunk:
            labels[t["nct"]] = sorted(a for a in got.get(t["nct"], []) if a in AXES)
        calls.append({"label": f"classify {i // BATCH + 1}", "trials": [t["nct"] for t in chunk],
                      "usage": r["usage"], "usd": round(r["usd"], 6), "cached": r["cached"]})
    return labels, calls


def dictionary_label(t: dict[str, Any]) -> set[str] | None:
    """Axes implied by known drugs; None when no drug is in the dictionary."""
    text = " ".join([t["title"], *t["drugs"]]).lower()
    found = {axis for drug, axis in DRUGS.items() if drug in text}
    return {a for a in found if a != "none"} if found else None
