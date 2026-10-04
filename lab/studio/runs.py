"""Start research runs on Omnigent and summarise them for the UI.

A run is a study folder (git-ignored, under ``studies/``) holding the request
and team layout (``run.json``), the Omnigent bundle (``agent/``), one record
per model call (``calls/``), the briefs and every artifact. The run itself is
an Omnigent session: ``omnigent run agent/ -p <question>`` starts the PI, and
Omnigent runs the sub-agents. Everything shown in the UI is read back from
the folder, so a run survives restarts of this server.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from cachew.harness import read_json, write_json
from cachew.pricing import PRICES
from lab.studio import team

REPO = Path(__file__).resolve().parents[2]
STUDIES = REPO / "studies"
KIND = "studio"  # marks studies made here, next to the lab's own studies


def omnigent_exe() -> str:
    scripts = Path(sys.executable).parent
    for name in ("omnigent.exe", "omnigent"):
        if (scripts / name).exists():
            return str(scripts / name)
    return "omnigent"


def omnigent_env() -> dict[str, str]:
    # Omnigent's host daemon prints non-ASCII marks; without UTF-8 mode it crashes on Windows consoles.
    return {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}


def new_study_dir(question: str) -> Path:
    STUDIES.mkdir(exist_ok=True)
    nums = [int(m.group(1)) for p in STUDIES.iterdir() if (m := re.match(r"(\d{3})-", p.name))]
    slug = "-".join(re.findall(r"[a-z0-9]+", question.lower())[:6])[:48] or "research"
    return STUDIES / f"{max(nums, default=0) + 1:03d}-{slug}"


def start(question: str, context: str = "", model: str = "claude-opus-5-5", effort: str = "low",
          width: int = 3, budget_usd: float = 3.0) -> Path:
    if model not in PRICES:
        raise ValueError(f"unknown model {model!r}")
    if model == "claude-haiku-4-5":
        effort = ""  # Haiku 4.5 takes no effort setting
    study = new_study_dir(question)
    run = team.make_study(study, question.strip(), context.strip(), model, effort, max(1, min(6, width)), budget_usd)
    run["kind"] = KIND
    write_json(study / "run.json", run)
    log = (study / "omnigent.log").open("w", encoding="utf-8")
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    proc = subprocess.Popen([omnigent_exe(), "run", str(study / "agent"), "-p", question.strip()[:2000]],
                            cwd=study, env=omnigent_env(), stdout=log, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, creationflags=flags)
    (study / "launcher.pid").write_text(str(proc.pid))
    return study


def stop(study: Path) -> None:
    """Ask the PI to stop dispatching; members already running finish their call."""
    (study / "STOP").write_text(time.strftime("%Y-%m-%d %H:%M:%S"))
    run = read_json(study / "run.json")
    if run and run.get("status") in ("starting", "running"):
        run.update(status="stopped", phase="stopped by user")
        write_json(study / "run.json", run)


def list_runs() -> list[dict[str, Any]]:
    out = []
    for p in sorted(STUDIES.glob("*/run.json"), reverse=True):
        run = read_json(p, {}) or {}
        if run.get("kind") != KIND:
            continue
        s = savings(p.parent)
        out.append({"id": p.parent.name, "question": run.get("question", ""), "status": run.get("status"),
                    "model": run.get("model"), "created": run.get("created"), "saved_usd": s["saved_usd"],
                    "usd": s["usd"]})
    return out


def calls(study: Path) -> dict[str, dict[str, Any]]:
    return {p.stem: read_json(p, {}) or {} for p in sorted((study / "calls").glob("*.json"))}


def savings(study: Path) -> dict[str, Any]:
    """Real spend vs. the same requests with no cache (pre-warms would not exist then)."""
    recs = [c for c in calls(study).values() if c.get("usage")]
    usd = sum(c.get("usd", 0.0) for c in recs)
    uncached = sum(c.get("usd_uncached", 0.0) for c in recs)
    tok = {k: sum(c["usage"].get(k, 0) for c in recs) for k in team_usage_keys()}
    return {"usd": round(usd, 6), "usd_uncached": round(uncached, 6), "saved_usd": round(uncached - usd, 6),
            "saved_pct": round(100 * (uncached - usd) / uncached, 1) if uncached else 0.0, "tokens": tok,
            "calls": len(recs)}


def team_usage_keys() -> tuple[str, ...]:
    return ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")


def stages(study: Path, run: dict[str, Any], recs: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per cached brief: who shared it, how often it was written vs read, and what that saved."""
    rows = []
    for stage, b in sorted(run.get("briefs", {}).items()):
        members = [r for r in recs.values()
                   if r.get("brief") == b["id"] and r.get("usage") and r.get("kind") != "prewarm"]
        warm = recs.get(b.get("prewarm", ""), {})
        usd = sum(r.get("usd", 0) for r in members) + warm.get("usd", 0)
        unc = sum(r.get("usd_uncached", 0) for r in members)
        sha_ok = all(r.get("system_sha") == b.get("system_sha") for r in members if r.get("system_sha"))
        rows.append({"stage": stage, "brief": b["id"], "chars": b["chars"], "prefix_tokens": b.get("prefix_tokens", 0),
                     "agents": [r["label"] for r in members], "prewarm": warm.get("status"),
                     "writes": sum(1 for r in [warm, *members] if (r.get("usage") or {}).get("cache_creation_input_tokens")),
                     "reads": sum(1 for r in members if r["usage"].get("cache_read_input_tokens")),
                     "cache_read_tokens": sum(r["usage"].get("cache_read_input_tokens", 0) for r in members),
                     "usd": round(usd, 6), "usd_uncached": round(unc, 6), "saved_usd": round(unc - usd, 6),
                     "prefix_match": sha_ok})
    return rows


def view(study: Path) -> dict[str, Any]:
    """Everything the UI shows for one run."""
    run = read_json(study / "run.json", {}) or {}
    recs = calls(study)
    agents = {}
    for label, a in run.get("agents", {}).items():
        r = recs.get("pi-plan" if label == "pi" else label, {})
        status = r.get("status", "idle")
        if label == "pi" and run.get("status") == "running" and status == "done":
            status = "waiting"
        agents[label] = {**a, "status": status, "usage": r.get("usage"), "usd": r.get("usd"),
                         "usd_uncached": r.get("usd_uncached"), "started": r.get("started"), "finished": r.get("finished"),
                         "error": r.get("error"), "session": r.get("session"), "brief": r.get("brief")}
    files = sorted(str(p.relative_to(study)).replace("\\", "/") for p in study.rglob("*")
                   if p.is_file() and p.parts[len(study.parts)] not in ("agent", "calls")
                   and p.suffix in (".md", ".json", ".py", ".txt") and p.name not in ("run.json",))
    return {"id": study.name, "run": {k: v for k, v in run.items() if k != "agents"}, "agents": agents,
            "prewarms": {k: v for k, v in recs.items() if v.get("kind") == "prewarm"},
            "stages": stages(study, run, recs), "savings": savings(study), "files": files,
            "spent_usd": round(team.spent(study), 6), "now": time.time()}


def read_file(study: Path, rel: str) -> str:
    path = (study / rel).resolve()
    if study.resolve() not in path.parents or not path.is_file():
        raise FileNotFoundError(rel)
    return path.read_text(encoding="utf-8", errors="replace")
