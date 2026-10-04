"""Spawn env for the harness subprocess, from the spec's ``executor`` block.

    executor:
      type: omnigent
      model: claude-opus-5-5
      reasoning_effort: low
      config: {harness: cachew, study: <dir>, program: <module:function>, tools: "0", max_tokens: "16000"}
"""

from __future__ import annotations

from typing import Any

_KEYS = ("study", "program", "tools", "max_tokens")


def build_spawn_env(spec: Any, *, cwd: Any = None, workdir: Any = None) -> dict[str, str]:
    from omnigent.spec.types import ApiKeyAuth

    ex = spec.executor
    cfg = dict(ex.config or {})
    env = {"HARNESS_CACHEW_MODEL": str(ex.model or cfg.get("model") or ""),
           "HARNESS_CACHEW_EFFORT": str(ex.reasoning_effort or cfg.get("effort") or "")}
    env.update({f"HARNESS_CACHEW_{k.upper()}": str(cfg[k]) for k in _KEYS if k in cfg})
    if isinstance(ex.auth, ApiKeyAuth) and ex.auth.api_key:
        env["HARNESS_CACHEW_API_KEY"] = ex.auth.api_key
    return env
