"""Cachew Studio: start research runs on Omnigent and watch the team and the cache live.

    .venv/Scripts/python.exe -m lab.studio            # http://127.0.0.1:8787

The UI is a static bundle in ``web/dist`` (``bun run build`` in ``web/``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, SecretStr

from cachew.harness import api_key
from cachew.pricing import MIN_CACHE_TOKENS, PRICES
from lab.studio import runs, team

DIST = Path(__file__).resolve().parent / "web" / "dist"
OMNIGENT_URL = "http://127.0.0.1:6767"
MODELS = [("claude-opus-5-5", "Claude Opus 5.5"), ("claude-sonnet-5-5", "Claude Sonnet 5.5"),
          ("claude-haiku-4-5", "Claude Haiku 4.5"), ("claude-fable-5-1", "Claude Fable 5.1")]
EFFORTS = ["low", "medium", "high"]

# On the Studio's shared key: the cheaper models, low effort, a small team and a small budget.
# With their own Anthropic key a scientist may use every model and effort and any team size.
SHARED_TIER = {"models": {"claude-sonnet-5-5": ["low"], "claude-haiku-4-5": [""]}, "max_width": 8, "max_budget_usd": 3.0}
OWN_TIER = {"models": {m: ([""] if m == "claude-haiku-4-5" else EFFORTS) for m, _ in MODELS}, "max_width": None,
            "max_budget_usd": 1000.0}


class NewRun(BaseModel):
    question: str = Field(min_length=10, max_length=4000)
    context: str = Field(default="", max_length=400_000)
    model: str = "claude-sonnet-5-5"
    effort: str = "low"
    width: int = Field(default=3, ge=1)
    budget_usd: float = Field(default=3.0, gt=0, le=OWN_TIER["max_budget_usd"])
    approval: bool = True
    api_key: SecretStr | None = Field(default=None, max_length=400)


class Approval(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")
    test: str = Field(default="", max_length=4000)
    note: str = Field(default="", max_length=2000)


def has_shared_key() -> bool:
    try:
        api_key()
    except RuntimeError:
        return False
    return True


def check_tier(req: NewRun, own: bool) -> None:
    """Reject what the run's key does not allow, with a message the form can show as is."""
    tier = OWN_TIER if own else SHARED_TIER
    allowed = tier["models"].get(req.model)
    if req.model not in PRICES:
        raise HTTPException(422, f"unknown model {req.model}")
    if allowed is None:
        raise HTTPException(422, f"{dict(MODELS)[req.model]} needs your own Anthropic API key")
    effort = "" if req.model == "claude-haiku-4-5" else req.effort
    if effort not in allowed:
        raise HTTPException(422, f"effort {req.effort} on {dict(MODELS)[req.model]} needs your own Anthropic API key")
    if tier["max_width"] and req.width > tier["max_width"]:
        raise HTTPException(422, f"more than {tier['max_width']} agents per swarm needs your own Anthropic API key")
    if req.budget_usd > tier["max_budget_usd"]:
        raise HTTPException(422, f"a budget above ${tier['max_budget_usd']:g} needs your own Anthropic API key")


def _study(run_id: str) -> Path:
    study = (runs.STUDIES / run_id).resolve()
    if study.parent != runs.STUDIES.resolve() or not (study / "run.json").exists():
        raise HTTPException(404, f"no run {run_id}")
    return study


app = FastAPI(title="Cachew Studio")


@app.get("/api/config")
def config() -> dict[str, Any]:
    return {"models": [{"id": m, "name": n, "input": PRICES[m].input, "output": PRICES[m].output,
                        "cache_read": PRICES[m].cache_read, "cache_write": PRICES[m].cache_write(),
                        "min_cache_tokens": MIN_CACHE_TOKENS.get(m)} for m, n in MODELS],
            "efforts": EFFORTS, "omnigent_url": OMNIGENT_URL, "shared_key": has_shared_key(),
            "tiers": {"shared": SHARED_TIER, "own": OWN_TIER}, "roles": team.ROLE_SPECS,
            "approval_timeout_s": team.APPROVAL_TIMEOUT_S}


@app.get("/api/runs")
def list_runs() -> list[dict[str, Any]]:
    return runs.list_runs()


@app.post("/api/runs")
def create_run(req: NewRun) -> dict[str, Any]:
    key = req.api_key.get_secret_value().strip() if req.api_key else ""
    if not key and not has_shared_key():
        raise HTTPException(422, "This Studio has no shared key: add your own Anthropic API key")
    check_tier(req, own=bool(key))
    try:
        if key:
            runs.check_key(key)
        study = runs.start(req.question, req.context, req.model, req.effort, req.width, req.budget_usd,
                           approval=req.approval, api_key=key)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return {"id": study.name}


@app.post("/api/runs/{run_id}/approval")
def approve_run(run_id: str, req: Approval) -> dict[str, Any]:
    try:
        return runs.approve(_study(run_id), req.decision, req.test, req.note)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    return runs.view(_study(run_id))


@app.get("/api/runs/{run_id}/file", response_class=PlainTextResponse)
def get_file(run_id: str, path: str) -> str:
    try:
        return runs.read_file(_study(run_id), path)
    except FileNotFoundError as e:
        raise HTTPException(404, f"no file {path}") from e


@app.post("/api/runs/{run_id}/stop")
def stop_run(run_id: str) -> dict[str, str]:
    runs.stop(_study(run_id))
    return {"status": "stopped"}


if DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=DIST), name="assets")


@app.get("/")
def index() -> FileResponse:
    if not (DIST / "index.html").exists():
        raise HTTPException(503, "UI not built: run `bun install && bun run build` in lab/studio/web")
    return FileResponse(DIST / "index.html", headers={"Cache-Control": "no-cache"})
