"""REPORT.md: assembled from the artifacts only, so every claim points at a file."""

from __future__ import annotations

from pathlib import Path

from autolab.study import Study, read_json, round_name


def write(study: Study) -> Path:
    m = study.meta
    lines = [f"# {m['question']}", "",
             f"Status: **{m['status']}** after {m['round']} round(s). Generated from the study artifacts; "
             f"every number below is traceable to a file under `rounds/`.", ""]
    if m.get("context"):
        lines += ["## Context", "", m["context"], ""]
    lines += ["## Hypotheses", "", "| id | status | credence by round | statement |", "|---|---|---|---|"]
    for h in study.hypotheses():
        traj = " → ".join(f"{v:.2f}" for _, v in sorted(h["credence"].items())) or "—"
        lines.append(f"| {h['id']} | {h['status']} | {traj} | {h['statement']} |")
    lines += ["", "## Rounds", ""]
    for n in study.rounds():
        r, R = study.rdir(n), round_name(n)
        sel = read_json(r / "selection.json", {})
        cand = read_json(r / "candidates.json", {})
        exp = next((e for e in cand.get("experiments", []) if e.get("id") == sel.get("chosen")), {})
        run = read_json(r / "run.json")
        rev = read_json(r / "review.json", {})
        ver = read_json(r / "verdict.json", {})
        appr = read_json(r / "approval.json")
        lines += [f"### {R}: {exp.get('description', sel.get('chosen', '(not planned)'))}", ""]
        if sel.get("why"):
            lines += [f"- **Why this experiment:** {sel['why']}"]
        lines += [f"- **Approved:** {'by ' + appr['approved_by'] + ' at ' + appr['at'] if appr else 'no'}"]
        if run:
            lines += [f"- **Run:** exit {run['exit_code']}, {run['wall_seconds']}s, sandbox {run['sandbox']}, "
                      f"results {run['results_json']} (`rounds/{R}/run.json`)"]
        if rev:
            lines += [f"- **Skeptic:** {rev.get('verdict')}; issues: {'; '.join(rev.get('issues', [])) or 'none'} "
                      f"(`rounds/{R}/review.md`)"]
        if ver:
            lines += [f"- **Judge:** {ver.get('summary', '')} Decision: {ver.get('decision')}. "
                      f"(`rounds/{R}/verdict.json`)"]
            if ver.get("surprise"):
                lines += [f"- **Surprise:** {ver['surprise']}"]
        lines.append("")
    findings = study.root / "findings.md"
    if findings.exists():
        lines += ["## Findings (judge)", "", findings.read_text(encoding="utf-8").strip(), ""]
    lit = study.root / "literature.md"
    if lit.exists():
        lines += ["## Literature", "", f"See `literature.md` (scout's notes, {len(lit.read_text().splitlines())} lines).", ""]
    out = study.root / "REPORT.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    return out
