"""A research team as Omnigent agents, with cached fan-outs.

The PI is an Omnigent agent on the ``cachew`` harness whose turns run
``pi()`` below; its team members are Omnigent sub-agents it dispatches with
``sys_session_send``. Omnigent wakes the PI whenever a member finishes.

    PI plans ─▶ brief A ─▶ scouts ×N
             ─▶ brief B ─▶ theorists ×N ─▶ lead theorist ─▶ experimenter
             ─▶ scientist approves the experiment (optional gate)
             ─▶ brief C ─▶ skeptics ×N  ─▶ judge ─▶ verdict

Each brief is everything the team knows so far. Before a stage's first
dispatch the PI publishes it and pre-warms it (one ``max_tokens: 0`` call
that writes the cache for the exact prefix every member will send), so all
members of that stage, and the single agents after them, read it from cache.
All members share one system prompt, which keeps their prefixes identical.

``make_study`` writes the request, the team layout (``run.json``) and the
Omnigent bundle (``agent/``); ``pi`` advances the run one step per turn.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

from cachew.harness import (Turn, dispatch_tag, forget_key, load_brief, publish_brief, read_json, record_call,
                            system_sha, write_json)
from cachew.pricing import MIN_CACHE_TOKENS

PROGRAM = "lab.studio.team:pi"
APPROVAL_TIMEOUT_S = 45 * 60  # below Omnigent's one-hour idle watchdog for a harness turn

ROLES: dict[str, dict[str, Any]] = {
    "scout": {"name": "Scout", "angles": ["prior results", "methods", "counter-evidence", "data sources",
                                          "adjacent fields", "recent developments"]},
    "theorist": {"name": "Theorist", "angles": ["mechanism", "null and confounds", "literature",
                                                "quantitative model", "alternative framing", "edge cases"]},
    "lead": {"name": "Lead theorist", "angles": ["merges + predicts"]},
    "experimenter": {"name": "Experimenter", "angles": ["design + code"]},
    "skeptic": {"name": "Skeptic", "angles": ["numbers", "code vs plan", "alternative explanations",
                                              "overclaiming", "reproducibility", "missing controls"]},
    "judge": {"name": "Judge", "angles": ["verdict"]},
}

# What each role decides, reads and writes, and what it may use: the team's agent specification.
NO_TOOLS = "none: no web, no code execution, no file access (Omnigent tools disabled)"
ROLE_SPECS: dict[str, dict[str, str]] = {
    "pi": {"name": "PI", "decision": "What the question means, what counts as an answer, and when each stage starts",
           "inputs": "Research question and context from the scientist", "output": "plan.md, briefs A-C",
           "tools": "Omnigent sys_session_send to dispatch members; no web, no code execution"},
    "scout": {"name": "Scout", "decision": "Which known evidence bears on the question, and how reliable it is",
              "inputs": "Brief A: question, context, plan", "output": "literature/scout-N.md", "tools": NO_TOOLS},
    "theorist": {"name": "Theorist", "decision": "Which competing hypotheses explain the evidence, and how to refute each",
                 "inputs": "Brief B: plan and scout reports", "output": "proposals/theorist-N.md", "tools": NO_TOOLS},
    "lead": {"name": "Lead theorist", "decision": "The candidate hypotheses, their priors and their predictions",
             "inputs": "Brief B and all theorist proposals", "output": "candidates.md, candidates.json, predictions.json",
             "tools": NO_TOOLS},
    "experimenter": {"name": "Experimenter", "decision": "Which of 2-3 tests to run, by information per unit of effort",
                     "inputs": "Brief B and the candidates", "output": "experiment/design.md, analysis.py, selection.json",
                     "tools": NO_TOOLS},
    "human": {"name": "Scientist", "decision": "Approve, change or reject the experiment before it is reviewed",
              "inputs": "The tests, the chosen test and the reason", "output": "approval.json",
              "tools": "Cachew Studio"},
    "skeptic": {"name": "Skeptic", "decision": "Whether the design, code and claims survive one line of attack",
                "inputs": "Brief C: everything so far, plus the scientist's decision", "output": "reviews/skeptic-N.md",
                "tools": NO_TOOLS},
    "judge": {"name": "Judge", "decision": "The answer, the probability of each hypothesis, and the next experiment",
              "inputs": "Brief C and all reviews", "output": "verdict.md, decision.json", "tools": NO_TOOLS},
}

# step id, stage (brief), role, fan-out?
STEPS: list[tuple[str, str, str, bool]] = [
    ("plan", "", "pi", False),
    ("scouts", "A", "scout", True),
    ("theorists", "B", "theorist", True),
    ("lead", "B", "lead", False),
    ("experimenter", "B", "experimenter", False),
    ("approve", "", "human", False),
    ("skeptics", "C", "skeptic", True),
    ("judge", "C", "judge", False),
    ("verdict", "", "pi", False),
]

TEAM_PROMPT = """\
You are a member of a research team that works on one question at a time:
a PI, scouts, theorists, a lead theorist, an experimenter, skeptics and a
judge. You receive a knowledge brief holding everything the team has
established so far, then your own task. Treat the brief as shared ground
truth and build on it rather than repeating it. You cannot browse the web or
run code: when you use your own background knowledge, say so and state how
confident you are, and never invent citations, numbers or sources. Say
plainly when something is unknown. Do only your own task, in clean Markdown,
as concisely as the task allows."""

WRITING_RULES = """\
How to write. A scientist skims your output, so say more with fewer words:
- Lead with the finding or answer, then the support. No preamble, no closing summary.
- Prefer a table or short bullets to paragraphs. One idea per bullet, one
  line where possible. Keep any paragraph to three sentences or fewer.
