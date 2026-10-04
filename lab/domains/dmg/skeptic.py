"""Skeptic: re-derives every number from the raw patient and trial files."""

from __future__ import annotations

from typing import Any

from lab.domains.dmg import classify, model as hyp
from lab.domains.dmg.runner import gaps, input_version, trial_points, winner
from lab.provenance import passage_still_matches
from lab.store import Store

MIN_AGREEMENT = 0.8
MIN_RECALL = 0.7


def _check(name: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"check": name, "ok": bool(ok), "detail": detail}


def audit(store: Store, spec: dict[str, Any], result: dict[str, Any]) -> list[dict[str, Any]]:
    p = spec["params"]
    pts = store.raw(result["raw"]["patients"])["points"]
    trials = trial_points(store, result["raw"])
    out = []

    # 1. the measurement ran: every patient is H3K27M of the requested variant, every trial got a label
    want = {"h31": {"H3.1"}, "h33": {"H3.3"}, "all": {"H3.1", "H3.3"}}[p["variant"]]
    wrong = [x["patient"] for x in pts if x["variant"] not in want]
    unlabelled = [t["nct"] for t in trials if "axes" not in t]
    agree = judged = 0
    misses = []
    for t in trials:
        expect = classify.dictionary_label(t)
        if expect is None or (not expect and any(d.lower() not in " ".join(classify.DRUGS) for d in t["drugs"])):
            continue  # dictionary cannot judge this trial
        judged += 1
        ok = bool(expect <= set(t["axes"]) and (expect or not t["axes"]))
        agree += ok
        if not ok:
            misses.append(f"{t['nct']} llm={t['axes']} dict={sorted(expect)}")
    rate = agree / judged if judged else 0.0
    # labels the dictionary cannot back up: reported so a reader can check them by hand
    unverified = [t["nct"] for t in trials if t["axes"] and not (classify.dictionary_label(t) or set()) & set(t["axes"])]
    # recall on positives: of the targets the dictionary is sure about, how many did Haiku find?
    pos = [(t, a) for t in trials for a in (classify.dictionary_label(t) or set())]
    found = sum(1 for t, a in pos if a in t["axes"])
    recall = found / len(pos) if pos else 1.0
    unanswered = result["fanout"].get("unanswered", [])
    out.append(_check("intervention_ran", not wrong and not unlabelled and not unanswered and rate >= MIN_AGREEMENT and recall >= MIN_RECALL,
                      f"{len(pts)} patients, {len(trials)} trials labelled ({len(unanswered)} unanswered); Haiku vs drug dictionary "
                      f"{agree}/{judged} ({rate:.0%}), recall on known targets {found}/{len(pos)} ({recall:.0%})"
                      + (f"; misses: {'; '.join(misses[:3])}" if misses else "")
                      + f"; {len(unverified)} label(s) not backed by the dictionary" + (f": {', '.join(unverified[:5])}" if unverified else "")))

    # 1b. the fan-out actually used the cache: siblings read the shared prefix, at most leader + pre-warm wrote it
    llm = store.raw(result["raw"]["llm"])["points"]
    subs = [c for c in llm if c["label"].startswith("sub:")]
    readers = sum(1 for c in subs if c["usage"]["cache_read_input_tokens"] > 0)
    writes = sum(1 for c in llm if c["usage"]["cache_creation_input_tokens"] > 0)
    parse = [c["label"] for c in subs if "parse_error" in c or c.get("answered", 0) < c.get("asked", 0)]
    ok = readers >= len(subs) - 2 and writes <= 2 and not parse and len(subs) == result["fanout"]["subagents"]
    out.append(_check("cache_used", ok, f"{readers}/{len(subs)} sub-agents read the shared prefix, {writes} write(s)"
                      + (f"; unparsable: {parse}" if parse else "")))

    # 2. controls: enough patients, no patient counted twice, cohorts and trial filter as specified
    ids = [(x["cohort"], x["patient"]) for x in pts]
    dupes = len(ids) - len(set(ids))
    cohorts = {x["cohort"] for x in pts}
    bad_cohort = cohorts - set(hyp.GROUPS[p["group"]])
    bad = []
    if len(pts) < hyp.MIN_PATIENTS:
        bad.append(f"only {len(pts)} patients")
    if dupes:
        bad.append(f"{dupes} duplicate patients")
    if bad_cohort:
        bad.append(f"unexpected cohorts {sorted(bad_cohort)}")
    if p["group"] == "cna" and not all(x["cna"] for x in pts):
        bad.append("copy-number test includes patients without copy-number data")
    out.append(_check("controls", not bad, "; ".join(bad) or f"{len(pts)} unique patients from {sorted(cohorts)}"))

    # 3. leakage: inputs as specified, predictions registered first, sources unchanged, no drift
    leaks = []
    if spec["data_version"] != result["data_version"] or input_version(p) != spec["data_version"]:
        leaks.append("inputs differ from spec")
    if spec["created"] >= result["created"]:
        leaks.append("predictions not pre-registered")
    stale = [h for h in spec["predictions"] if not all(passage_still_matches(store.get(s)) for s in store.get(h)["sources"])]
    if stale:
        leaks.append(f"sources changed: {','.join(stale)}")
    drift = [h for h, pr in spec["predictions"].items() if pr != hyp.predict(store.get(h)["rule"], p)]
    if drift:
        leaks.append(f"predictions drifted: {','.join(drift)}")
    out.append(_check("no_leakage", not leaks, "; ".join(leaks) or "inputs match spec, predictions pre-registered"))

    # 4. claims match raw: recompute prevalence, trial counts, gaps and the winner
    g = gaps(pts, {t["nct"]: t["axes"] for t in trials})
    top, outcome = winner(g)
    diff = [a for a in g if g[a]["gap"] != result["arms"][a]["gap"] or g[a]["altered"] != result["arms"][a]["altered"]]
    ok = not diff and outcome == result["outcome"] and top == result["winner"]
    out.append(_check("claims_match_raw", ok, f"winner {top} ({outcome}) recomputed" + (f"; differs on {diff}" if diff else "")))
    return out
