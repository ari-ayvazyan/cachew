"""Turn a round's trace (``rounds/RNN/trace.jsonl``) into a live activity model for the UI.

The trace comes from ``autolab/trace.py`` (an Omnigent function policy on every
agent). Omnigent applies the root agent's policies to its children too, so
every sub-agent event is also recorded once under ``pi``; those copies are
dropped here (same phase, tool, payload and session usage within a few
seconds as the sub-agent's own record).

Model, as served by ``GET /api/studies/<name>/activity``::

    agents  {role: {state, detail, task, report, calls, tools, tokens, cost_usd, last_t}}
    edges   [{from, to, kind, label, t}]   dispatch | report | write | read | denied | web | exec | run | start | approve
    files   [{path, writers, readers, t}]
    spans   [{agent, start, end, kind}]   when each agent was working
    feed    [{t, agent, kind, text}]      plain-language event stream
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from autolab import roles
from autolab.study import Study, read_json, round_name

GROUPS = ["scout", "theorist", "experimenter", "skeptic", "judge"]  # graph columns, in pipeline order
SUBS = {"theorist": "merges + predicts", "experimenter": "design + code", "judge": "verdict",
        "scout": "literature", "skeptic": "audit"}


def layout(team: list[str]) -> list[dict[str, Any]]:
    """Graph placement: one column per role group; parallel instances stack in their column."""
    out, rows = [], {g: 0 for g in GROUPS}
    for r in team:
        g = r.split("_")[0]
        if g not in rows:
            continue
        ang = roles.angle(r)
        num = r.split("_")[1] if "_" in r else ""
        label = {"theorist": "Lead theorist"}.get(r, f"{g.capitalize()}{' ' + num if num else ''}")
        out.append({"id": r, "group": g, "label": label, "sub": ang[0] if ang else SUBS.get(g, ""),
                    "col": GROUPS.index(g), "row": rows[g]})
        rows[g] += 1
    return out
DUP_WINDOW_S = 5.0
# messages from mcp_server.Door that mean a rule was enforced (vs. ordinary errors like a missing file)
DENIALS = ("may not write", "outside the study", "is not readable", "must be valid JSON", "does not have",
           "only available in the analyze phase", "limit")


def _tool(name: str | None) -> str:
    """'mcp__omnigent__study__write_file' -> 'write_file'."""
    return (name or "").split("__")[-1]


def _key(r: dict[str, Any]) -> str:
    return json.dumps([r.get("phase"), r.get("tool"), r.get("args"), (r.get("text") or "")[:300],
                       r.get("session_usage")], sort_keys=True, default=str)


def load_trace(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    recs = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            recs.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    recs.sort(key=lambda r: r["t"])
    # drop the root policy's copies of sub-agent events
    child: dict[str, list[float]] = {}
    for r in recs:
        if r["role"] != "pi":
            child.setdefault(_key(r), []).append(r["t"])
    out = []
    for r in recs:
        if r["role"] == "pi":
            ts = child.get(_key(r))
            if ts:
                hit = next((t for t in ts if abs(t - r["t"]) <= DUP_WINDOW_S), None)
                if hit is not None:
                    ts.remove(hit)
                    continue
        out.append(r)
    return out


def _clip(s: Any, n: int = 220) -> str:
    s = "" if s is None else str(s)
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def build(study: Study, n: int | None = None, job_running: bool = False) -> dict[str, Any]:
    n = study.round if n is None else n
    if n == 0:
        return {"round": None, "roles": layout(roles.team(study.config.get("fanout", {}))), "agents": {},
                "edges": [], "files": [], "spans": [], "feed": [], "phases": [], "traced": False}
    rdir = study.rdir(n)
    recs = load_trace(rdir / "trace.jsonl")
    team = roles.team(study.config.get("fanout", {}))
    AGENTS = ["pi", *team]
    agents: dict[str, dict[str, Any]] = {
        a: {"state": "idle", "detail": "", "task": None, "report": None, "calls": 0, "tools": {},
            "tokens": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}, "cost_usd": 0.0,
            "last_t": None, "open": False, "stage": None} for a in AGENTS}
    edges: list[dict[str, Any]] = []
    files: dict[str, dict[str, Any]] = {}
    spans: list[dict[str, Any]] = []
    feed: list[dict[str, Any]] = []
    open_span: dict[str, dict[str, Any]] = {}

    def say(t: float, agent: str, kind: str, text: str) -> None:
        feed.append({"t": t, "agent": agent, "kind": kind, "text": text})

    def touch(path: str, agent: str, how: str, t: float) -> None:
        f = files.setdefault(path, {"path": path, "writers": [], "readers": [], "t": t})
        lst = f["writers"] if how == "write" else f["readers"]
        if agent not in lst:
            lst.append(agent)
        f["t"] = t

    # driver-level events for this round (plan / approve / run), from events.jsonl
    import time as _time
    for e in study.events():
        if e.get("round") != n:
            continue
        try:
            t = _time.mktime(_time.strptime(e["t"], "%Y-%m-%dT%H:%M:%S"))
        except (KeyError, ValueError):
            continue
        ev = e["event"]
        if ev == "plan_start":
            edges.append({"from": "driver", "to": "pi", "kind": "start", "label": "PHASE plan", "t": t})
            say(t, "driver", "start", "Started the planning phase" + (f" with your feedback: {_clip(e.get('feedback'), 160)}" if e.get("feedback") else ""))
        elif ev == "approved":
            edges.append({"from": "you", "to": "driver", "kind": "approve", "label": "approved", "t": t})
            say(t, "you", "approve", "You approved the plan; experiment and predictions frozen")
        elif ev == "run_start":
            edges.append({"from": "driver", "to": "sandbox", "kind": "run", "label": "run.py", "t": t})
            spans.append({"agent": "sandbox", "start": t, "end": None, "kind": "run"})
            say(t, "driver", "run", f"Started the experiment in the sandbox ({e.get('sandbox')}, cap {round(e.get('limit_s', 0) / 60)} min)")
        elif ev == "run_end":
            for s in spans:
                if s["agent"] == "sandbox" and s["end"] is None:
                    s["end"] = t
            ok = e.get("exit") == 0 and not e.get("timed_out")
            edges.append({"from": "sandbox", "to": "files", "kind": "write", "label": "output/", "t": t})
            touch("output/results.json", "sandbox", "write", t)
            say(t, "sandbox", "run", f"Experiment {'finished' if ok else 'failed'} after {e.get('seconds')} s (exit {e.get('exit')})")

    for r in recs:
        role, ph, t = r["role"], r["phase"], r["t"]
        if role not in agents:
            continue
        a = agents[role]
        a["last_t"] = t
        if u := r.get("session_usage"):
            a["tokens"] = {"input": int(u.get("input_tokens") or 0), "output": int(u.get("output_tokens") or 0),
                           "cache_read": int(u.get("cache_read_input_tokens") or 0),
                           "cache_write": int(u.get("cache_creation_input_tokens") or 0)}
            a["cost_usd"] = float(u.get("total_cost_usd") or 0)
        if ph == "request":
            text = r.get("text") or ""
            if role == "pi":
                if text.startswith("[System: sub-agent"):
                    who = text.split("sub-agent", 1)[1].split("/")[0].strip()
                    say(t, "pi", "wake", f"Woke up: {who} finished")
                else:
                    stage = "analyze" if "PHASE analyze" in text else "plan"
                    if "PROBLEMS" in text:
                        say(t, "pi", "task", "Got validation problems to fix")
                    elif not any(x["kind"] == "start" and x["label"] == f"PHASE {stage}" and abs(x["t"] - t) < 120 for x in edges):
                        edges.append({"from": "driver", "to": "pi", "kind": "start", "label": f"PHASE {stage}", "t": t})
                        say(t, "driver", "start", f"Started the {stage} phase")
                    a["task"] = text
                a["state"], a["detail"] = "working", "reading the task"
            else:
                a["task"], a["state"], a["detail"] = text, "working", "starting"
                if role not in open_span:
                    open_span[role] = {"agent": role, "start": t, "end": None, "kind": "work"}
                    spans.append(open_span[role])
            if role == "pi" and "pi" not in open_span:
                open_span["pi"] = {"agent": "pi", "start": t, "end": None, "kind": "work"}
                spans.append(open_span["pi"])
        elif ph == "llm_request":
            a["state"], a["detail"] = "working", "thinking"
        elif ph == "llm_response":
            if r.get("text_preview"):
                a["detail"] = "thinking: " + _clip(r["text_preview"], 120)
        elif ph == "tool_call":
            tool, args = _tool(r.get("tool")), r.get("args") or {}
            a["calls"] += 1
            a["tools"][tool] = a["tools"].get(tool, 0) + 1
            path = args.get("path") if isinstance(args, dict) else None
            if tool == "sys_session_send" and isinstance(args, dict):
                to = args.get("agent", "?")
                edges.append({"from": role, "to": to, "kind": "dispatch", "label": args.get("title", ""), "t": t})
                say(t, role, "dispatch", f"Handed {to} a task: {_clip(args.get('args'), 260)}")
                a["detail"] = f"dispatching {to}"
            elif tool == "sys_read_inbox":
                a["detail"] = "reading the inbox"
            elif tool == "write_file" and path:
                edges.append({"from": role, "to": "file:" + path, "kind": "write", "label": path, "t": t})
                touch(path, role, "write", t)
                a["detail"] = f"writing {path}"
            elif tool == "read_file" and path:
                edges.append({"from": "file:" + path, "to": role, "kind": "read", "label": path, "t": t})
                touch(path, role, "read", t)
                a["detail"] = f"reading {path}"
            elif tool == "list_files":
                a["detail"] = f"listing {args.get('path', '.') if isinstance(args, dict) else '.'}"
            elif tool == "study_brief":
                a["detail"] = "reading the study brief"
            elif tool == "check_syntax":
                a["detail"] = f"checking syntax of {path}"
            elif tool == "run_analysis":
                edges.append({"from": role, "to": "sandbox", "kind": "exec", "label": "analysis script", "t": t})
                say(t, role, "exec", "Ran an analysis script in the sandbox")
                a["detail"] = "running an analysis script"
            elif tool in ("web_search", "web_fetch"):
                q = args.get("query") or args.get("url") or args.get("prompt") if isinstance(args, dict) else ""
                edges.append({"from": role, "to": "web", "kind": "web", "label": _clip(q, 60), "t": t})
                say(t, role, "web", f"{'Searched the web' if tool == 'web_search' else 'Fetched'}: {_clip(q, 140)}")
                a["detail"] = f"{tool.replace('_', ' ')}: {_clip(q, 60)}"
            elif not tool.startswith("sys_"):
                a["detail"] = f"using {tool}"
        elif ph == "tool_result":
            tool = _tool(r.get("tool"))
            res = r.get("result")
            if isinstance(res, str) and res.startswith("ERROR:"):
                args = r.get("args") or {}
                if any(m in res for m in DENIALS):  # the door refused: a role or phase rule was enforced
                    edges.append({"from": role, "to": "file:" + str(args.get("path", "?")), "kind": "denied",
                                  "label": _clip(res, 80), "t": t})
                    say(t, role, "denied", f"Blocked by the study door: {_clip(res[6:], 200)}")
                else:
                    say(t, role, "error", f"{tool} failed: {_clip(res[6:], 200)}")
            elif tool == "write_file" and isinstance(res, str):
                say(t, role, "write", _clip(res[0].upper() + res[1:], 200))
            a["detail"] = "thinking"
        elif ph == "response":
            text = r.get("text") or ""
            if role != "pi":
                a["report"] = text
                a["state"], a["detail"] = "done", "reported back"
                edges.append({"from": role, "to": "pi", "kind": "report", "label": "report", "t": t})
                say(t, role, "report", f"Reported back: {_clip(text, 300)}")
                if role in open_span:
                    open_span.pop(role)["end"] = t
            else:
                a["report"] = text
                children = [x for x in team if agents[x]["state"] == "working"]
                a["state"] = "waiting" if children else "done"
                a["detail"] = f"waiting for {', '.join(children)}" if children else "turn finished"
                say(t, "pi", "reply", _clip(text, 300))
                if not children and "pi" in open_span:
                    open_span.pop("pi")["end"] = t

    # when no phase process is running, nothing can still be working
    if not job_running:
        for a in agents.values():
            if a["state"] in ("working", "waiting"):
                a["state"], a["detail"] = "stopped", "phase ended"
        last = recs[-1]["t"] if recs else None
        for s in spans:
            if s["end"] is None and s["agent"] != "sandbox":
                s["end"] = last
    run = read_json(rdir / "run.json")
    sandbox = {"state": "idle", "stdout_tail": None}
    if study.status == "running":
        sandbox["state"] = "working"
        out = rdir / "output" / "stdout.log"
        if out.exists():
            lines = out.read_text(encoding="utf-8", errors="replace").splitlines()
            sandbox["stdout_tail"] = lines[-6:]
    elif run:
        sandbox["state"] = "done" if run.get("exit_code") == 0 else "failed"
    for a in agents.values():
        a.pop("open", None)
    phases = [e for e in study.events() if e.get("round") == n and e["event"] == "phase"]
    return {
        "round": round_name(n), "roles": layout(team), "agents": agents, "sandbox": sandbox, "edges": edges[-5000:],
        "files": sorted(files.values(), key=lambda f: f["path"]), "spans": spans, "feed": feed[-3000:],
        "phases": phases, "events": len(recs), "traced": (rdir / "trace.jsonl").exists(),
    }
