"""The ``cachew`` Omnigent harness: agent turns on Omnigent's own Anthropic adapter.

Omnigent's stock Claude harness drives the Claude CLI, which talks to the API
itself, so the adapter patch never sees an agent turn. This executor runs each
turn through ``omnigent.llms.Client`` instead: Omnigent still owns sessions,
the tool loop's tools, sub-agents (``sys_session_send``), child sessions and
usage accounting, and every request goes through the patched
``AnthropicAdapter`` (breakpoints, single-flight, cache usage).

Fan-out protocol (what ``pass_history`` would have done, but cacheable):

1. The orchestrator publishes the shared knowledge as a brief
   (``publish_brief``) and pre-warms it (``Turn.prewarm``): one
   ``max_tokens: 0`` request writes the cache entry for exactly the prefix
   every sub-agent will send.
2. It dispatches each sub-agent with ``sys_session_send`` and an input that
   starts with a ``dispatch_tag`` naming the brief.
3. The sub-agent's executor sees the tag and lays its request out as
   system / brief / acknowledgement / task (``compact.subagent_turns``), so
   all siblings share a byte-identical prefix and read it from cache.

Every call writes a record under ``<study>/calls/`` with its real usage, so a
UI can follow a run live and show what the cache saved.

The harness subprocess is configured by environment variables that the
plugin's spawn-env builder fills from the spec's ``executor`` block:
``HARNESS_CACHEW_MODEL``, ``..._EFFORT``, ``..._STUDY``, ``..._PROGRAM``
(``module:function`` that runs an orchestrator's turns), ``..._TOOLS``
(``0`` sends no tool schemas), ``..._MAX_TOKENS``.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

from cachew import patch
from cachew.compact import subagent_turns
from cachew.pricing import PRICES, add_usage, cost_usd, uncached_usd

REPO = Path(__file__).resolve().parent.parent
_ENV = "HARNESS_CACHEW_"
_TAG = re.compile(r"\A<<cachew (?P<attrs>[^>\n]*)>>\n?")
USAGE_KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")


# -- fan-out protocol: briefs, dispatch tags, call records ---------------------


def publish_brief(study: Path, stage: str, text: str) -> str:
    """Store a brief under ``briefs/`` and return its id (stage + content hash)."""
    bid = f"{stage}-{hashlib.sha256(text.encode()).hexdigest()[:10]}"
    path = Path(study) / "briefs" / f"{bid}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return bid


def load_brief(study: Path, bid: str) -> str:
    return (Path(study) / "briefs" / f"{bid}.md").read_text(encoding="utf-8")


def dispatch_tag(**attrs: str) -> str:
    """First line of a sub-agent's input: ``<<cachew brief=B-.. agent=.. out=..>>``."""
    return "<<cachew " + " ".join(f"{k}={v}" for k, v in attrs.items()) + ">>\n"


def parse_tag(text: str) -> tuple[dict[str, str], str]:
    m = _TAG.match(text)
    if not m:
        return {}, text
    attrs = dict(kv.split("=", 1) for kv in m.group("attrs").split() if "=" in kv)
    return attrs, text[m.end():]