- Use numbers with units instead of adjectives ("lowers the error by 12%", not
  "a substantial improvement"). Don't repeat what the brief already says.
- Word limits are ceilings, not targets. Stop once the point is made.
- Write plainly: use "is" and "has", not "serves as" or "boasts". Avoid filler
  ("it is important to note", "in order to") and stacked hedges; state
  uncertainty once, as a number or a plain "unknown".
- Avoid inflated or promotional words: crucial, pivotal, key (as an
  adjective), delve, underscore, highlight (as a verb), showcase, landscape,
  tapestry, testament, robust, groundbreaking, comprehensive, seamless.
- Don't announce what you are about to do ("let's explore", "here is").
  Don't use "not only ... but", "it's not X, it's Y", or lists forced into
  threes.
- Never write em dashes or en dashes; use a period, comma, colon or
  parentheses. Use straight quotes, sentence-case headings, no emojis, and
  bold only where a reader truly must not miss something.
- Never attribute claims to vague authorities ("experts say"); name the
  source or the team artifact, or say it is your own background knowledge."""

TEAM_PROMPT += "\n\n" + WRITING_RULES

PI_PROMPT = """\
You are the PI of a research team. You turn a research question into a
plan that the team can act on, and you coordinate the team. Your turns are
driven by the cachew orchestration program; write plans that are concrete,
testable and honest about what can and cannot be known.

""" + WRITING_RULES

TASKS = {
    "plan": """Research question:
{question}

{context_note}Write the research plan (plan.md), at most 400 words:
1. Restate the question precisely and say what would count as an answer.
2. List 3-5 sub-questions.
3. Say what the scouts should look for: prior results, methods, and counter-evidence.
4. Name the main risks of fooling ourselves.""",
    "scout": """You are {name}, scouting for **{angle}**.
Report what is known that bears on the question from this angle: concrete
findings, numbers with units, named sources (only ones you are confident
exist) and how reliable each item is. At most 450 words. End with the two
facts that matter most for the question.""",
    "theorist": """You are {name}, working from **{angle}**.
Propose 2-3 competing hypotheses that answer the question. For each give: an
id (e.g. {short}-1), the claim, the reasoning from the brief, one prediction
that a feasible test could check, and what result would refute it. At most
450 words.""",
    "lead": """You are the lead theorist. The theorists proposed:

{proposals}

Merge these into 3-6 distinct candidate hypotheses, always including
"none of these is right". For each: id (H1, H2, ...), claim, prior
probability (they sum to 1), and the prediction each makes for the most
decisive feasible test. Write it as Markdown, then end with one JSON code
block: {{"candidates": [{{"id", "claim", "prior"}}], "predictions": [{{"id", "test", "predicts"}}]}}.""",
    "experimenter": """You are the experimenter. The lead theorist's candidates:

{candidates}

