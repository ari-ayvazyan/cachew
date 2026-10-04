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
from pydantic import BaseModel, Field

from cachew.pricing import MIN_CACHE_TOKENS, PRICES
from lab.studio import runs

DIST = Path(__file__).resolve().parent / "web" / "dist"
OMNIGENT_URL = "http://127.0.0.1:6767"
MODELS = [("claude-opus-5-5", "Claude Opus 5.5"), ("claude-sonnet-5-5", "Claude Sonnet 5.5"),
          ("claude-haiku-4-5", "Claude Haiku 4.5"), ("claude-fable-5-1", "Claude Fable 5.1")]


class NewRun(BaseModel):
    question: str = Field(min_length=10, max_length=4000)
    context: str = Field(default="", max_length=400_000)
    model: str = "claude-opus-5-5"
    effort: str = "low"
    width: int = Field(default=3, ge=1, le=6)
    budget_usd: float = Field(default=3.0, gt=0, le=50)


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
            "efforts": ["low", "medium", "high"], "omnigent_url": OMNIGENT_URL}


@app.get("/api/runs")
def list_runs() -> list[dict[str, Any]]:
    return runs.list_runs()


@app.post("/api/runs")
def create_run(req: NewRun) -> dict[str, Any]:
    if req.effort not in ("", "low", "medium", "high"):
        raise HTTPException(422, "effort must be low, medium or high")
    try:
        study = runs.start(req.question, req.context, req.model, req.effort, req.width, req.budget_usd)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return {"id": study.name}


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