def write_json(path: Path, data: Any) -> None:
    """Atomic write, so a reader polling the file never sees half of it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def record_call(study: Path | None, label: str, **fields: Any) -> dict[str, Any]:
    """Create or update ``calls/<label>.json``. Usage fields get $ and the uncached $ added."""
    if not study:
        return fields
    path = Path(study) / "calls" / f"{label}.json"
    rec = {**read_json(path, {"label": label}), **fields}
    if "usage" in fields and rec.get("model") in PRICES:
        rec["usd"] = round(cost_usd(rec["usage"], rec["model"]), 6)
        # A pre-warm only exists because of caching: without the cache it would not be sent.
        rec["usd_uncached"] = 0.0 if rec.get("kind") == "prewarm" else round(uncached_usd(rec["usage"], rec["model"]), 6)
    write_json(path, rec)
    return rec


def system_sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


# -- credentials ---------------------------------------------------------------


def api_key() -> str:
    """Key for Omnigent's adapter: harness env, then the process env, then the repo's git-ignored .env."""
    for name in (_ENV + "API_KEY", "ANTHROPIC_API_KEY"):
        if os.environ.get(name):
            return os.environ[name]
    found: dict[str, str] = {}
    env = REPO / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            k, _, v = line.partition("=")
            found[k.strip()] = v.strip().strip('"').strip("'")
    if found.get("ANTHROPIC_WORKSPACE_ID") and not os.environ.get("ANTHROPIC_WORKSPACE_ID"):
        os.environ["ANTHROPIC_WORKSPACE_ID"] = found["ANTHROPIC_WORKSPACE_ID"]
    if found.get("ANTHROPIC_API_KEY"):
        return found["ANTHROPIC_API_KEY"]
    raise RuntimeError("no Anthropic API key: set ANTHROPIC_API_KEY or add it to the repo's .env")


# -- one turn ------------------------------------------------------------------


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""


class Turn:
    """What a turn (or an orchestrator program) can do: call the model, pre-warm, call Omnigent tools."""

    def __init__(self, ex: CachewExecutor, model: str, system: str, tools: list[dict[str, Any]], session: str) -> None:
        self.ex, self.model, self.system, self.tools, self.session = ex, model, system, tools, session
        self.study = ex.study
        self.usage: list[dict[str, Any]] = []
        self.cost = 0.0

    def _add(self, usage: dict[str, Any]) -> dict[str, Any]:
        u = {k: int(usage.get(k) or 0) for k in USAGE_KEYS}
        self.usage.append(u)
        if self.model in PRICES:
            self.cost += cost_usd(u, self.model)
        return u

    async def llm(self, items: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None = None,
                  max_tokens: int | None = None, system: str | None = None) -> tuple[str, list[Any], dict[str, Any]]:
        """One model call through Omnigent's client. Returns (text, function calls, raw usage)."""
        from omnigent.llms.client import Client

        kwargs: dict[str, Any] = {"max_tokens": max_tokens or self.ex.max_tokens}
        with patch.capture_usage() as sink:
            resp = await Client().responses.create(
                input=items, instructions=self.system if system is None else system,
                model=f"anthropic/{self.model}", tools=tools or None,
                reasoning={"effort": self.ex.effort} if self.ex.effort else None,
                stream=False, connection_params={"api_key": self.ex.key}, **kwargs)
        usage = self._add(add_usage(*sink) if sink else {
            "input_tokens": getattr(resp.usage, "input_tokens", 0), "output_tokens": getattr(resp.usage, "output_tokens", 0)})
        text, calls = "", []
        for item in resp.output:
            if getattr(item, "type", "") == "function_call" or hasattr(item, "call_id"):
                calls.append(item)
            else:
                text += "".join(getattr(c, "text", "") for c in getattr(item, "content", []) or [])
        return text, calls, usage

    async def prewarm(self, turns: list[dict[str, Any]], system: str) -> dict[str, Any]:
        """Cache ``system`` + ``turns[:-1]`` (the shared prefix) before any sibling starts."""
        raw = await patch.prewarm([{"role": "system", "content": system}, *turns], self.model,
                                  api_key=self.ex.key, reasoning_effort=self.ex.effort or None)
        return self._add(raw)

    async def tool(self, name: str, args: dict[str, Any], call_id: str | None = None) -> Any:
        """Run an Omnigent tool (e.g. ``sys_session_send``) through the runner."""
        if self.ex._tool_executor is None:
            raise RuntimeError("no Omnigent tool executor bound to this harness")
        out = self.ex._tool_executor(name, args, call_id=call_id)
        return await out if isinstance(out, Awaitable) else out

    def usage_total(self) -> dict[str, Any]:
        u = add_usage(*self.usage) if self.usage else {k: 0 for k in USAGE_KEYS}
        cached = u["cache_read_input_tokens"] + u["cache_creation_input_tokens"]
        return {**u, "total_tokens": u["input_tokens"] + cached + u["output_tokens"],
                "context_tokens": (self.usage[-1]["input_tokens"] + self.usage[-1]["cache_read_input_tokens"]
                                   + self.usage[-1]["cache_creation_input_tokens"]) if self.usage else 0,
                "model": self.model, "cost_usd": round(self.cost, 6)}


Program = Callable[[Turn, list[dict[str, Any]]], Awaitable[str]]


# -- the executor --------------------------------------------------------------


def _executor_base() -> type:
    from omnigent.inner.executor import Executor

    return Executor


