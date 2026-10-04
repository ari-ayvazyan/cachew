"""Browser UI for autolab, the research loop (lab/) and the Cachew experiment.

    autolab ui            ->  http://127.0.0.1:8765

Every button runs the same CLI command the terminal would (``python -m
autolab plan <study>`` and so on) as a background job. State lives in the
study folders, so the UI and the CLI can be mixed freely, and restarting the
server loses nothing but the in-memory job list (logs stay on disk).

Only binds to localhost. State-changing requests must carry the
``X-Autolab: 1`` header, which a foreign web page cannot send without a CORS
preflight that this server never approves.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from autolab import activity, board, execute
from autolab.study import STATUSES, Study, StudyError, read_json, round_name

REPO = Path(__file__).resolve().parent.parent
WEB = Path(__file__).with_name("web")
DIST = WEB / "dist"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
STUCK = {"planning", "running", "analyzing"}

# status -> actions the UI offers (the CLI enforces the same rules)
ACTIONS: dict[str, list[str]] = {
    "ready": ["plan"],
    "plan_failed": ["plan"],
    "awaiting_approval": ["approve_run", "approve", "revise"],
    "approved": ["run"],
    "ran": ["analyze"],
    "analysis_failed": ["analyze"],
    "concluded": ["report", "reopen"],
}


@dataclass
class Job:
    id: str
    kind: str          # autolab | lab | cachew
    target: str        # study name, or "cachew"
    action: str
    cmd: list[str]
    log: Path
    started: float = field(default_factory=time.time)
    ended: float | None = None
    rc: int | None = None
    cancelled: bool = False
    proc: subprocess.Popen | None = None  # type: ignore[type-arg]

    @property
    def running(self) -> bool:
        return self.ended is None

    def public(self) -> dict[str, Any]:
        state = "running" if self.running else "cancelled" if self.cancelled else "done" if self.rc == 0 else "failed"
        return {"id": self.id, "kind": self.kind, "target": self.target, "action": self.action,
                "cmd": " ".join(self.cmd[2:]), "started": self.started, "ended": self.ended,
                "rc": self.rc, "state": state}


class Jobs:
    def __init__(self, log_dir: Path) -> None:
        self.log_dir = log_dir
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()

    def active(self, kind: str, target: str) -> Job | None:
        return next((j for j in self.jobs.values() if j.kind == kind and j.target == target and j.running), None)

    def latest(self, kind: str, target: str) -> Job | None:
        js = [j for j in self.jobs.values() if j.kind == kind and j.target == target]
        return max(js, key=lambda j: j.started) if js else None

    def start(self, kind: str, target: str, action: str, args: list[str]) -> Job:
        with self.lock:
            if self.active(kind, target):
                raise HTTPException(409, f"a job is already running for {target}")
            self.log_dir.mkdir(parents=True, exist_ok=True)
            jid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
            job = Job(jid, kind, target, action, [sys.executable, "-u", *args], self.log_dir / f"{jid}.log")
            out = open(job.log, "w", encoding="utf-8")
            out.write(f"$ {' '.join(args)}\n")
            out.flush()
            job.proc = subprocess.Popen(job.cmd, cwd=REPO, stdout=out, stderr=subprocess.STDOUT,
                                        start_new_session=True, env={**os.environ, "PYTHONUNBUFFERED": "1"})
            self.jobs[jid] = job
        threading.Thread(target=self._reap, args=(job, out), daemon=True).start()
        return job

    def _reap(self, job: Job, out: Any) -> None:
        assert job.proc is not None
        job.rc = job.proc.wait()
        out.write(f"\n[exit {job.rc}{' - cancelled' if job.cancelled else ''}]\n")
        out.close()
        job.ended = time.time()

    def cancel(self, jid: str) -> Job:
        job = self.jobs.get(jid)
        if job is None:
            raise HTTPException(404, "no such job")
        if job.running and job.proc is not None:
            job.cancelled = True
            # SIGINT first: the CLI turns KeyboardInterrupt into a clean status reset
            try:
                os.killpg(job.proc.pid, signal.SIGINT)
            except ProcessLookupError:
                pass

            def hard_kill() -> None:
                if job.running:
                    try:
                        os.killpg(job.proc.pid, signal.SIGKILL)  # type: ignore[union-attr]
                    except ProcessLookupError:
                        pass
            threading.Timer(20, hard_kill).start()
        return job


PHASE_CMDS = {"plan", "revise", "approve", "run", "analyze", "next"}


def phase_process_alive(root: Path) -> bool:
    return bool(phase_pids(root))


def phase_pids(root: Path) -> list[int]:
    """Is an autolab plan/run/analyze process working on this study right now?

    Checks every process on the machine (Linux /proc), so it also sees runs started from a
    terminal or by a previous server instance, which the in-memory job list doesn't know about.
    """
    root = root.resolve()
    me = os.getpid()
    found: list[int] = []
    for d in Path("/proc").iterdir():
        if not d.name.isdigit() or int(d.name) == me:
            continue
        try:
            args = (d / "cmdline").read_bytes().split(b"\0")
            argv = [a.decode(errors="replace") for a in args if a]
            if "autolab" not in " ".join(argv) or not PHASE_CMDS.intersection(argv):
                continue
            cwd = Path(os.readlink(d / "cwd"))
        except (OSError, ValueError):
            continue
        for a in argv:
            if "/" in a or a == root.name:
                try:
                    p = (cwd / a).resolve()
                except (OSError, RuntimeError):
                    continue
                if p == root:
                    found.append(int(d.name))
                    break
    return found


def read_text(p: Path) -> str:
    """UTF-8, falling back to cp1252 for files written on Windows (results/ came from a Windows run)."""
    data = p.read_bytes()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def create_app(studies: Path) -> FastAPI:
    studies = studies.resolve()
    studies.mkdir(parents=True, exist_ok=True)
    jobs = Jobs(studies / ".ui-jobs")
    app = FastAPI(title="autolab", docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def guard(request: Request, call_next):  # type: ignore[no-untyped-def]
        if request.method not in ("GET", "HEAD") and request.headers.get("x-autolab") != "1":
            return JSONResponse({"detail": "missing X-Autolab header"}, status_code=403)
        return await call_next(request)

    # -- helpers ---------------------------------------------------------------
    def study_dir(name: str) -> Path:
        if not NAME_RE.match(name):
            raise HTTPException(400, "invalid study name")
        return studies / name

    def autolab_study(name: str) -> Study:
        d = study_dir(name)
        meta = read_json(d / "study.json")
        if not meta or "config" not in meta:
            raise HTTPException(404, f"no autolab study named {name}")
        return Study(d)

    def busy(name: str, root: Path) -> bool:
        return jobs.active("autolab", name) is not None or phase_process_alive(root)

    def kind_of(d: Path) -> str | None:
        meta = read_json(d / "study.json")
        if not isinstance(meta, dict):
            return None
        return "autolab" if "config" in meta else "lab" if "domain" in meta else None

    def round_files(s: Study) -> dict[str, Any]:
        r = s.rdir()
        if s.round == 0 or not r.exists():
            return {}
        code = {}
        if (r / "experiment").exists():
            for f in sorted((r / "experiment").rglob("*")):
                if f.is_file() and "__pycache__" not in f.parts and f.stat().st_size < 200_000:
                    code[f.relative_to(r).as_posix()] = f.read_text(encoding="utf-8", errors="replace")
        txt = lambda p: p.read_text(encoding="utf-8", errors="replace") if p.exists() else None  # noqa: E731
        return {"round": round_name(s.round), "plan_md": txt(r / "plan.md"), "code": code,
                "selection": read_json(r / "selection.json"), "candidates": read_json(r / "candidates.json"),
                "predictions": read_json(r / "predictions.json"), "approval": read_json(r / "approval.json"),
                "run": read_json(r / "run.json"), "review_md": txt(r / "review.md"),
                "verdict": read_json(r / "verdict.json")}

    # -- pages -----------------------------------------------------------------
    if (DIST / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        if not (DIST / "index.html").exists():
            return ("<!doctype html><title>Autolab</title><p style='font-family:system-ui;padding:2rem'>"
                    "The UI isn't built. Run <code>npm --prefix autolab/web install &amp;&amp; npm --prefix autolab/web run build</code>, "
                    "or restart with <code>autolab ui</code>, which builds it when npm is available.</p>")
        return (DIST / "index.html").read_text(encoding="utf-8")

    @app.get("/files/{path:path}")
    def files(path: str):  # type: ignore[no-untyped-def]
        """Read-only access to study folders and results/ (boards, reports, logs, code)."""
        base, rel = (REPO / "results", path[len("results/"):]) if path.startswith("results/") else (studies, path)
        p = (base / rel).resolve()
        if base not in p.parents or not p.is_file():
            raise HTTPException(404, "not found")
        if p.suffix == ".html":
            return FileResponse(p, media_type="text/html")
        return PlainTextResponse(read_text(p))

    # -- environment -----------------------------------------------------------
    @app.get("/api/env")
    def env() -> dict[str, Any]:
        from autolab import sandbox
        creds = Path.home() / ".claude" / ".credentials.json"
        return {"studies_dir": str(studies), "sandbox": sandbox.available(),
                "claude_login": creds.exists(), "api_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
                "statuses": STATUSES, "cpus": os.cpu_count()}

    # -- autolab studies --------------------------------------------------------
    @app.get("/api/studies")
    def list_studies() -> list[dict[str, Any]]:
        out = []
        for d in sorted(studies.iterdir()):
            if d.is_dir() and kind_of(d) == "autolab":
                m = read_json(d / "study.json")
                out.append({"name": d.name, "question": m["question"], "status": m["status"], "round": m["round"],
                            "max_rounds": m["config"]["max_rounds"],
                            "busy": m["status"] in STUCK and busy(d.name, d),
                            "updated": (d / "study.json").stat().st_mtime})
        return sorted(out, key=lambda x: -x["updated"])

    @app.post("/api/studies")
    async def new_study(request: Request) -> dict[str, Any]:
        b = await request.json()
        name, question = (b.get("name") or "").strip(), (b.get("question") or "").strip()
        if not NAME_RE.match(name):
            raise HTTPException(400, "name: letters, digits, '.', '_' or '-', up to 64 characters")
        if len(question) < 10:
            raise HTTPException(400, "write the research question as a full sentence")
        if (studies / name).exists():
            raise HTTPException(409, f"{name} already exists")
        models = {"pi": b["pi_model"]} if b.get("pi_model") else {}
        s = Study.create(studies / name, question, context=(b.get("context") or "").strip(),
                         model=b.get("model") or None, models=models,
                         max_run_minutes=float(b["max_run_minutes"]) if b.get("max_run_minutes") else None,
                         max_rounds=int(b["max_rounds"]) if b.get("max_rounds") else None,
                         fanout={k: int(b["fanout"]) for k in ("scout", "theorist", "skeptic")} if b.get("fanout") else None)
        board.render(s)
        return {"name": name}

    @app.get("/api/studies/{name}")
    def get_study(name: str) -> dict[str, Any]:
        s = autolab_study(name)
        data = board._data(s)
        job = jobs.latest("autolab", name)
        is_busy = busy(name, s.root)
        actions = [] if is_busy else list(ACTIONS.get(s.status, []))
        if not is_busy and s.status in STUCK:
            actions = ["recover"]
        return {**data, "name": name, "pending": round_files(s), "actions": actions, "busy": is_busy,
                "job": job.public() if job else None,
                "tampered": execute.tampered(s) if (s.rdir() / "approval.json").exists() and s.round else []}

    @app.get("/api/studies/{name}/activity")
    def get_activity(name: str, round: int | None = None) -> dict[str, Any]:
        s = autolab_study(name)
        live = (round is None or round == s.round) and busy(name, s.root)
        return activity.build(s, round, job_running=live)

    @app.get("/api/studies/{name}/tree")
    def tree(name: str) -> list[dict[str, Any]]:
        s = autolab_study(name)
        out = []
        for p in sorted(s.root.rglob("*")):
            rel = p.relative_to(s.root)
            if p.is_file() and rel.parts[0] != ".bundle" and "__pycache__" not in rel.parts and not p.name.endswith(".tmp"):
                out.append({"path": rel.as_posix(), "size": p.stat().st_size})
        return out

    @app.post("/api/studies/{name}/stop")
    def stop(name: str) -> dict[str, Any]:
        """Stop the running phase, whoever started it (SIGINT: the CLI resets the status cleanly)."""
        s = autolab_study(name)
        job = jobs.active("autolab", name)
        if job is not None:
            jobs.cancel(job.id)
            return {"stopped": [job.proc.pid if job.proc else None]}
        pids = phase_pids(s.root)
        if not pids:
            raise HTTPException(409, "nothing is running for this study")
        for pid in pids:
            try:
                os.kill(pid, signal.SIGINT)
            except ProcessLookupError:
                pass
        return {"stopped": pids}

    @app.post("/api/studies/{name}/{action}")
    async def act(name: str, action: str, request: Request) -> dict[str, Any]:
        s = autolab_study(name)
        b = await request.json() if (await request.body()) else {}
        if busy(name, s.root):
            raise HTTPException(409, "a phase is already running for this study")
        allowed = ["recover"] if s.status in STUCK else ACTIONS.get(s.status, [])
        if action not in allowed:
            raise HTTPException(409, f"'{action}' is not possible while the study is '{s.status}'")
        path = str(s.root)
        if action == "revise":
            fb = (b.get("feedback") or "").strip()
            if not fb:
                raise HTTPException(400, "write the feedback for the agents")
            args = ["-m", "autolab", "revise", path, fb]
        elif action in ("approve", "approve_run"):
            args = ["-m", "autolab", "approve", path] + (["--run"] if action == "approve_run" else [])
            if note := (b.get("note") or "").strip():
                args += ["--note", note]
        else:
            args = ["-m", "autolab", action, path]
        if action in ("recover", "reopen", "report"):  # instant: run inline
            res = subprocess.run([sys.executable, *args], cwd=REPO, capture_output=True, text=True)
            if res.returncode:
                raise HTTPException(409, (res.stderr or res.stdout).strip())
            return {"ok": True, "output": res.stdout}
        job = jobs.start("autolab", name, action, args)
        return {"job": job.public()}

    # -- jobs ----------------------------------------------------------------
    @app.get("/api/jobs/{jid}")
    def job_log(jid: str, offset: int = 0) -> dict[str, Any]:
        job = jobs.jobs.get(jid)
        if job is None:
            raise HTTPException(404, "no such job")
        with open(job.log, "rb") as f:
            f.seek(offset)
            chunk = f.read(200_000)
        return {**job.public(), "text": chunk.decode("utf-8", errors="replace"), "offset": offset + len(chunk)}

    @app.post("/api/jobs/{jid}/cancel")
    def cancel(jid: str) -> dict[str, Any]:
        return jobs.cancel(jid).public()

    # -- research loop (lab/) --------------------------------------------------
    @app.get("/api/lab")
    def lab_list() -> dict[str, Any]:
        out = []
        for d in sorted(studies.iterdir()):
            if d.is_dir() and kind_of(d) == "lab":
                m = read_json(d / "study.json")
                job = jobs.latest("lab", d.name)
                out.append({"name": d.name, "question": m.get("question"), "status": m.get("status"),
                            "round": m.get("round"), "board": (d / "board.html").exists(),
                            "job": job.public() if job else None})
        running = [j.public() for j in jobs.jobs.values() if j.kind == "lab" and j.running]
        return {"studies": out, "running": running}

    @app.post("/api/lab")
    async def lab_run(request: Request) -> dict[str, Any]:
        b = await request.json()
        name = (b.get("name") or "").strip()
        if not NAME_RE.match(name):
            raise HTTPException(400, "invalid study name")
        args = ["-m", "lab", "--study", str(studies / name), "--max-rounds", str(int(b.get("max_rounds") or 8)),
                "--pace", str(float(b.get("pace") or 0))]
        if b.get("fresh"):
            args.append("--fresh")
        elif (studies / name).exists():
            raise HTTPException(409, f"{name} exists; tick 'overwrite' to rerun it")
        return {"job": jobs.start("lab", name, "run", args).public()}

    # -- Cachew live experiment -----------------------------------------------
    @app.get("/api/cachew")
    def cachew_state() -> dict[str, Any]:
        rep = REPO / "results" / "live_report.md"
        job = jobs.latest("cachew", "cachew")
        return {"api_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
                "report": read_text(rep) if rep.exists() else None,
                "report_updated": rep.stat().st_mtime if rep.exists() else None,
                "job": job.public() if job else None}

    @app.post("/api/cachew")
    async def cachew_run(request: Request) -> dict[str, Any]:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise HTTPException(409, "set ANTHROPIC_API_KEY before starting the UI; the experiment calls the API directly")
        b = await request.json()
        args = ["-m", "cachew.experiment", "--model", str(b.get("model") or "claude-haiku-4-5"),
                "--subagents", str(int(b.get("subagents") or 8)), "--effort", str(b.get("effort") or ""),
                "--max-tokens", str(int(b.get("max_tokens") or 600))]
        if b.get("brief_target"):
            args += ["--brief-target", str(int(b["brief_target"]))]
        return {"job": jobs.start("cachew", "cachew", "experiment", args).public()}

    return app


def build_ui() -> None:
    """Build the React UI (autolab/web) if it hasn't been built and npm is available."""
    import shutil as _sh
    if (DIST / "index.html").exists():
        return
    npm = _sh.which("npm")
    if npm is None:
        print("autolab: the UI isn't built and npm isn't installed; see autolab/web/README.md")
        return
    print("Building the UI (first run only) ...")
    if not (WEB / "node_modules").exists():
        subprocess.run([npm, "install", "--no-audit", "--no-fund"], cwd=WEB, check=True)
    subprocess.run([npm, "run", "build"], cwd=WEB, check=True)


def serve(port: int = 8765, studies: Path = Path("studies")) -> None:
    import uvicorn

    build_ui()

    app = create_app(studies)
    print(f"autolab UI: http://127.0.0.1:{port}  (studies in {studies.resolve()}; Ctrl+C to stop)")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
