"""Live trace of every agent, via an Omnigent function policy that always allows.

Each agent in the generated bundle gets::

    guardrails:
      policies:
        trace:
          type: function
          function: {path: autolab.trace.make, arguments: {role: scout, log: <round>/trace.jsonl}}

Omnigent calls the evaluator on every phase of that agent's session:
``request`` (the task it was handed), ``llm_request`` / ``llm_response``
(each model round-trip, with usage), ``tool_call`` / ``tool_result`` and
``response`` (what it reports back). One JSON line per event lets the UI draw
who is working, who dispatched whom, and which files flow between agents.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

MAX_TEXT = 1500
_lock = threading.Lock()


def _short(x: Any, n: int = MAX_TEXT) -> Any:
    if isinstance(x, str):
        return x if len(x) <= n else x[:n] + f"…[{len(x)} chars]"
    if isinstance(x, dict):
        return {k: _short(v, n) for k, v in list(x.items())[:30]}
    if isinstance(x, (list, tuple)):
        return [_short(v, n) for v in list(x)[:30]]
    if isinstance(x, (int, float, bool)) or x is None:
        return x
    return _short(str(x), n)


def _record(role: str, event: dict[str, Any]) -> dict[str, Any]:
    kind = event.get("type")
    data = event.get("data")
    rec: dict[str, Any] = {"t": round(time.time(), 3), "role": role, "phase": kind}
    if kind in ("tool_call", "tool_result"):
        rec["tool"] = event.get("target")
        if kind == "tool_call" and isinstance(data, dict):
            rec["args"] = _short(data.get("arguments"))
        elif isinstance(data, dict):
            rec["result"] = _short(data.get("result"), 600)
            req = event.get("request_data")
            if isinstance(req, dict):
                rec["args"] = _short(req.get("arguments"), 300)
    elif kind == "request":
        text = data.get("user_content") if isinstance(data, dict) else data
        rec["text"] = _short(text)
    elif kind == "response":
        rec["text"] = _short(data)
    elif kind in ("llm_request", "llm_response") and isinstance(data, dict):
        rec.update({k: _short(data.get(k), 400) for k in ("model", "text_preview", "tool_calls_count", "usage",
                                                          "messages_count") if k in data})
    usage = (event.get("context") or {}).get("usage") if isinstance(event.get("context"), dict) else None
    if usage:
        rec["session_usage"] = _short(usage)
    return rec


def make(role: str, log: str):  # type: ignore[no-untyped-def]
    """Factory: returns the evaluator Omnigent calls for every event of one agent."""

    def evaluate(event: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            line = json.dumps(_record(role, event), ensure_ascii=False, default=str)
            with _lock:
                os.makedirs(os.path.dirname(log), exist_ok=True)
                with open(log, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
        except Exception:  # tracing must never block an agent
            pass
        return {"result": "ALLOW", "reason": None}

    return evaluate
