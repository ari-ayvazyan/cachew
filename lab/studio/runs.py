"""Start research runs on Omnigent and summarise them for the UI.

A run is a study folder (git-ignored, under ``studies/``) holding the request
and team layout (``run.json``), the Omnigent bundle (``agent/``), one record
per model call (``calls/``), the briefs and every artifact. The run itself is
an Omnigent session: ``omnigent run agent/ -p <question>`` starts the PI, and
Omnigent runs the sub-agents. Everything shown in the UI is read back from
the folder, so a run survives restarts of this server.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from cachew.harness import forget_key, read_json, store_key, write_json
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


def check_key(key: str, timeout: float = 10.0) -> None:
    """Ask Anthropic whether ``key`` works before a run is started on it (lists models, costs nothing)."""
    req = urllib.request.Request("https://api.anthropic.com/v1/models?limit=1",
                                 headers={"x-api-key": key, "anthropic-version": "2023-06-01"})
    try:
        with urllib.request.urlopen(req, timeout=timeout):
            return
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise ValueError("Anthropic rejected this API key") from None
        raise ValueError(f"Anthropic could not check the key (HTTP {e.code}); try again") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ValueError("Could not reach Anthropic to check the API key") from None


def start(question: str, context: str = "", model: str = "claude-opus-5-5", effort: str = "low",
          width: int = 3, budget_usd: float = 3.0, approval: bool = False, api_key: str = "") -> Path:
    if model not in PRICES:
        raise ValueError(f"unknown model {model!r}")
    if model == "claude-haiku-4-5":
        effort = ""  # Haiku 4.5 takes no effort setting
    study = new_study_dir(question)
    run = team.make_study(study, question.strip(), context.strip(), model, effort, max(1, width), budget_usd,
                          approval=approval, own_key=bool(api_key))
    run["kind"] = KIND
    write_json(study / "run.json", run)
    if api_key:
        store_key(study, api_key)
    log = (study / "omnigent.log").open("w", encoding="utf-8")
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    proc = subprocess.Popen([omnigent_exe(), "run", str(study / "agent"), "-p", question.strip()[:2000]],
                            cwd=study, env=omnigent_env(), stdout=log, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, creationflags=flags)
    (study / "launcher.pid").write_text(str(proc.pid))
    return study


LIVE = ("starting", "running", "awaiting_approval")


def stop(study: Path) -> None:
    """Ask the PI to stop dispatching; members already running finish their call."""
    (study / "STOP").write_text(time.strftime("%Y-%m-%d %H:%M:%S"))
    run = read_json(study / "run.json")
    if run and run.get("status") in LIVE:
        run.update(status="stopped", phase="stopped by user")
        write_json(study / "run.json", run)
    forget_key(study)


def approve(study: Path, decision: str, test: str = "", note: str = "") -> dict[str, Any]:
    """Record the scientist's decision on the experiment; the waiting PI picks it up within a second."""
    run = read_json(study / "run.json", {}) or {}
    if run.get("status") != "awaiting_approval":
        raise ValueError("this run is not waiting for an approval")
    if decision not in ("approve", "reject"):
        raise ValueError("decision must be approve or reject")
    sel = read_json(study / "selection.json", {}) or {}
    chosen = str(sel.get("chosen", "")) if isinstance(sel, dict) else ""
    rec = {"decision": decision, "test": test.strip() or chosen, "note": note.strip()[:2000],
           "changed": bool(test.strip()) and test.strip() != chosen, "at": time.time()}
    write_json(study / "approval.json", rec)
    return rec


def list_runs() -> list[dict[str, Any]]:
    out = []
    for p in sorted(STUDIES.glob("*/run.json"), reverse=True):
        run = read_json(p, {}) or {}
        if run.get("kind") != KIND:
            continue
        s = savings(p.parent)
        out.append({"id": p.parent.name, "question": run.get("question", ""), "status": run.get("status"),
                    "model": run.get("model"), "created": run.get("created"), "saved_usd": s["saved_usd"],
                    "usd": s["usd"], "agents": len(run.get("agents", {}))})
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


