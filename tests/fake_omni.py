"""Stand-in for `omni run <bundle> --no-session -p <prompt>` used by tests/test_autolab.py.

Plays every role through the same role-checked Door the real agents use, so
the tests exercise the permission rules too. Behaviour knobs via env:
FAKE_OMNI_BAD_PLAN=1 skips predictions.json on the first plan pass.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from autolab.mcp_server import Door  # noqa: E402

RUN_PY = '''import argparse, json, os, random
ap = argparse.ArgumentParser(); ap.add_argument("--out"); a = ap.parse_args()
runs = [{"cond": c, "seed": s, "acc": (0.9 if c == "B" else 0.8) + random.Random(s).random() * 0.01}
        for c in "AB" for s in range(3)]
mean = lambda c: sum(r["acc"] for r in runs if r["cond"] == c) / 3
print("training", flush=True)
json.dump({"summary": {"A": mean("A"), "B": mean("B")}, "runs": runs, "config": {}}, open(os.path.join(a.out, "results.json"), "w"))
'''


def main() -> None:
    if delay := float(os.environ.get("FAKE_OMNI_SLEEP", "0")):
        import time
        time.sleep(delay)
    args = sys.argv[1:]
    prompt = args[args.index("-p") + 1]
    root = Path(os.environ["AUTOLAB_STUDY"])
    meta = json.loads((root / "study.json").read_text())
    R = f"R{meta['round']:02d}"
    phase = "analyze" if "PHASE analyze" in prompt else "plan"
    door = lambda role: Door(root, role, phase, R)  # noqa: E731
    log = root / "fake_omni_calls.jsonl"
    with open(log, "a") as f:
        f.write(json.dumps({"phase": phase, "round": R, "fix": "PROBLEMS" in prompt}) + "\n")
    if phase == "plan":
        hyps = [{"id": "H1", "statement": "B beats A"}, {"id": "H2", "statement": "A beats B"},
                {"id": "H0", "statement": "none", "catch_all": True}]
        for i in (1, 2, 3):  # parallel scouts and proposing theorists, each to its own file
            door(f"scout_{i}").write(f"literature/{R}-scout_{i}.md", f"# notes from scout {i}\n")
            door(f"theorist_{i}").write(f"rounds/{R}/proposals/theorist_{i}.json", json.dumps({"hypotheses": hyps[:2], "experiments": []}))
        door("theorist").write(f"rounds/{R}/candidates.json", json.dumps({"hypotheses": hyps, "experiments": [
            {"id": "E1", "description": "compare A vs B", "discriminates": ["H1", "H2"]},
            {"id": "E2", "description": "other", "discriminates": ["H1"]}]}))
        door("pi").write(f"rounds/{R}/selection.json", json.dumps({"chosen": "E1", "why": "separates H1/H2", "rejected": []}))
        door("experimenter").write(f"rounds/{R}/plan.md", "# plan\n")
        door("experimenter").write(f"rounds/{R}/experiment/run.py", RUN_PY)
        first = not (root / "rounds" / R / "logs").exists() or len(list((root / "rounds" / R / "logs").glob("plan-*.log"))) <= 1
        if not (os.environ.get("FAKE_OMNI_BAD_PLAN") and first and "PROBLEMS" not in prompt):
            door("theorist").write(f"rounds/{R}/predictions.json", json.dumps({"predictions": [
                {"hypothesis": h["id"], "prediction": "...", "falsified_if": "..."} for h in hyps]}))
        print("plan ready")
    else:
        for i in (1, 2, 3):  # parallel skeptics; skeptic_3 flags a caveat
            sk = door(f"skeptic_{i}")
            sk.run_analysis("import json; print(json.load(open('output/results.json'))['summary'])")
            v = "pass_with_caveats" if i == 3 else "pass"
            sk.write(f"rounds/{R}/reviews/skeptic_{i}.json", json.dumps({"verdict": v, "checks": [{"name": "x", "ok": True, "detail": ""}],
                                                                     "issues": [], "caveats": ["small n"] if i == 3 else []}))
            sk.write(f"rounds/{R}/reviews/skeptic_{i}.md", f"review {i}")
        door("judge").write(f"rounds/{R}/verdict.json", json.dumps({
            "updates": [{"id": "H1", "credence": 0.8, "status": "supported", "evidence": "..."},
                        {"id": "H2", "credence": 0.05, "status": "refuted", "evidence": "..."},
                        {"id": "H0", "credence": 0.15, "status": "alive", "evidence": "..."}],
            "summary": "B beats A", "decision": os.environ.get("FAKE_OMNI_DECISION", "continue"),
            "next_direction": "replicate"}))
        door("judge").write("findings.md", "B > A\n")
        print("analysis done")


if __name__ == "__main__":
    main()
