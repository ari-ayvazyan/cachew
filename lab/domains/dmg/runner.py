"""Runner: prevalence of each axis in the chosen H3K27M patients vs. trials that target it.

gap(axis) = prevalence / (1 + matched trials). The winner is the largest gap;
"tie" when the runner-up is within ``TIE`` of it.
"""

from __future__ import annotations

from typing import Any

from lab.domains.dmg import classify, model as hyp
from lab.domains.dmg.data import version
from lab.store import Store


PART = 100  # trials per raw file, keeps each file under the store's size cap


def trial_points(store: Store, raw: dict[str, str]) -> list[dict[str, Any]]:
    return [t for part, rel in sorted(raw.items()) if part.startswith("trials_") for t in store.raw(rel)["points"]]


def inputs(params: dict[str, Any]) -> dict[str, Any]:
    return {"patients": hyp.patients(params), "trials": hyp.trial_set(params)}


def input_version(params: dict[str, Any], seed: int = 0) -> str:
    return version(inputs(params))


def gaps(patients: list[dict[str, Any]], labels: dict[str, list[str]]) -> dict[str, dict[str, Any]]:
    n = len(patients)
    out = {}
    for axis in hyp.AXES:
        altered = sum(1 for p in patients if axis in p["axes"])
        matched = sorted(t for t, axes in labels.items() if axis in axes)
        out[axis] = {"altered": altered, "n": n, "prevalence": round(altered / n, 4), "trials": len(matched),
                     "matched": matched, "gap": round(altered / n / (1 + len(matched)), 4)}
    return out


def winner(g: dict[str, dict[str, Any]]) -> tuple[str, str]:
    ranked = sorted(g, key=lambda a: -g[a]["gap"])
    top, second = ranked[0], ranked[1]
    tie = g[second]["gap"] >= hyp.TIE * g[top]["gap"]
    return top, ("tie" if tie else top)


def execute(store: Store, run_id: str, spec: dict[str, Any]) -> dict[str, Any]:
    data = inputs(spec["params"])
    labels, calls, fanout = classify.classify(data["trials"])
    g = gaps(data["patients"], labels)
    top, outcome = winner(g)
    files = {
        "patients": store.put_raw(run_id, "patients", {"points": [{"label": p["patient"], **p} for p in data["patients"]]}),
        **{f"trials_{i // PART + 1}": store.put_raw(run_id, f"trials_{i // PART + 1}", {"points": [
            {"label": t["nct"], **t, "eligibility_genes": t["eligibility_genes"][:100], "axes": labels[t["nct"]]}
            for t in data["trials"][i:i + PART]]}) for i in range(0, len(data["trials"]), PART)},
        "llm": store.put_raw(run_id, "llm", {"model": "claude-haiku-4-5", "points": calls, "usd": fanout["usd"],
                                             "wall_s": fanout["wall_s"]}),
    }
    return {
        "spec": spec["id"],
        "backend": "cBioPortal + ClinicalTrials.gov (cached data), trial labels by a cached claude-haiku-4-5 fan-out",
        "data_version": version(data),
        "arms": g,
        "winner": top,
        "outcome": outcome,
        "llm_usd": fanout["usd"],
        "fanout": fanout,
        "raw": files,
    }
