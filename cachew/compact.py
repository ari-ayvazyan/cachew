"""Compact a finished research phase once, then build sub-agent requests on it.

Each sub-agent request is laid out as

    system     parent system prompt                          ┐ shared, byte-identical
    user       <knowledge_brief> compacted research </...>   │ -> cached prefix
    assistant  acknowledgement                               ┘ <- breakpoint (messages[-2])
    user       per-subagent instructions                       <- unique, uncached

so the patched adapter's second breakpoint lands at the end of the brief.
"""

from __future__ import annotations

from typing import Any

COMPACT_INSTRUCTIONS = """\
You are compacting a finished research session so that several sub-agents can
each work from it independently. Write a dense knowledge brief that preserves
every fact, file path, identifier, number, decision and open question a
sub-agent could need. Drop chit-chat, dead ends, repeated tool output and
anything that was later superseded. Use terse headed sections and bullet
points. Do not address any single downstream task."""

_COMPACT_TRIGGER = "Write the knowledge brief now, following the system instructions."
_ACK = "Understood. I will treat the knowledge brief as ground truth and not redo the research."


def compaction_messages(history: list[dict[str, Any]], target_tokens: int | None = None) -> list[dict[str, Any]]:
    """Chat Completions messages for the one-off compaction call.

    ``target_tokens`` asks for a brief of roughly that size, e.g. to clear a
    model's minimum cacheable prefix (4096 tokens on Haiku 4.5).
    """
    body = [m for m in history if m["role"] != "system"]
    instructions = COMPACT_INSTRUCTIONS
    if target_tokens:
        instructions += f"\nAim for roughly {target_tokens} tokens; keep detail rather than cutting it."
    return [{"role": "system", "content": instructions}, *body, {"role": "user", "content": _COMPACT_TRIGGER}]


def subagent_turns(brief: str, task: str) -> list[dict[str, Any]]:
    """A sub-agent's turns after its system prompt: brief, acknowledgement, task.

    These are also valid Responses API input items, which is how an Omnigent
    harness sends them (the system prompt goes in ``instructions``).
    """
    return [
        {"role": "user", "content": f"<knowledge_brief>\n{brief}\n</knowledge_brief>"},
        {"role": "assistant", "content": _ACK},
        {"role": "user", "content": task},
    ]


def subagent_messages(system: str, brief: str, task: str) -> list[dict[str, Any]]:
    """Chat Completions messages for one sub-agent over the compacted brief."""
    return [{"role": "system", "content": system}, *subagent_turns(brief, task)]


def pass_history_messages(history: list[dict[str, Any]], task: str) -> list[dict[str, Any]]:
    """Baseline: what a ``pass_history: true`` sub-agent sends today —
    the whole raw parent history with the task appended."""
    return [*history, {"role": "user", "content": task}]