_VERDICT = re.compile(r"verdict\s*:?\s*\**\s*(PASS|CONCERNS|FAIL)\b", re.I)
_LABEL = re.compile(r"\s*\(?([A-Z]\d?|\d+)[:.)]\s")


def _clip(text: str, limit: int = 1200) -> str:
    """Shorten at a line or sentence boundary, never mid-word."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind("\n"), cut.rfind(". "))
    return (cut[:end + 1] if end > limit // 2 else cut[:cut.rfind(" ")]).rstrip() + " …"


def _section_text(md: str, *words: str) -> str:
    """The body under the first heading that mentions any of ``words``."""
    lines = md.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("#") and any(w in line.lower() for w in words):
            body = []
            for nxt in lines[i + 1:]:
                if nxt.startswith("#"):
                    break
                body.append(nxt)
            return _clip("\n".join(body).strip())
    return ""


def _decision_from_verdict(md: str) -> dict[str, Any] | None:
    """Best-effort decision for runs whose judge wrote no JSON block: verdict line, posterior table, next step."""
    if not md.strip():
        return None
    first = md.strip().splitlines()[0]
    answer = re.sub(r"^\W*verdict\W*", "", first, flags=re.I).strip()
    posterior = []
    for line in md.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")] if line.strip().startswith("|") else []
        hid = re.search(r"\bH\d+\b", cells[0]) if cells else None
        nums = [float(m.group(1)) / (100 if m.group(2) else 1)
                for c in cells[1:] if (m := re.fullmatch(r"\**~?(\d*\.?\d+)\s*(%?)\**", c))]
        if hid and nums and hid.group(0) not in {p["id"] for p in posterior}:
            posterior.append({"id": hid.group(0), "p": nums[-1]})
    return {"answer": answer, "status": "unresolved" if "unresolved" in answer.lower() else None,
            "posterior": posterior, "next_experiment": _section_text(md, "next experiment"),
            "validation_needed": _section_text(md, "validation"), "source": "verdict.md"}


def _selection(raw: Any) -> dict[str, Any] | None:
    """Normalise selection.json: tests as text, and which one was chosen (-1 when it cannot be matched)."""
    if not isinstance(raw, dict):
        return None
    tests = []
    for t in raw.get("tests") or []:
        if isinstance(t, dict):
            t = t.get("test") or t.get("name") or t.get("description") or json.dumps(t)
        tests.append(str(t))
    chosen = str(raw.get("chosen") or "").strip()
    idx = next((i for i, t in enumerate(tests) if t == chosen), -1)
    if idx < 0 and chosen:
        for i, t in enumerate(tests):
            m = _LABEL.match(t)
            if t.startswith(chosen) or chosen.startswith(t[:40]) or (m and re.match(rf"\(?{re.escape(m.group(1))}\b", chosen)):
                idx = i
                break
    return {"tests": tests, "chosen": chosen, "chosen_index": idx, "why": str(raw.get("why") or "")}


def science(study: Path, run: dict[str, Any]) -> dict[str, Any]:
    """The research content the UI shows as structure: hypotheses, the test choice, reviews, the decision."""
    def items(name: str) -> list[dict[str, Any]]:
        raw = read_json(study / name)
        return [x for x in raw if isinstance(x, dict) and x.get("id")] if isinstance(raw, list) else []

    decision = read_json(study / "decision.json")
    verdict = study / "verdict.md"
    if not isinstance(decision, dict):
        decision = _decision_from_verdict(verdict.read_text(encoding="utf-8")) if verdict.exists() else None
    reviews = []
    for label, a in run.get("agents", {}).items():
        if a.get("role") != "skeptic":
            continue
        out = study / a.get("out", "")
        m = _VERDICT.search(out.read_text(encoding="utf-8")[:600]) if out.is_file() else None
        reviews.append({"label": label, "name": a["name"], "focus": a["focus"], "out": a.get("out"),
                        "verdict": m.group(1).upper() if m else None})
    approval = read_json(study / "approval.json")
    return {"candidates": items("candidates.json"), "predictions": items("predictions.json"),
            "selection": _selection(read_json(study / "selection.json")), "decision": decision,
            "reviews": reviews, "approval": approval if isinstance(approval, dict) else None}


def acceleration(run: dict[str, Any], recs: dict[str, dict[str, Any]], sav: dict[str, Any], now: float) -> dict[str, Any]:
    """How much the team compresses: agent-time run in parallel per fan-out, and spend vs. no cache."""
    timed = {k: r for k, r in recs.items()
             if r.get("started") is not None and r.get("finished") is not None and r.get("kind") != "prewarm"}
    fanouts = []
    for step in run.get("steps", []):
        if not step.get("fan"):
            continue
        rs = [timed[k] for k, a in run.get("agents", {}).items() if a.get("step") == step["id"] and k in timed]
        if not rs:
            continue
        serial = sum(r["finished"] - r["started"] for r in rs)
        wall = max(r["finished"] for r in rs) - min(r["started"] for r in rs)
        fanouts.append({"step": step["id"], "agents": len(rs), "serial_s": round(serial, 1), "wall_s": round(wall, 1),
                        "speedup": round(serial / wall, 2) if wall > 0 else None})
    serial = sum(f["serial_s"] for f in fanouts)
    wall = sum(f["wall_s"] for f in fanouts)
    agent_s = sum(r["finished"] - r["started"] for r in timed.values())
    starts = [s["started"] for s in run.get("steps", []) if s.get("started") is not None]
    end = run.get("finished") or (now if run.get("status") in LIVE else max(
        [r["finished"] for r in timed.values()] or [now]))
    return {"fanouts": fanouts, "serial_s": round(serial, 1), "wall_s": round(wall, 1),
            "speedup": round(serial / wall, 2) if wall > 0 else None, "agent_s": round(agent_s, 1),
            "run_s": round(end - min(starts), 1) if starts else 0.0,
            "cost_ratio": round(sav["usd_uncached"] / sav["usd"], 2) if sav["usd"] > 0 else None}


def authors(run: dict[str, Any]) -> dict[str, str]:
    """Which agent wrote each artifact, so every entry of the research record has an owner."""
    out = {a["out"]: label for label, a in run.get("agents", {}).items() if a.get("out")}
    out.update({"plan.md": "pi", "candidates.json": "lead", "predictions.json": "lead", "selection.json": "experimenter",
                "experiment/analysis.py": "experimenter", "decision.json": "judge", "approval.json": "scientist"})
    return out


def view(study: Path) -> dict[str, Any]:
    """Everything the UI shows for one run."""
    run = read_json(study / "run.json", {}) or {}
    if run.get("status") in team.TERMINAL:
        forget_key(study)  # the PI normally does this; covers runs whose process died
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
    bundle = sorted(str(p.relative_to(study)).replace("\\", "/") for p in (study / "agent").rglob("config.yaml"))
    sav, now = savings(study), time.time()
    return {"id": study.name, "run": {k: v for k, v in run.items() if k != "agents"}, "agents": agents,
            "prewarms": {k: v for k, v in recs.items() if v.get("kind") == "prewarm"},
            "stages": stages(study, run, recs), "savings": sav, "files": files, "bundle": bundle,
            "authors": authors(run), "science": science(study, run), "acceleration": acceleration(run, recs, sav, now),
            "spent_usd": round(team.spent(study), 6), "now": now}


READABLE = (".md", ".json", ".py", ".txt", ".yaml")


def read_file(study: Path, rel: str) -> str:
    """A research artifact of the run; never a hidden file (the run's key) or anything outside the study."""
    path = (study / rel).resolve()
    inside = study.resolve() in path.parents
    if not inside or path.suffix not in READABLE or any(p.startswith(".") for p in path.relative_to(study.resolve()).parts) \
            or not path.is_file():
        raise FileNotFoundError(rel)
    return path.read_text(encoding="utf-8", errors="replace")
