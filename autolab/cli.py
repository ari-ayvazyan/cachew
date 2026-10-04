"""autolab: an Omnigent research team with a human approval gate.

    autolab new <name> "<question>"     create studies/<name>
    autolab plan <study>                agents plan the next round (scout, theorist, PI, experimenter)
    autolab show <study>                print the plan awaiting approval
    autolab revise <study> "<feedback>" agents rework the plan
    autolab approve <study> [--run]     freeze experiment + predictions (and run)
    autolab run <study>                 run the approved experiment, then skeptic + judge
    autolab next <study>                do whichever of plan / run / analyze is due
    autolab status <study>              where things stand
    autolab report <study>              write REPORT.md
    autolab ui [--port 8765]            control everything from the browser
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from autolab import board, bundle, execute, omni, report, validate
from autolab.study import STATUSES, Study, StudyError, read_json, round_name

MAX_FIX_ATTEMPTS = 2


def find(arg: str) -> Study:
    p = Path(arg)
    if not (p / "study.json").exists() and (Path("studies") / arg / "study.json").exists():
        p = Path("studies") / arg
    return Study(p)


def _phase(study: Study, phase: str, prompt: str, check, label: str) -> list[str]:  # type: ignore[no-untyped-def]
    """Run an Omnigent phase, then up to MAX_FIX_ATTEMPTS fix passes. Returns remaining problems."""
    b = bundle.generate(study, phase)
    rc, reply = omni.run_phase(study, b, prompt, label)
    print(f"\nPI: {reply}\n" if reply else f"(PI gave no reply, exit {rc})")
    problems = check(study)
    for _ in range(MAX_FIX_ATTEMPTS):
        if not problems:
            break
        print("Validation problems, sending back to the PI:\n  - " + "\n  - ".join(problems))
        fix = (f"PHASE {phase}, round {round_name(study.round)}. Validation of the artifacts found these "
               f"PROBLEMS. Fix exactly these by re-dispatching the sub-agent that owns each file, then "
               f"reply with a short summary.\n- " + "\n- ".join(problems))
        rc, reply = omni.run_phase(study, b, fix, f"{label}-fix")
        print(f"\nPI: {reply}\n" if reply else "")
        problems = check(study)
    return problems


def _prev_direction(study: Study) -> str:
    if study.round <= 1:
        return ""
    v = read_json(study.rdir(study.round - 1) / "verdict.json", {})
    return (f"\nPrevious round's judge summary: {v.get('summary', '')}\n"
            f"Judge's suggested next direction: {v.get('next_direction', '')}\n") if v else ""


def cmd_new(a: argparse.Namespace) -> None:
    root = Path(a.dir) / a.name
    models = {"pi": a.pi_model} if a.pi_model else {}
    fanout = {"scout": a.fanout, "theorist": a.fanout, "skeptic": a.fanout} if a.fanout else None
    s = Study.create(root, a.question, context=a.context or "", model=a.model, models=models,
                     max_run_minutes=a.max_run_minutes, max_rounds=a.max_rounds, fanout=fanout)
    board.render(s)
    print(f"created {s.root}\nnext: autolab plan {root}")


def cmd_plan(a: argparse.Namespace, feedback: str = "") -> None:
    s = find(a.study)
    if feedback:
        s.require("awaiting_approval")
        _archive_plan(s)
    else:
        s.require("ready", "plan_failed")
        if s.status == "ready":
            if s.round >= s.config["max_rounds"]:
                raise StudyError(f"round budget ({s.config['max_rounds']}) used up; raise max_rounds in study.json")
            s.update(round=s.round + 1)
    s.rdir().mkdir(parents=True, exist_ok=True)
    s.update(status="planning")
    s.log("plan_start", round=s.round, feedback=feedback)
    R = round_name(s.round)
    prompt = (f"PHASE plan, round {R}. Study question: {s.meta['question']}\n"
              f"Plan round {R} following your PHASE plan procedure.{_prev_direction(s)}")
    if feedback:
        prompt += f"\nHUMAN FEEDBACK on the current plan (address it, redo only affected steps):\n{feedback}\n"
    print(f"Planning {R} with Omnigent (scouts -> theorists -> lead theorist -> PI -> experimenter -> lead theorist). "
          f"This takes a few minutes.")
    try:
        problems = _phase(s, "plan", prompt, validate.plan, "plan")
    except BaseException:
        s.update(status="plan_failed")
        raise
    if (s.rdir() / "candidates.json").exists() and not any("candidates.json" in p for p in problems):
        s.merge_candidates(s.round)
    s.update(status="awaiting_approval", plan_problems=problems)
    s.log("plan_end", round=s.round, problems=problems)
    board.render(s)
    _show(s)


def _archive_plan(s: Study) -> None:
    r = s.rdir()
    n = len(list((r / "revisions").glob("v*"))) + 1 if (r / "revisions").exists() else 1
    dst = r / "revisions" / f"v{n}"
    dst.mkdir(parents=True)
    for name in ["candidates.json", "selection.json", "plan.md", "predictions.json", "experiment"]:
        src = r / name
        if src.is_dir():
            shutil.copytree(src, dst / name)
        elif src.exists():
            shutil.copy2(src, dst / name)


def _show(s: Study) -> None:
    r = s.rdir()
    sel = read_json(r / "selection.json", {})
    print(f"\n=== {round_name(s.round)} plan awaiting your approval ===")
    print(f"chosen: {sel.get('chosen')} - {sel.get('why', '')}")
    print(f"\nplan:        {r / 'plan.md'}\ncode:        {r / 'experiment'}\n"
          f"predictions: {r / 'predictions.json'}\nboard:       {s.root / 'board.html'}")
    if probs := s.meta.get("plan_problems"):
        print("\nUNRESOLVED PROBLEMS (consider `autolab revise`):\n  - " + "\n  - ".join(probs))
    print(f"\nnext: autolab approve {s.root} --run   |   autolab revise {s.root} \"<feedback>\"")


def cmd_show(a: argparse.Namespace) -> None:
    s = find(a.study)
    plan = s.rdir() / "plan.md"
    if plan.exists():
        print(plan.read_text(encoding="utf-8"))
    pred = s.rdir() / "predictions.json"
    if pred.exists():
        print("\n## Pre-registered predictions\n" + pred.read_text(encoding="utf-8"))
    _show(s)


def cmd_approve(a: argparse.Namespace) -> None:
    s = find(a.study)
    rec = execute.approve(s, a.note or "")
    s.update(status="approved")
    print(f"approved {round_name(s.round)}; experiment {rec['hashes']['experiment'][:12]} frozen")
    board.render(s)
    if a.run:
        cmd_run(a)
    else:
        print(f"next: autolab run {s.root}")


def cmd_run(a: argparse.Namespace) -> None:
    s = find(a.study)
    s.require("approved")
    s.update(status="running")
    print(f"Running {round_name(s.round)} experiment (cap {s.config['max_run_minutes']} min) ...")
    try:
        rec = execute.run(s)
    except BaseException:
        s.update(status="approved")
        raise
    print(f"\nexit {rec['exit_code']}{' (TIMED OUT)' if rec['timed_out'] else ''}, "
          f"{rec['wall_seconds']}s, results.json {rec['results_json']}, sandbox {rec['sandbox']}")
    s.update(status="ran")
    board.render(s)
    cmd_analyze(a)


def cmd_analyze(a: argparse.Namespace) -> None:
    s = find(a.study)
    s.require("ran", "analysis_failed")
    s.update(status="analyzing")
    R = round_name(s.round)
    run = read_json(s.rdir() / "run.json", {})
    prompt = (f"PHASE analyze, round {R}. Study question: {s.meta['question']}\n"
              f"The approved experiment has run: exit {run.get('exit_code')}, timed_out {run.get('timed_out')}, "
              f"{run.get('wall_seconds')}s, results.json {run.get('results_json')}. "
              f"Follow your PHASE analyze procedure.")
    print(f"Analyzing {R} with Omnigent (skeptics -> judge) ...")
    try:
        problems = _phase(s, "analyze", prompt, validate.analysis, "analyze")
    except BaseException:
        s.update(status="analysis_failed")
        raise
    if bad := execute.tampered(s):
        problems.append(f"INTEGRITY: {', '.join(bad)} changed after approval")
    if problems:
        s.update(status="analysis_failed", analysis_problems=problems)
        board.render(s)
        print("Analysis incomplete:\n  - " + "\n  - ".join(problems) + f"\nretry: autolab analyze {s.root}")
        return
    s.apply_verdict(s.round)
    v = read_json(s.rdir() / "verdict.json", {})
    done = v.get("decision") == "conclude" or s.round >= s.config["max_rounds"]
    s.update(status="concluded" if done else "ready", analysis_problems=[])
    s.log("round_end", round=s.round, decision=v.get("decision"))
    board.render(s)
    report.write(s)
    print(f"\n=== {R} verdict ===\n{v.get('summary', '')}\n")
    for h in s.hypotheses():
        c = h["credence"].get(R)
        print(f"  {h['id']:4} {h['status']:9} {'' if c is None else f'{c:.2f}':5}  {h['statement'][:90]}")
    print(f"\ndecision: {v.get('decision')} - {v.get('next_direction', '')}")
    print(f"board: {s.root / 'board.html'}\nnext: {'autolab report' if done else 'autolab plan'} {s.root}")


def cmd_next(a: argparse.Namespace) -> None:
    st = find(a.study).status
    if st in ("ready", "plan_failed"):
        cmd_plan(a)
    elif st == "approved":
        cmd_run(a)
    elif st in ("ran", "analysis_failed"):
        cmd_analyze(a)
    else:
        print(f"status {st}: {STATUSES.get(st, '')}")


RECOVER = {"planning": "plan_failed", "running": "approved", "analyzing": "analysis_failed"}


def cmd_recover(a: argparse.Namespace) -> None:
    """Unstick a study whose phase process died (crash, reboot) without resetting its status."""
    s = find(a.study)
    if s.status not in RECOVER:
        raise StudyError(f"study is '{s.status}', nothing to recover")
    new = RECOVER[s.status]
    s.log("recovered", old=s.status, new=new)
    s.update(status=new)
    board.render(s)
    print(f"{s.status}: next {STATUSES[new]}")


def cmd_reopen(a: argparse.Namespace) -> None:
    """Continue a concluded study with another round (raises max_rounds if it is used up)."""
    s = find(a.study)
    s.require("concluded")
    cfg = s.config
    if s.round >= cfg["max_rounds"]:
        s.update(config={**cfg, "max_rounds": s.round + 1})
    s.update(status="ready")
    s.log("reopened", round=s.round)
    board.render(s)
    print(f"reopened; next: autolab plan {s.root}")


def cmd_status(a: argparse.Namespace) -> None:
    s = find(a.study)
    m = s.meta
    print(f"{m['question']}\nstatus: {m['status']} (round {round_name(m['round'])}) -> {STATUSES.get(m['status'])}")
    for h in s.hypotheses():
        traj = " ".join(f"{k}:{v:.2f}" for k, v in sorted(h["credence"].items()))
        print(f"  {h['id']:4} {h['status']:9} {h['statement'][:70]:70}  {traj}")
    print(f"board: {s.root / 'board.html'}")


def cmd_report(a: argparse.Namespace) -> None:
    s = find(a.study)
    print(f"wrote {report.write(s)}")


def cmd_board(a: argparse.Namespace) -> None:
    s = find(a.study)
    print(f"wrote {board.render(s)}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="autolab", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("new", help="create a study")
    p.add_argument("name")
    p.add_argument("question")
    p.add_argument("--context", help="background, constraints, what you already know")
    p.add_argument("--dir", default="studies")
    p.add_argument("--model", default=None, help="model for all agents (default claude-sonnet-5-5)")
    p.add_argument("--pi-model", default=None, help="model for the PI only")
    p.add_argument("--max-run-minutes", type=float, default=None)
    p.add_argument("--max-rounds", type=int, default=None)
    p.add_argument("--fanout", type=int, default=None, help="parallel scouts/theorists/skeptics (default 3)")
    p.set_defaults(fn=cmd_new)
    for name, fn, h in [("plan", cmd_plan, "plan the next round"), ("show", cmd_show, "print the pending plan"),
                        ("run", cmd_run, "run approved experiment + analysis"),
                        ("analyze", cmd_analyze, "(re)run skeptic + judge"), ("next", cmd_next, "do the next due step"),
                        ("status", cmd_status, "show status"), ("report", cmd_report, "write REPORT.md"),
                        ("board", cmd_board, "re-render board.html"),
                        ("recover", cmd_recover, "unstick a study whose phase process died"),
                        ("reopen", cmd_reopen, "plan another round of a concluded study")]:
        p = sub.add_parser(name, help=h)
        p.add_argument("study")
        p.set_defaults(fn=fn)
    p = sub.add_parser("approve", help="approve the pending plan")
    p.add_argument("study")
    p.add_argument("--note")
    p.add_argument("--run", action="store_true", help="run immediately after approving")
    p.set_defaults(fn=cmd_approve)
    p = sub.add_parser("revise", help="send feedback on the pending plan")
    p.add_argument("study")
    p.add_argument("feedback")
    p.set_defaults(fn=lambda a: cmd_plan(a, feedback=a.feedback))
    p = sub.add_parser("ui", help="start the browser UI")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--dir", default="studies")
    p.set_defaults(fn=lambda a: __import__("autolab.server", fromlist=["serve"]).serve(a.port, Path(a.dir)))
    a = ap.parse_args(argv)
    try:
        a.fn(a)
    except StudyError as exc:
        sys.exit(f"autolab: {exc}")


if __name__ == "__main__":
    main()
