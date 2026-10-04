"""Live A/B experiment: does caching the compacted brief work, and what does it save?

Drives Omnigent's real AnthropicAdapter (patched / unpatched) against the real
API with the same research history and the same N sub-agent tasks, in four arms:

  naive          raw parent history to every sub-agent, no caching (Omnigent today)
  cache_only     raw history, patched adapter (cached)
  compact_only   compacted brief, unpatched adapter (not cached)
  compact_cache  compacted brief, patched adapter (cached)   <- the proposal

The compaction call runs once and is charged to both compact arms. All numbers
come from the API's own ``usage`` fields; dollars from ``pricing.PRICES``.

Requires ANTHROPIC_API_KEY. Spends real money (roughly $1-3 on Opus 5.5 with
the defaults; use --model claude-sonnet-5-5 to halve it).

    .venv/Scripts/python.exe -m cachew.experiment --subagents 8
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from omnigent.llms.adapters import anthropic as adapter_mod

from cachew import patch
from cachew.compact import compaction_messages, pass_history_messages, subagent_messages
from cachew.pricing import add_usage, cost_usd

REPO = Path(__file__).resolve().parent.parent
SYSTEM = "You are a sub-agent of a coding orchestrator working on the Omnigent codebase."
# Real source files as the "tool output" of a research phase.
RESEARCH_FILES = [
    "llms/adapters/anthropic.py",
    "llms/summarize.py",
    "llms/client.py",
    "llms/types.py",
    "inner/tools.py",
]


def research_history() -> list[dict[str, Any]]:
    import omnigent

    root = Path(omnigent.__file__).parent
    history: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM}]
    for rel in RESEARCH_FILES:
        text = (root / rel).read_text(encoding="utf-8")[:40_000]
        history.append({"role": "user", "content": f"Read omnigent/{rel} and note anything relevant to LLM request building."})
        history.append({"role": "assistant", "content": f"Contents of omnigent/{rel}:\n```python\n{text}\n```"})
    history.append({"role": "user", "content": "Research is done. Next we fan out to sub-agents."})
    history.append({"role": "assistant", "content": "Ready to delegate."})
    return history


def tasks(n: int) -> dict[str, str]:
    topics = [
        "List every function in the Anthropic adapter that builds or mutates the request payload.",
        "Explain how streaming usage is reported by the Anthropic adapter.",
        "Describe how tool schemas are converted for Anthropic.",
        "Summarize how reasoning effort maps to thinking settings.",
        "Describe how the summarization prompt detects a prior summary.",
        "List the fields of AgentTool and what pass_history does.",
        "Explain how temperature is translated between OpenAI and Anthropic ranges.",
        "Describe the error handling in the Anthropic adapter's HTTP helpers.",
        "Describe how assistant tool calls are converted to Anthropic tool_use blocks.",
        "Explain the model metadata cache in the Anthropic adapter.",
    ]
    return {f"sub{i:02d}": f"Your task: {topics[i % len(topics)]} Answer in at most 150 words." for i in range(n)}


class Runner:
    def __init__(self, model: str, effort: str, max_tokens: int) -> None:
        self.adapter = adapter_mod.AnthropicAdapter()
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.conn = {"api_key": os.environ["ANTHROPIC_API_KEY"]}

    async def call(self, messages: list[dict[str, Any]], max_tokens: int | None = None) -> dict[str, Any]:
        extra = {"reasoning_effort": self.effort, "max_tokens": max_tokens or self.max_tokens}
        resp = await self.adapter.chat_completions(messages, self.model, None, False, extra, connection_params=self.conn)
        u = resp["usage"]
        return {
            "usage": {
                # Unpatched adapter only reports prompt/completion tokens; with
                # no cache_control there are no cache fields to report anyway.
                "input_tokens": u.get("prompt_tokens") or 0,
                "cache_creation_input_tokens": u.get("cache_creation_input_tokens", 0),
                "cache_read_input_tokens": u.get("cache_read_input_tokens", 0),
                "output_tokens": u.get("completion_tokens") or 0,
            },
            "text": resp["choices"][0]["message"]["content"] or "",
        }


async def run_arm(name: str, runner: Runner, build, task_map: dict[str, str], patched: bool, extra_calls=()) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    state = tempfile.mkdtemp(prefix=f"fanout_{name}_")
    os.environ["CACHEW_DIR"] = state
    if patched:
        patch.install()
    else:
        patch.uninstall()
    prewarms: list[dict[str, Any]] = []  # set in finally, so it survives a failed arm
    t0 = time.monotonic()
    try:
        results = await asyncio.gather(*(runner.call(build(t)) for t in task_map.values()))
    finally:
        prewarms = [u for f in Path(state).glob("*.prewarm.json") if "error" not in (u := json.loads(f.read_text()))]
        patch.uninstall()
        shutil.rmtree(state, ignore_errors=True)
    calls = [{"label": label, **r} for label, r in zip(task_map, results)]
    calls += [{"label": "prewarm", "usage": {**u, "output_tokens": 0}, "text": ""} for u in prewarms]
    calls += list(extra_calls)
    total = add_usage(*(c["usage"] for c in calls))
    return {
        "arm": name,
        "patched": patched,
        "wall_s": round(time.monotonic() - t0, 2),
        "calls": calls,
        "total_usage": total,
        "cost_usd": cost_usd(total, runner.model),
    }


def verify(arm: dict[str, Any], n: int) -> list[str]:
    """Assertions that caching actually worked in a patched arm."""
    subs = [c for c in arm["calls"] if c["label"].startswith("sub")]
    readers = [c for c in subs if c["usage"]["cache_read_input_tokens"] > 0]
    writes = [c for c in arm["calls"] if c["usage"]["cache_creation_input_tokens"] > 0]
    problems = []
    if len(readers) < n - 2:
        problems.append(f"{arm['arm']}: only {len(readers)}/{n} sub-agents read from cache")
    if len(writes) > 2:
        problems.append(f"{arm['arm']}: {len(writes)} cache writes (expected <= 2)")
    return problems


def report(results: dict[str, Any]) -> str:
    arms = results["arms"]
    base = arms["naive"]["cost_usd"]
    lines = [
        f"# Fan-out cache experiment — {results['model']}, {results['subagents']} sub-agents",
        "",
        f"Run at {results['timestamp']}. All token counts are the API's own `usage` fields.",
        "",
        "| Arm | uncached input | cache write | cache read | output | cost (USD) | vs naive |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name, a in arms.items():
        u = a["total_usage"]
        lines.append(
            f"| {name} | {u['input_tokens']:,} | {u['cache_creation_input_tokens']:,} | "
            f"{u['cache_read_input_tokens']:,} | {u['output_tokens']:,} | ${a['cost_usd']:.4f} | "
            f"{(1 - a['cost_usd'] / base) * 100:+.1f}% saved |"
        )
    lines += ["", "## Cache verification", ""]
    lines += [f"- FAIL: {p}" for p in results["verification_problems"]] or ["- PASS: every patched arm read the shared prefix from cache with at most 2 writes."]
    lines += ["", "## Per-call usage (compact_cache)", "", "| call | input | write | read | output |", "|---|---:|---:|---:|---:|"]
    for c in arms["compact_cache"]["calls"]:
        u = c["usage"]
        lines.append(f"| {c['label']} | {u['input_tokens']:,} | {u['cache_creation_input_tokens']:,} | {u['cache_read_input_tokens']:,} | {u['output_tokens']:,} |")
    return "\n".join(lines) + "\n"


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="claude-opus-5-5")
    ap.add_argument("--subagents", type=int, default=8)
    ap.add_argument("--effort", default="low")
    ap.add_argument("--max-tokens", type=int, default=1500)
    ap.add_argument("--brief-target", type=int, default=None, help="approx. brief size in tokens")
    ap.add_argument("--out", default=str(REPO / "results"))
    args = ap.parse_args()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set", file=sys.stderr)
        return 2

    patch.install_workspace_header()  # all arms, patched or not
    runner = Runner(args.model, args.effort, args.max_tokens)
    history = research_history()
    task_map = tasks(args.subagents)

    # One compaction, shared by both compact arms.
    patch.uninstall()
    compaction = await runner.call(compaction_messages(history, args.brief_target), max_tokens=12000)
    brief = compaction["text"]
    comp_call = {"label": "compaction", "usage": compaction["usage"], "text": ""}

    arms = {
        "naive": await run_arm("naive", runner, lambda t: pass_history_messages(history, t), task_map, False),
        "cache_only": await run_arm("cache_only", runner, lambda t: pass_history_messages(history, t), task_map, True),
        "compact_only": await run_arm("compact_only", runner, lambda t: subagent_messages(SYSTEM, brief, t), task_map, False, [comp_call]),
        "compact_cache": await run_arm("compact_cache", runner, lambda t: subagent_messages(SYSTEM, brief, t), task_map, True, [comp_call]),
    }
    problems = verify(arms["cache_only"], args.subagents) + verify(arms["compact_cache"], args.subagents)
    results = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "model": args.model,
        "subagents": args.subagents,
        "brief_chars": len(brief),
        "arms": arms,
        "verification_problems": problems,
    }
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "live_results.json").write_text(json.dumps(results, indent=2))
    (out / "live_report.md").write_text(report(results))
    print(report(results))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
