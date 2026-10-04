"""Deterministic merges of fan-out results (no model involved, so nothing is lost or reinterpreted).

- ``literature``: every scout note under ``literature/`` -> ``literature.md`` (newest round first).
- ``reviews``: every skeptic review under ``rounds/RNN/reviews/`` -> ``review.json`` / ``review.md``.
  The merged verdict is the strictest one: any ``fail`` fails the review.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from autolab import roles
from autolab.study import Study, read_json, write_json

ORDER = {"fail": 0, "pass_with_caveats": 1, "pass": 2}


def literature(study: Study) -> Path | None:
    d = study.root / "literature"
    lit = study.root / "literature.md"
    shards = sorted(d.glob("*.md"), key=lambda p: (p.name.split("-")[0], p.name), reverse=True) if d.exists() else []
    if not shards:
        return None
    parts = ["# Literature\n\nMerged from the scouts' notes in `literature/` (newest round first).\n"]
    for p in shards:
        rnd, _, who = p.stem.partition("-")
        ang = roles.angle(who)
        parts.append(f"\n## {rnd} · {who}" + (f" ({ang[0]})" if ang else "") + f"\n\n{p.read_text(encoding='utf-8').strip()}\n")
    lit.write_text("".join(parts), encoding="utf-8")
    return lit


def reviews(study: Study, n: int | None = None) -> dict[str, Any] | None:
    r = study.rdir(n)
    files = sorted((r / "reviews").glob("*.json")) if (r / "reviews").exists() else []
    if not files:
        return None
    merged: dict[str, Any] = {"verdict": "pass", "by": {}, "checks": [], "recomputed": {}, "issues": [], "caveats": []}
    md = [f"# Review ({len(files)} skeptics)\n"]
    for f in files:
        who = f.stem
        try:
            rv = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            rv = {"verdict": "fail", "issues": [f"{f.name} is not valid JSON"]}
        v = rv.get("verdict", "fail")
        merged["by"][who] = v
        if ORDER.get(v, 0) < ORDER[merged["verdict"]]:
            merged["verdict"] = v if v in ORDER else "fail"
        for c in rv.get("checks", []):
            if isinstance(c, dict):
                merged["checks"].append({**c, "name": f"{who}: {c.get('name', '')}"})
        if rv.get("recomputed"):
            merged["recomputed"][who] = rv["recomputed"]
        merged["issues"] += [f"{who}: {x}" for x in rv.get("issues", [])]
        merged["caveats"] += [f"{who}: {x}" for x in rv.get("caveats", [])]
        ang = roles.angle(who)
        body = (f.with_suffix(".md").read_text(encoding="utf-8").strip() if f.with_suffix(".md").exists() else "")
        md.append(f"\n## {who}" + (f" ({ang[0]})" if ang else "") + f": {v}\n\n{body}\n")
    write_json(r / "review.json", merged)
    (r / "review.md").write_text("".join(md), encoding="utf-8")
    return merged


def expected_reviewers(study: Study) -> list[str]:
    return [x for x in roles.team(study.config.get("fanout", {})) if x.startswith("skeptic_")]


def missing_reviews(study: Study) -> list[str]:
    have = {p.stem for p in (study.rdir() / "reviews").glob("*.json")} if (study.rdir() / "reviews").exists() else set()
    return [w for w in expected_reviewers(study) if w not in have]


def has_merged_review(study: Study, n: int | None = None) -> bool:
    return read_json(study.rdir(n) / "review.json") is not None