1. List 2-3 candidate tests and how strongly each separates the hypotheses;
   pick the one with the most information per unit of effort and say why.
2. Write the design: data, procedure, controls, sample size, and the result
   each hypothesis predicts.
3. Write a short self-contained Python script (standard library only) that
   performs the analysis on the data described, or simulates it from numbers
   in the brief where real data is not available. Say which.
End with one JSON code block: {{"selection": {{"tests": [...], "chosen": "...", "why": "..."}}}}.""",
    "skeptic": """You are {name}, auditing for **{angle}**.
Review the candidates, the experiment design and the code in the brief from
this angle only. List each concrete problem with where it is and how serious
it is (blocker, major, minor), and say what would fix it. Start with one
line: "Verdict: PASS", "Verdict: CONCERNS" or "Verdict: FAIL". At most 350 words.""",
    "judge": """You are the judge; you proposed nothing and ran nothing. The skeptics wrote:

{reviews}

Weigh the hypotheses, the experiment and the reviews. Write the verdict:
1. The answer to the research question, or "unresolved" and why.
2. A probability for each candidate hypothesis after the evidence.
3. What the skeptics changed, and which objections stand.
4. The single next experiment that would settle what remains.
5. The validation still needed before anyone relies on this in the real world.
Cite the team's artifacts (plan, scouts, theorists, candidates, experiment,
reviews) by name. Start with one line: "Verdict: <one sentence>". End with one
JSON code block: {{"decision": {{"answer": "<one sentence>", "status": "supported" | "refuted" | "unresolved",
"posterior": [{{"id", "p"}}], "next_experiment": "...", "validation_needed": "..."}}}}.""",
}

OUT = {"scout": "literature/scout-{i}.md", "theorist": "proposals/theorist-{i}.md", "lead": "candidates.md",
       "experimenter": "experiment/design.md", "skeptic": "reviews/skeptic-{i}.md", "judge": "verdict.md"}


# -- study setup (called by the studio, outside Omnigent) -----------------------


def team_layout(width: int) -> dict[str, dict[str, Any]]:
    """Every agent the run will use, so a UI can draw the team before anything starts."""
    agents: dict[str, dict[str, Any]] = {"pi": {"role": "pi", "name": "PI", "focus": "plans + coordinates", "step": "plan"}}
    for step, stage, role, fan in STEPS:
        if role in ("pi", "human"):
            continue
        n = width if fan else 1
        for i in range(1, n + 1):
            label = f"{role}-{i}" if fan else role
            angles = ROLES[role]["angles"]
            agents[label] = {"role": role, "name": f"{ROLES[role]['name']} {i}" if fan else ROLES[role]["name"],
                             "focus": angles[(i - 1) % len(angles)], "step": step, "stage": stage,
                             "out": OUT[role].format(i=i)}
    return agents


def _yaml_block(text: str, indent: int = 2) -> str:
    return "|\n" + "\n".join(" " * indent + line for line in text.splitlines())


def write_bundle(study: Path, model: str, effort: str) -> Path:
    """The Omnigent bundle: the PI plus one sub-agent per role, all on the cachew harness."""
    root = study / "agent"

    def executor(program: str = "", max_tokens: int = 16000) -> str:
        lines = ["executor:", "  type: omnigent", f"  model: {model}"]
        if effort:
            lines.append(f"  reasoning_effort: {effort}")
        lines += ["  config:", "    harness: cachew", f"    study: '{study.as_posix()}'", '    tools: "0"',
                  f'    max_tokens: "{max_tokens}"']
        if program:
            lines.append(f"    program: {program}")
        return "\n".join(lines)

    (root / "agents").mkdir(parents=True, exist_ok=True)
    (root / "config.yaml").write_text("\n".join([
        "spec_version: 1", "name: research-pi",
        "description: PI of a cachew research team; fans out to Omnigent sub-agents over cached briefs.",
        executor(PROGRAM, 8000), "async: true", f"prompt: {_yaml_block(PI_PROMPT)}",
        "tools:", "  agents: [" + ", ".join(ROLES) + "]", ""]), encoding="utf-8")
    for role, spec in ROLES.items():
        d = root / "agents" / role
        d.mkdir(parents=True, exist_ok=True)
        (d / "config.yaml").write_text("\n".join([
            "spec_version: 1", f"name: {role}", f"description: {spec['name']} on the research team.",
            executor(max_tokens=16000), f"prompt: {_yaml_block(TEAM_PROMPT)}", ""]), encoding="utf-8")
    return root


def make_study(study: Path, question: str, context: str, model: str, effort: str, width: int, budget_usd: float,
               approval: bool = False, own_key: bool = False) -> dict[str, Any]:
    study.mkdir(parents=True, exist_ok=True)
    write_bundle(study, model, effort)
    run = {"question": question, "context": context, "model": model, "effort": effort, "width": width,
           "budget_usd": budget_usd, "status": "starting", "phase": "starting Omnigent", "created": time.time(),
           "step": 0, "steps": [{"id": s, "stage": st, "role": r, "fan": f, "status": "pending"} for s, st, r, f in STEPS],
           "agents": team_layout(width), "briefs": {}, "min_cache_tokens": MIN_CACHE_TOKENS.get(model),
           "approval": approval, "key": "own" if own_key else "shared"}
    write_json(study / "run.json", run)
    return run


# -- the PI program (runs inside the PI's cachew harness) ------------------------


def member_system(study: Path, role: str) -> str:
    """The system prompt Omnigent composes for a member, so the pre-warm matches it byte for byte."""
    from omnigent.runtime.prompt import build_instructions
    from omnigent.spec.parser import parse

    spec = parse(study / "agent")
    child = next(s for s in spec.sub_agents if s.name == role)
    return build_instructions(child, None, [])


def _read(study: Path, rel: str) -> str:
    p = study / rel
    return p.read_text(encoding="utf-8").strip() if p.exists() else "(missing)"


def _labels(run: dict[str, Any], step: str) -> list[str]:
    return [k for k, a in run["agents"].items() if a.get("step") == step]


def _section(title: str, body: str) -> str:
    return f"## {title}\n\n{body.strip()}\n"


def brief_text(study: Path, run: dict[str, Any], stage: str) -> str:
    """Stage A: question + context + plan. B adds scout reports. C adds proposals, candidates, experiment."""
    parts = [_section("Research question", run["question"])]
    if run.get("context"):
        parts.append(_section("Context supplied with the question", run["context"]))
    parts.append(_section("Research plan (PI)", _read(study, "plan.md")))
    if stage in "BC":
        parts += [_section(f"{run['agents'][k]['name']}: {run['agents'][k]['focus']}", _read(study, run["agents"][k]["out"]))
                  for k in _labels(run, "scouts")]
    if stage == "C":
        parts += [_section(f"{run['agents'][k]['name']}: {run['agents'][k]['focus']}", _read(study, run["agents"][k]["out"]))
                  for k in _labels(run, "theorists")]
        parts.append(_section("Candidate hypotheses and predictions (lead theorist)", _read(study, "candidates.md")))
        parts.append(_section("Experiment: selection, design and code (experimenter)", _read(study, "experiment/design.md")))
        if note := approval_note(study):
            parts.append(_section("Scientist's decision on the experiment", note))
    return "\n".join(parts)


def approval_note(study: Path) -> str:
    """The scientist's approval as the team reads it in brief C ('' when the run has no gate)."""
    a = read_json(study / "approval.json")
    if not a or a.get("decision") != "approve":
        return ""
    lines = [f"Approved test: {a['test']}" if a.get("test") else "Approved the experimenter's chosen test."]
    if a.get("changed"):
        lines.append("The scientist chose this test instead of the experimenter's pick; review the design against it.")
    if a.get("note"):
        lines.append(f"Scientist's note: {a['note']}")
    return "\n".join(lines)


