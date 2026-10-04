"""Who may write what, and when. Enforced by the study MCP server, not by prompts.

Agents have no shell and no native file tools; the only way they can change
the study is ``write_file`` on the MCP server, which checks these rules. That
is what makes pre-registration and the approval gate real: in the analyze
phase nobody can touch the experiment or the predictions, and in the plan
phase nobody can run code.

Fan-out: scouts, proposing theorists and skeptics run as N parallel instances
(``scout_1``, ``theorist_2``, ``skeptic_3`` ...). Each instance is its own
Omnigent agent with its own door, and may only write its own files
(``{me}`` below), so parallel instances can't overwrite each other. The
plain ``theorist`` is the lead who merges the proposals and writes the
predictions.
"""

from __future__ import annotations

import re
from fnmatch import fnmatch

FANOUT_KINDS = ("scout", "theorist", "skeptic")
INSTANCE = re.compile(r"^(scout|theorist|skeptic)_(\d+)$")

# kind -> phase -> write globs relative to the study root; {R} = round dir, {me} = the instance's role name
WRITES: dict[str, dict[str, list[str]]] = {
    "pi": {"plan": ["rounds/{R}/selection.json", "rounds/{R}/pi_notes.md"],
           "analyze": ["rounds/{R}/pi_notes.md"]},
    "scout_k": {"plan": ["literature/{R}-{me}.md"], "analyze": []},
    "theorist_k": {"plan": ["rounds/{R}/proposals/{me}.json"], "analyze": []},
    "theorist": {"plan": ["rounds/{R}/candidates.json", "rounds/{R}/predictions.json"], "analyze": []},
    "experimenter": {"plan": ["rounds/{R}/plan.md", "rounds/{R}/experiment/*"], "analyze": []},
    "skeptic_k": {"plan": [], "analyze": ["rounds/{R}/reviews/{me}.json", "rounds/{R}/reviews/{me}.md",
                                          "rounds/{R}/analysis/{me}/*"]},
    "judge": {"plan": [], "analyze": ["rounds/{R}/verdict.json", "findings.md"]},
}

# extra tools beyond read/list/write
TOOLS: dict[str, set[str]] = {
    "experimenter": {"check_syntax"},
    "skeptic_k": {"run_analysis"},
}

# files nobody may read through the tools (driver internals with no research value)
HIDDEN = [".bundle/*", "*.tmp", "rounds/*/trace.jsonl"]

# The angle each parallel instance takes, so instances diverge instead of duplicating each other.
ANGLES: dict[str, list[tuple[str, str]]] = {
    "scout": [
        ("prior results", "Find prior EMPIRICAL results on this exact question or the closest settings: who measured what, with which numbers."),
        ("methods", "Find the standard METHODS: datasets, model sizes, metrics, protocols and known pitfalls for measuring this on small models."),
        ("counter-evidence", "Find COUNTER-EVIDENCE: failed replications, contested claims, conditions under which the expected effect disappears or reverses."),
        ("theory", "Find THEORY: mechanisms and analytical arguments that predict when and why the effect should appear."),
    ],
    "theorist": [
        ("mechanism", "Think MECHANISM-FIRST: propose hypotheses that name a concrete causal mechanism and the regime where it should dominate."),
        ("null and confounds", "Think like a SKEPTIC: propose null hypotheses and confound-based explanations (noise, tuning budget, data size, metric artifacts) that could produce the same observations."),
        ("literature", "Think LITERATURE-DRIVEN: turn the scouts' findings into hypotheses, especially contested or untested claims, and say which source each comes from."),
        ("boundary conditions", "Think about BOUNDARY CONDITIONS: hypotheses about where the effect flips sign or vanishes (scale, data size, training length)."),
    ],
    "skeptic": [
        ("numbers", "Focus on NUMBERS: recompute every headline number from the raw per-run records with run_analysis and compare to what results.json claims."),
        ("code vs plan", "Focus on the CODE: does experiment/ implement plan.md exactly (conditions, seeds, metric, train/test separation)? Look for bugs and leakage."),
        ("statistics", "Focus on STATISTICS and CONFOUNDS: effect sizes vs seed noise, confidence intervals, multiple comparisons, and alternative explanations."),
        ("run integrity", "Focus on RUN INTEGRITY: run.json, stderr, timeouts, skipped or partial runs, and whether partial results bias the conclusion."),
    ],
}


def kind(role: str) -> str:
    """'scout_2' -> 'scout_k'; 'theorist' -> 'theorist' (the lead)."""
    m = INSTANCE.match(role)
    return f"{m.group(1)}_k" if m else role


def valid(role: str) -> bool:
    return kind(role) in WRITES


def angle(role: str) -> tuple[str, str] | None:
    m = INSTANCE.match(role)
    if not m:
        return None
    options = ANGLES[m.group(1)]
    return options[(int(m.group(2)) - 1) % len(options)]


def team(fanout: dict[str, int]) -> list[str]:
    """Sub-agent role names for a study, in pipeline order."""
    n = lambda k: max(1, int(fanout.get(k, 3)))  # noqa: E731
    return ([f"scout_{i}" for i in range(1, n("scout") + 1)]
            + ["theorist"] + [f"theorist_{i}" for i in range(1, n("theorist") + 1)]
            + ["experimenter"]
            + [f"skeptic_{i}" for i in range(1, n("skeptic") + 1)]
            + ["judge"])


def _globs(role: str, phase: str, round_dir: str) -> list[str]:
    return [g.replace("{R}", round_dir).replace("{me}", role) for g in WRITES.get(kind(role), {}).get(phase, [])]


def may_write(role: str, phase: str, rel: str, round_dir: str) -> bool:
    return any(fnmatch(rel, g) for g in _globs(role, phase, round_dir))


def may_read(rel: str) -> bool:
    return not any(fnmatch(rel, g) for g in HIDDEN)


def tools(role: str) -> set[str]:
    return TOOLS.get(kind(role), set())


def describe(role: str, phase: str, round_dir: str) -> str:
    globs = _globs(role, phase, round_dir)
    return ", ".join(globs) if globs else "nothing (read-only in this phase)"
