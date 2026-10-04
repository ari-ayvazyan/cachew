"""Question, competing hypotheses, test grid and predictions for the DMG domain."""

from __future__ import annotations

from typing import Any

from lab.domains.dmg import data

QUESTION = "Which targetable subgroup of H3K27M diffuse midline glioma is common but has the fewest matched trials?"
AXES = list(data.AXES)
OUTCOMES = AXES + ["tie"]
CONF = 0.75
TIE = 0.9  # runner-up within 90% of the top gap = no clear winner

BG = "lab/domains/dmg/background.md"
SOURCES = {
    "p53": (BG, 6, 9),
    "acvr1": (BG, 11, 13),
    "pi3k": (BG, 15, 17),
    "rtk": (BG, 19, 21),
    "variant": (BG, 23, 24),
    "measurement": (BG, 26, 27),
}

HYPOTHESES = [
    {"key": "p53", "claim": "p53 loss is the biggest gap everywhere", "sources": ["p53"], "rule": {"type": "const", "axis": "p53"}},
    {"key": "variant", "claim": "gap depends on H3 variant: ACVR1 in H3.1, p53 in H3.3", "sources": ["variant", "acvr1", "p53"],
     "rule": {"type": "by_variant", "h31": "acvr1", "h33": "p53"}},
    {"key": "pi3k", "claim": "PI3K pathway is the biggest gap", "sources": ["pi3k"], "rule": {"type": "const", "axis": "pi3k"}},
    {"key": "rtk", "claim": "RTK (PDGFRA etc.) is the biggest gap once copy number is seen", "sources": ["rtk", "measurement"],
     "rule": {"type": "by_cna", "cna": "rtk", "no_cna": "p53"}},
    {"key": "scope", "claim": "artifact: wider HGG trials already target the DMG gap", "sources": ["measurement", "p53", "acvr1"],
     "rule": {"type": "scope", "h31": "acvr1", "h33": "p53"}},
    {"key": "none", "claim": "none of these: no consistent winner", "sources": ["measurement"], "rule": {"type": "none"}},
]

# cohort groups: which cBioPortal cohorts each test pools
GROUPS = {"dkfz": ["dkfz"], "others": ["mskcc", "pipseq", "cptac", "mai", "tcga"],
          "cna": ["mskcc", "cptac", "mai", "tcga"], "pooled": list(data.COHORTS)}
GRID = {"group": list(GROUPS), "variant": ["all", "h33", "h31"], "trials": ["open", "since2015", "hgg_open"]}
MIN_PATIENTS = 8


def predict(rule: dict[str, Any], params: dict[str, Any]) -> dict[str, float]:
    t = rule["type"]
    if t == "none":
        return {o: 1 / len(OUTCOMES) for o in OUTCOMES}
    if t == "const":
        axis = rule["axis"]
    elif t == "by_variant":
        axis = rule["h31"] if params["variant"] == "h31" else rule["h33"]
    elif t == "by_cna":
        axis = rule["cna"] if params["group"] == "cna" else rule["no_cna"]
    elif t == "by_trials":
        axis = rule.get(params["trials"], rule["open"])
    elif t == "scope":
        axis = rule["h31"] if params["variant"] == "h31" else rule["h33"]
        if params["trials"] == "hgg_open":  # drugs for that axis exist in wider trials: anything but it
            low = (1 - CONF) / (len(OUTCOMES) - 1)
            return {o: low if o == axis else (1 - low) / (len(OUTCOMES) - 1) for o in OUTCOMES}
    else:
        raise ValueError(t)
    rest = (1 - CONF) / (len(OUTCOMES) - 1)
    return {o: CONF if o == axis else rest for o in OUTCOMES}


def patients(params: dict[str, Any]) -> list[dict[str, Any]]:
    out = [p for c in GROUPS[params["group"]] for p in data.cohort(c)]
    if params["variant"] != "all":
        out = [p for p in out if p["variant"] == {"h31": "H3.1", "h33": "H3.3"}[params["variant"]]]
    return out


def trial_set(params: dict[str, Any]) -> list[dict[str, Any]]:
    if params["trials"] == "hgg_open":
        return data.trials("hgg")  # all open high-grade glioma trials, adult and paediatric
    ts = data.trials("dmg")
    if params["trials"] == "open":
        return [t for t in ts if t["status"] in data.OPEN]
    return [t for t in ts if t["start"][:4] >= "2015"]


def feasible(params: dict[str, Any]) -> tuple[bool, str]:
    n = len(patients(params))
    return (n >= MIN_PATIENTS, f"{n} patients" + ("" if n >= MIN_PATIENTS else f" < {MIN_PATIENTS}"))


def estimate_usd(params: dict[str, Any]) -> float:
    """Haiku cost of this test's cached fan-out over its trials."""
    from lab.domains.dmg.classify import estimate_usd as fanout_usd

    return fanout_usd(trial_set(params))
