"""Claude calls through the same path as the Cachew experiment.

Omnigent's Anthropic adapter, with ``cachew.patch`` installed (prompt caching
plus the workspace header), priced by ``cachew.pricing``. This module adds only
two things the research loop needs: a response cache by prompt hash (reruns
cost nothing) and a hard spend cap. The key comes from the environment or the
repo's git-ignored ``.env`` and is never written anywhere.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from cachew import patch
from cachew.pricing import cost_usd
from lab.domains.dmg.data import CACHE

MODEL = "claude-haiku-4-5"
BUDGET_USD = 3.0
SPEND = CACHE / "spend.json"


class BudgetExceeded(RuntimeError):
    pass


def _load_env() -> None:
    env = Path(".env")
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        k, _, v = line.partition("=")
        if k.strip() in ("ANTHROPIC_API_KEY", "ANTHROPIC_WORKSPACE_ID") and not os.environ.get(k.strip()):
            os.environ[k.strip()] = v.strip().strip('"').strip("'")


def spent() -> float:
    return json.loads(SPEND.read_text())["usd"] if SPEND.exists() else 0.0


async def _call(system: str, user: str, max_tokens: int) -> dict[str, Any]:
    from omnigent.llms.adapters import anthropic as adapter_mod

    resp = await adapter_mod.AnthropicAdapter().chat_completions(
        [{"role": "system", "content": system}, {"role": "user", "content": user}], MODEL, None, False,
        {"max_tokens": max_tokens}, connection_params={"api_key": os.environ["ANTHROPIC_API_KEY"]})
    u = resp["usage"]
    usage = {"input_tokens": u.get("prompt_tokens") or 0, "cache_creation_input_tokens": u.get("cache_creation_input_tokens", 0),
             "cache_read_input_tokens": u.get("cache_read_input_tokens", 0), "output_tokens": u.get("completion_tokens") or 0}
    return {"text": resp["choices"][0]["message"]["content"] or "", "usage": usage}


def ask(system: str, user: str, max_tokens: int = 2000) -> dict[str, Any]:
    """Returns {text, usage, usd, cached}; usd is 0 for a cache hit."""
    key = hashlib.sha256(json.dumps([MODEL, system, user, max_tokens]).encode()).hexdigest()[:16]
    path = CACHE / "llm" / f"{key}.json"
    if path.exists():
        return {**json.loads(path.read_text(encoding="utf-8")), "usd": 0.0, "cached": True}
    if spent() >= BUDGET_USD:
        raise BudgetExceeded(f"spent ${spent():.4f} of ${BUDGET_USD}")
    _load_env()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY not set (environment or .env)")
    patch.install_workspace_header()
    patch.install()
    out = asyncio.run(_call(system, user, max_tokens))
    usd = cost_usd(out["usage"], MODEL)
    SPEND.parent.mkdir(parents=True, exist_ok=True)
    SPEND.write_text(json.dumps({"usd": round(spent() + usd, 6)}))
    out["model"] = MODEL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out), encoding="utf-8")
    return {**out, "usd": usd, "cached": False}


def parse_json(text: str) -> Any:
    """First JSON value in the text (models sometimes wrap it in a code fence)."""
    start = min((i for i in (text.find("["), text.find("{")) if i >= 0), default=-1)
    if start < 0:
        raise ValueError("no JSON in model output")
    return json.JSONDecoder().raw_decode(text[start:])[0]