def _task(study: Path, run: dict[str, Any], label: str) -> str:
    a = run["agents"][label]
    fill = {"name": a["name"], "angle": a["focus"], "short": label.split("-")[0][:1].upper() + label.split("-")[-1]}
    if a["role"] == "lead":
        fill["proposals"] = "\n\n".join(f"### {run['agents'][k]['name']} ({run['agents'][k]['focus']})\n{_read(study, run['agents'][k]['out'])}"
                                        for k in _labels(run, "theorists"))
    if a["role"] == "experimenter":
        fill["candidates"] = _read(study, "candidates.md")
    if a["role"] == "judge":
        fill["reviews"] = "\n\n".join(f"### {run['agents'][k]['name']} ({run['agents'][k]['focus']})\n{_read(study, run['agents'][k]['out'])}"
                                      for k in _labels(run, "skeptics"))
    return TASKS[a["role"]].format(**fill)


def _json_block(text: str) -> Any:
    blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    for b in reversed(blocks):
        try:
            return json.loads(b)
        except json.JSONDecodeError:
            continue
    return None


def _extract(study: Path, role: str) -> None:
    """Split structured parts out of a finished artifact (best effort; the Markdown stays the record)."""
    if role == "lead" and (j := _json_block(_read(study, "candidates.md"))):
        write_json(study / "candidates.json", j.get("candidates", j))
        write_json(study / "predictions.json", j.get("predictions", []))
    if role == "experimenter":
        design = _read(study, "experiment/design.md")
        if j := _json_block(design):
            write_json(study / "selection.json", j.get("selection", j))
        if code := re.findall(r"```python\s*(.*?)```", design, re.S):
            (study / "experiment" / "analysis.py").write_text(code[0], encoding="utf-8")
    if role == "judge" and (j := _json_block(_read(study, "verdict.md"))):
        write_json(study / "decision.json", j.get("decision", j))


