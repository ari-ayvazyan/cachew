"""Shared plumbing for the DMG domain's Claude calls: key loading and a hard spend cap.

Calls go through the same path as the Cachew experiment (Omnigent's Anthropic
adapter with ``cachew.patch``). The key comes from the environment or the
repo's git-ignored ``.env`` and is never written anywhere.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from lab.domains.dmg.data import CACHE

MODEL = "claude-haiku-4-5"
BUDGET_USD = 3.0
SPEND = CACHE / "spend.json"


class BudgetExceeded(RuntimeError):
    pass


def load_env() -> None:
    env = Path(".env")
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            k, _, v = line.partition("=")
            if k.strip() in ("ANTHROPIC_API_KEY", "ANTHROPIC_WORKSPACE_ID") and not os.environ.get(k.strip()):
                os.environ[k.strip()] = v.strip().strip('"').strip("'")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY not set (environment or .env)")


def spent() -> float:
    return json.loads(SPEND.read_text())["usd"] if SPEND.exists() else 0.0


def record(usd: float) -> None:
    SPEND.parent.mkdir(parents=True, exist_ok=True)
    SPEND.write_text(json.dumps({"usd": round(spent() + usd, 6)}))


def ask(system: str, user: str, max_tokens: int = 2000) -> dict[str, Any]:
    """One call (no fan-out): returns {text, usage, usd}."""
    from cachew import patch
    from cachew.experiment import Runner
    from cachew.pricing import cost_usd

    if spent() >= BUDGET_USD:
        raise BudgetExceeded(f"spent ${spent():.4f} of ${BUDGET_USD}")
    load_env()
    patch.install_workspace_header()
    out = asyncio.run(Runner(MODEL, "", max_tokens).call([{"role": "system", "content": system}, {"role": "user", "content": user}]))
    usd = cost_usd(out["usage"], MODEL)
    record(usd)
    return {**out, "usd": usd}


def parse_json(text: str) -> Any:
    """First JSON value in the text (models sometimes wrap it in a code fence)."""
    start = min((i for i in (text.find("["), text.find("{")) if i >= 0), default=-1)
    if start < 0:
        raise ValueError("no JSON in model output")
    return json.JSONDecoder().raw_decode(text[start:])[0]
