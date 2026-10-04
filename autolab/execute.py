"""Approve and run an experiment. The driver runs it, never an agent.

Approval freezes the experiment: approval.json stores the hash of
``experiment/`` and of ``predictions.json``. The run refuses to start if
either changed, and runs in a sandbox where the experiment dir is read-only
and only ``output/`` and the dataset cache are writable.
"""

from __future__ import annotations

import getpass
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from autolab import sandbox
from autolab.study import Study, StudyError, now, read_json, tree_hash, write_json

DATA_DIR = Path(os.environ.get("AUTOLAB_DATA", Path.home() / ".cache" / "autolab" / "data"))


def frozen_hashes(study: Study) -> dict[str, str]:
    r = study.rdir()
    return {"experiment": tree_hash(r / "experiment"), "predictions": tree_hash(r / "predictions.json")}


def approve(study: Study, note: str = "") -> dict[str, Any]:
    study.require("awaiting_approval")
    rec = {"approved_by": getpass.getuser(), "at": now(), "note": note, "hashes": frozen_hashes(study)}
    write_json(study.rdir() / "approval.json", rec)
    study.log("approved", round=study.round, **rec)
    return rec


def tampered(study: Study) -> list[str]:
    appr = read_json(study.rdir() / "approval.json")
    if appr is None:
        return ["no approval.json"]
    cur = frozen_hashes(study)
    return [k for k, h in appr["hashes"].items() if cur.get(k) != h]


def run(study: Study, echo: bool = True) -> dict[str, Any]:
    r = study.rdir()
    if bad := tampered(study):
        raise StudyError(f"refusing to run: {', '.join(bad)} changed since approval")
    out = r / "output"
    if out.exists():  # a previous attempt (e.g. interrupted); keep it for the record
        out.rename(r / f"output.prev-{int(time.time())}")
    out.mkdir(parents=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    limit_s = int(study.config["max_run_minutes"] * 60)
    cmd, kind = sandbox.wrap([sys.executable, "-u", "run.py", "--out", str(out)], writable=[out, DATA_DIR], readonly=[study.root])
    env = {**os.environ, "AUTOLAB_DATA": str(DATA_DIR), "PYTHONHASHSEED": "0", "MPLBACKEND": "Agg",
           "PYTHONDONTWRITEBYTECODE": "1"}
    study.log("run_start", round=study.round, sandbox=kind, limit_s=limit_s)
    t0 = time.monotonic()
    timed_out = threading.Event()
    with open(out / "stdout.log", "w") as so, open(out / "stderr.log", "w") as se:
        proc = subprocess.Popen(cmd, cwd=r / "experiment", env=env, stdout=subprocess.PIPE, stderr=se,
                                text=True, start_new_session=True)

        def kill() -> None:
            timed_out.set()
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

        watchdog = threading.Timer(limit_s, kill)
        watchdog.start()
        assert proc.stdout is not None
        try:
            for line in proc.stdout:  # stream progress to the terminal and the log
                so.write(line)
                so.flush()
                if echo:
                    print(f"  | {line}", end="", flush=True)
            proc.wait()
        except KeyboardInterrupt:
            kill()
            raise
        finally:
            watchdog.cancel()
            proc.stdout.close()
    wall = time.monotonic() - t0
    results = out / "results.json"
    valid = False
    if results.exists():
        try:
            json.loads(results.read_text(encoding="utf-8"))
            valid = True
        except json.JSONDecodeError:
            pass
    rec = {
        "exit_code": proc.returncode, "timed_out": timed_out.is_set(), "wall_seconds": round(wall, 1),
        "limit_seconds": limit_s, "sandbox": kind, "results_json": "present" if valid else (
            "invalid JSON" if results.exists() else "missing"),
        "hash_check": "ok (experiment/ and predictions.json unchanged since approval)",
        "hashes": frozen_hashes(study), "python": sys.version.split()[0], "finished": now(),
        "stderr_tail": (out / "stderr.log").read_text(errors="replace")[-3000:],
    }
    write_json(r / "run.json", rec)
    study.log("run_end", round=study.round, exit=proc.returncode, timed_out=timed_out.is_set(), seconds=round(wall))
    return rec