def spent(study: Path) -> float:
    return sum((read_json(p, {}) or {}).get("usd", 0.0) for p in (study / "calls").glob("*.json"))


async def _prepare_stage(turn: Turn, run: dict[str, Any], stage: str, role: str) -> str:
    """Publish the stage's brief and pre-warm it, before any member of the stage starts."""
    if stage in run["briefs"]:
        return run["briefs"][stage]["id"]
    study = turn.study
    text = brief_text(study, run, stage)
    bid = publish_brief(study, stage, text)
    system = member_system(study, role)
    label = f"prewarm-{stage}"
    run["phase"] = f"pre-warming brief {stage} ({len(text):,} chars) before the fan-out"
    write_json(study / "run.json", run)
    record_call(study, label, kind="prewarm", stage=stage, brief=bid, status="thinking", started=time.time(),
                model=turn.model, system_sha=system_sha(system))
    from cachew.compact import subagent_turns

    if len(text) / 4 < (run.get("min_cache_tokens") or 0):
        # Below the model's minimum cacheable prefix: a pre-warm would be billed and cache nothing.
        record_call(study, label, status="skipped", finished=time.time(),
                    note=f"brief under the {run['min_cache_tokens']}-token cache minimum")
        usage = {"input_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 0}
    else:
        usage = await turn.prewarm(subagent_turns(text, "warm"), system)
        record_call(study, label, status="done", finished=time.time(), usage=usage)
    run["briefs"][stage] = {"id": bid, "chars": len(text), "prewarm": label, "system_sha": system_sha(system),
                            "prefix_tokens": usage["cache_creation_input_tokens"] + usage["cache_read_input_tokens"] + usage["input_tokens"]}
    return bid


async def _dispatch(turn: Turn, run: dict[str, Any], step: dict[str, Any]) -> int:
    study = turn.study
    bid = await _prepare_stage(turn, run, step["stage"], step["role"])
    labels = _labels(run, step["id"])
    for label in labels:
        a = run["agents"][label]
        record_call(study, label, kind="member", role=a["role"], stage=step["stage"], brief=bid,
                    status="dispatched", dispatched=time.time(), model=turn.model)
        handle = await turn.tool("sys_session_send", {
            "agent": a["role"], "title": label,
            "args": {"input": dispatch_tag(brief=bid, agent=label, out=a["out"]) + _task(study, run, label),
                     "purpose": f"{a['name']}: {a['focus']}"}})
        a["omnigent"] = handle if isinstance(handle, dict) else {"result": str(handle)[:300]}
    return len(labels)


def _done(study: Path, label: str) -> bool:
    return (read_json(study / "calls" / f"{label}.json", {}) or {}).get("status") in ("done", "error")


TERMINAL = ("done", "failed", "stopped", "budget")


async def pi(turn: Turn, messages: list[dict[str, Any]]) -> str:
    """One PI turn: collect finished members, then start the next step(s) until something is in flight."""
    try:
        return await _advance(turn)
    except Exception as e:  # leave the run in a state the UI can show, then let Omnigent see the failure
        run = read_json(turn.study / "run.json")
        run.update(status="failed", phase=f"PI failed: {type(e).__name__}: {e}"[:500])
        write_json(turn.study / "run.json", run)
        raise
    finally:
        if (read_json(turn.study / "run.json", {}) or {}).get("status") in TERMINAL:
            forget_key(turn.study)


async def await_approval(study: Path, run: dict[str, Any], poll_s: float = 1.0) -> dict[str, Any]:
    """Hold the PI's turn until the scientist decides in the Studio (``approval.json``), stops, or times out."""
    run.update(status="awaiting_approval", phase="waiting for the scientist to approve the experiment")
    write_json(study / "run.json", run)
    deadline = time.time() + APPROVAL_TIMEOUT_S
    while time.time() < deadline:
        if (study / "STOP").exists():
            return {"decision": "stop"}
        if decision := read_json(study / "approval.json"):
            return decision
        await asyncio.sleep(poll_s)
    return {"decision": "timeout"}


async def _advance(turn: Turn) -> str:
    study = turn.study
    run = read_json(study / "run.json")
    if run["status"] in TERMINAL:
        return f"Research is {run['status']}."
    run["status"], run["pi_session"] = "running", turn.session
    if any(s["status"] == "running" and s["role"] != "human" for s in run["steps"]):
        try:  # Omnigent's own completion channel; the outputs themselves are on disk
            await turn.tool("sys_read_inbox", {})
        except Exception:
            pass
    while run["step"] < len(run["steps"]):
        step = run["steps"][run["step"]]
        if (study / "STOP").exists():
            run.update(status="stopped", phase="stopped by user")
            break
        if step["id"] == "approve":
            if not run.get("approval"):
                step.update(status="skipped", finished=time.time())
                run["step"] += 1
                continue
            step.update(status="running", started=step.get("started") or time.time())
            decision = (await await_approval(study, run)).get("decision")
            step.update(status="done" if decision == "approve" else "rejected", finished=time.time())
            if decision != "approve":
                run.update(status="stopped", phase={
                    "reject": "stopped: the scientist rejected the experiment",
                    "timeout": f"stopped: no approval within {APPROVAL_TIMEOUT_S // 60} minutes",
                }.get(decision or "", "stopped by user"))
                break
            run["status"] = "running"
            run["step"] += 1
            continue
        if step["status"] == "running":
            waiting = [k for k in _labels(run, step["id"]) if not _done(study, k)]
            if waiting:
                run["phase"] = f"waiting for {len(waiting)} agent(s): {', '.join(run['agents'][k]['name'] for k in waiting)}"
                write_json(study / "run.json", run)
                return run["phase"]
            _extract(study, step["role"])
            step.update(status="done", finished=time.time())
            run["step"] += 1
            continue
        if spent(study) >= run["budget_usd"]:
            run.update(status="budget", phase=f"stopped: spent ${spent(study):.3f} of the ${run['budget_usd']} budget")
            break
        step.update(status="running", started=time.time())
        if step["id"] == "plan":
            run["phase"] = "PI is writing the research plan"
            write_json(study / "run.json", run)
            record_call(study, "pi-plan", kind="pi", status="thinking", started=time.time(), model=turn.model,
                        session=turn.session)
            note = "Context supplied with the question:\n" + run["context"] + "\n\n" if run.get("context") else ""
            text, _c, usage = await turn.llm([{"role": "user", "content": TASKS["plan"].format(
                question=run["question"], context_note=note)}], max_tokens=8000)
            (study / "plan.md").write_text(text, encoding="utf-8")
            record_call(study, "pi-plan", status="done", finished=time.time(), usage=usage, out="plan.md")
            step.update(status="done", finished=time.time())
            run["step"] += 1
            continue
        if step["id"] == "verdict":
            step.update(status="done", finished=time.time())
            run.update(status="done", finished=time.time(), phase="done: verdict written",
                       verdict=_read(study, "verdict.md").splitlines()[0][:300])
            run["step"] += 1
            break
        n = await _dispatch(turn, run, step)
        run["phase"] = f"{step['id']}: {n} agent(s) dispatched on cached brief {run['briefs'][step['stage']]['id']}"
        write_json(study / "run.json", run)
        return run["phase"]
    write_json(study / "run.json", run)
    return f"Research {run['status']}: {run.get('verdict') or run['phase']}"
