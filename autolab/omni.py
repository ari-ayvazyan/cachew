"""Run one Omnigent phase headless and show progress while it runs.

``omni run <bundle> --no-session -p <prompt>`` starts a throwaway Omnigent
server + runner, runs the PI until it and all sub-agents are idle, prints the
PI's final text and exits. Progress is shown by watching which study files
the agents write (they can write nowhere else).

``AUTOLAB_OMNI`` overrides the command (tests use a fake).
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

from autolab.study import Study

PHASE_TIMEOUT_S = 45 * 60  # omni itself gives up after 30 min of following a session


def omni_command() -> list[str]:
    if override := os.environ.get("AUTOLAB_OMNI"):
        return shlex.split(override)
    exe = Path(sys.executable).parent / "omni"
    return [str(exe) if exe.exists() else "omni"]


def _snapshot(root: Path) -> dict[str, float]:
    out = {}
    for p in root.rglob("*"):
        if p.is_file() and ".bundle" not in p.parts and "logs" not in p.parts and "__pycache__" not in p.parts and p.name != "trace.jsonl":
            out[p.relative_to(root).as_posix()] = p.stat().st_mtime
    return out


def run_phase(study: Study, bundle: Path, prompt: str, log_name: str, quiet: bool = False) -> tuple[int, str]:
    logs = study.rdir() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    n = len(list(logs.glob(f"{log_name}*.log"))) + 1
    log_path = logs / f"{log_name}-{n}.log"
    env = {**os.environ, "ENABLE_CLAUDEAI_MCP_SERVERS": "false", "AUTOLAB_STUDY": str(study.root)}
    cmd = omni_command() + ["run", str(bundle), "--no-session", "-p", prompt]
    t0 = time.monotonic()
    seen = _snapshot(study.root)
    with open(log_path, "w", encoding="utf-8") as log:
        log.write(f"# prompt\n{prompt}\n\n# omni output\n")
        log.flush()
        out_path = log_path.with_suffix(".reply")
        out_file = open(out_path, "w", encoding="utf-8")
        proc = subprocess.Popen(cmd, cwd=bundle, env=env, stdout=out_file, stderr=log, text=True)
        try:
            while proc.poll() is None:
                time.sleep(3)
                now = _snapshot(study.root)
                for rel, mt in sorted(now.items()):
                    if seen.get(rel) != mt and not quiet:
                        verb = "updated" if rel in seen else "wrote"
                        print(f"  [{time.monotonic() - t0:5.0f}s] {verb} {rel}", flush=True)
                seen = now
                if time.monotonic() - t0 > PHASE_TIMEOUT_S:
                    proc.kill()
                    break
        except KeyboardInterrupt:
            proc.kill()
            raise
        finally:
            rc = proc.wait()
            out_file.close()
        text = out_path.read_text(encoding="utf-8")
        out_path.unlink()
        log.write(f"\n# PI final reply (exit {rc}, {time.monotonic() - t0:.0f}s)\n{text}\n")
    study.log("phase", name=log_name, round=study.round, exit=rc, seconds=round(time.monotonic() - t0),
              log=log_path.relative_to(study.root).as_posix())
    return rc, text.strip()