class CachewExecutor(_executor_base()):  # type: ignore[misc]
    """Omnigent executor: tool loop in-harness, every model call on Omnigent's patched adapter."""

    def __init__(self, *, model: str, effort: str = "", study: str = "", program: str = "",
                 use_tools: bool = True, max_tokens: int = 16000, max_steps: int = 40) -> None:
        self.model = model.split("/", 1)[-1]
        self.effort, self.max_tokens, self.max_steps, self.use_tools = effort, max_tokens, max_steps, use_tools
        self.study = Path(study) if study else None
        self.program: Program | None = None
        if program:
            mod, _, fn = program.partition(":")
            self.program = getattr(importlib.import_module(mod), fn)
        self.key = api_key()
        self._tool_executor: Any = None  # installed by Omnigent's ExecutorAdapter
        self._history: dict[str, tuple[int, list[dict[str, Any]]]] = {}
        patch.install()

    @classmethod
    def from_env(cls) -> CachewExecutor:
        e = os.environ.get
        return cls(model=e(_ENV + "MODEL") or "claude-opus-5-5", effort=e(_ENV + "EFFORT", ""),
                   study=e(_ENV + "STUDY", ""), program=e(_ENV + "PROGRAM", ""),
                   use_tools=e(_ENV + "TOOLS", "1") != "0", max_tokens=int(e(_ENV + "MAX_TOKENS") or 16000))

    def handles_tools_internally(self) -> bool:
        return True

    def supports_streaming(self) -> bool:
        return False

    async def run_turn(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], system_prompt: str,
                       config: Any = None) -> AsyncIterator[Any]:
        from omnigent.inner.executor import ExecutorError, TextChunk, TurnComplete

        model = getattr(config, "model", None) or self.model
        session = next((m.get("session_id") for m in messages if m.get("session_id")), "") or ""
        turn = Turn(self, model.split("/", 1)[-1], system_prompt or "", tools or [], session)
        try:
            text = await (self.program(turn, messages) if self.program else self._agent(turn, messages))
        except Exception as e:  # surfaced to Omnigent as a failed turn, with whatever was spent
            yield ExecutorError(message=f"cachew: {type(e).__name__}: {e}", retryable=False, usage=turn.usage_total())
            return
        if text:
            yield TextChunk(text=text)
        yield TurnComplete(response=text, usage=turn.usage_total())

    # -- a plain agent: tool loop, or a fan-out member when the input is tagged --

    async def _agent(self, turn: Turn, messages: list[dict[str, Any]]) -> str:
        first_user = next((m for m in messages if m.get("role") == "user"), None)
        attrs, task = parse_tag(_text_of(first_user.get("content"))) if first_user else ({}, "")
        if attrs.get("brief") and self.study:
            return await self._member(turn, attrs, task)
        # Omnigent hands over text messages only; keep the full transcript (tool calls
        # included) per session and append just the new user turns.
        seen, items = self._history.get(turn.session, (0, []))
        roles = ("user",) if items else ("user", "assistant")
        new = messages[seen:] if items else messages
        items.extend([{"role": m["role"], "content": _text_of(m.get("content"))} for m in new if m.get("role") in roles])
        schemas = [{"type": "function", "function": t.get("function", t)} for t in turn.tools] if self.use_tools else None
        text = ""
        for _ in range(self.max_steps):
            text, calls, _u = await turn.llm(items, tools=schemas)
            if not calls:
                break
            for c in calls:
                items.append({"type": "function_call", "call_id": c.call_id, "name": c.name, "arguments": c.arguments})
                try:
                    out = await turn.tool(c.name, json.loads(c.arguments or "{}"), call_id=c.call_id)
                except Exception as e:  # the model sees the failure and can recover
                    out = {"error": str(e)}
                items.append({"type": "function_call_output", "call_id": c.call_id,
                              "output": out if isinstance(out, str) else json.dumps(out)})
        items.append({"role": "assistant", "content": text})
        self._history[turn.session] = (len(messages) + 1, items)
        return text

    async def _member(self, turn: Turn, attrs: dict[str, str], task: str) -> str:
        """One fan-out sub-agent: system / brief / ack / task, so siblings share the cached prefix."""
        label = attrs.get("agent") or turn.session
        record_call(self.study, label, kind="member", status="thinking", started=time.time(), model=turn.model,
                    session=turn.session, brief=attrs["brief"], system_sha=system_sha(turn.system))
        try:
            text, _calls, usage = await turn.llm(subagent_turns(load_brief(self.study, attrs["brief"]), task))
        except Exception as e:
            record_call(self.study, label, status="error", error=str(e)[:500], finished=time.time())
            raise
        if out := attrs.get("out"):
            dest = (self.study / out).resolve()
            if self.study.resolve() in dest.parents:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(text, encoding="utf-8")
        record_call(self.study, label, status="done", finished=time.time(), usage=usage, out=attrs.get("out"),
                    chars=len(text))
        return text
