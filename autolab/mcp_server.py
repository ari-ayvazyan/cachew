"""Study MCP server: the only door agents have into a study.

One stdio process per agent, configured by environment variables that the
generated bundle sets (see ``bundle.py``):

    AUTOLAB_STUDY   absolute study root
    AUTOLAB_ROLE    pi | scout | theorist | experimenter | skeptic | judge
    AUTOLAB_PHASE   plan | analyze
    AUTOLAB_ROUND   e.g. R03
    AUTOLAB_PYTHON  interpreter used for check_syntax / run_analysis

Run as a script (``python autolab/mcp_server.py``) so it doesn't depend on
the agent's working directory or PYTHONPATH.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autolab import roles, sandbox  # noqa: E402

MAX_READ = 60_000
MAX_WRITE = 400_000
ANALYSIS_TIMEOUT_S = 600


class Denied(Exception):
    pass


class Door:
    """Role-checked file access to one study. Plain methods so tests can call them directly."""

    def __init__(self, root: Path, role: str, phase: str, round_dir: str, python: str = sys.executable) -> None:
        if not roles.valid(role):
            raise ValueError(f"unknown role {role!r}")
        self.root, self.role, self.phase, self.round_dir, self.python = root.resolve(), role, phase, round_dir, python

    @classmethod
    def from_env(cls) -> "Door":
        e = os.environ
        return cls(Path(e["AUTOLAB_STUDY"]), e["AUTOLAB_ROLE"], e["AUTOLAB_PHASE"], e["AUTOLAB_ROUND"],
                   e.get("AUTOLAB_PYTHON", sys.executable))

    def _resolve(self, path: str) -> tuple[Path, str]:
        p = (self.root / path).resolve()
        if p != self.root and self.root not in p.parents:
            raise Denied(f"{path!r} is outside the study")
        rel = p.relative_to(self.root).as_posix() if p != self.root else "."
        return p, rel

    # -- tools -------------------------------------------------------------
    def brief(self) -> str:
        meta = json.loads((self.root / "study.json").read_text(encoding="utf-8"))
        hyps = json.loads((self.root / "hypotheses.json").read_text(encoding="utf-8"))
        extra = sorted(roles.tools(self.role))
        ang = roles.angle(self.role)
        return json.dumps({
            "question": meta["question"], "context": meta.get("context", ""),
            "round": self.round_dir, "phase": self.phase, "your_role": self.role,
            "you_may_write": roles.describe(self.role, self.phase, self.round_dir),
            "your_extra_tools": extra, "your_angle": ang[1] if ang else None,
            "hardware": meta["config"].get("hardware"),
            "max_run_minutes": meta["config"].get("max_run_minutes"),
            "hypothesis_ledger": hyps,
        }, indent=2)

    def read(self, path: str, offset: int = 0) -> str:
        p, rel = self._resolve(path)
        if not roles.may_read(rel):
            raise Denied(f"{rel} is not readable")
        if p.is_dir():
            raise Denied(f"{rel} is a directory; use list_files")
        text = p.read_text(encoding="utf-8", errors="replace")
        chunk = text[offset: offset + MAX_READ]
        if offset + MAX_READ < len(text):
            chunk += f"\n...[truncated: {len(text)} chars total; call again with offset={offset + MAX_READ}]"
        return chunk

    def list(self, path: str = ".") -> str:
        p, rel = self._resolve(path)
        if not p.is_dir():
            raise Denied(f"{rel} is not a directory")
        out = []
        for f in sorted(p.rglob("*")):
            r = f.relative_to(self.root).as_posix()
            if f.is_file() and roles.may_read(r) and "__pycache__" not in f.parts:
                out.append(f"{r}  ({f.stat().st_size} B)")
        return "\n".join(out[:500]) or "(empty)"

    def write(self, path: str, content: str) -> str:
        p, rel = self._resolve(path)
        if not roles.may_write(self.role, self.phase, rel, self.round_dir):
            raise Denied(f"role {self.role} may not write {rel} in the {self.phase} phase; "
                         f"allowed: {roles.describe(self.role, self.phase, self.round_dir)}")
        if len(content) > MAX_WRITE:
            raise Denied(f"content is {len(content)} chars; limit {MAX_WRITE}")
        if rel.endswith(".json"):
            try:
                json.loads(content)
            except json.JSONDecodeError as exc:
                raise Denied(f"{rel} must be valid JSON: {exc}") from exc
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"wrote {rel} ({len(content)} chars)"

    def check_syntax(self, path: str) -> str:
        """Compile (never execute) a Python file the experimenter wrote."""
        self._need("check_syntax")
        p, rel = self._resolve(path)
        try:
            compile(p.read_text(encoding="utf-8"), rel, "exec")
        except SyntaxError as exc:
            return f"SyntaxError in {rel} line {exc.lineno}: {exc.msg}"
        return f"{rel}: compiles OK (not executed)"

    def run_analysis(self, code: str, timeout_s: int = 300) -> str:
        """Run a read-only analysis script (skeptic, analyze phase) with cwd = the round dir."""
        self._need("run_analysis")
        if self.phase != "analyze":
            raise Denied("run_analysis is only available in the analyze phase")
        adir = self.root / "rounds" / self.round_dir / "analysis" / self.role  # one folder per parallel skeptic
        adir.mkdir(parents=True, exist_ok=True)
        n = len(list(adir.glob("a*.py"))) + 1
        script = adir / f"a{n:02d}.py"
        script.write_text(code, encoding="utf-8")
        cmd, kind = sandbox.wrap([self.python, str(script)], writable=[adir], readonly=[self.root], network=False)
        # cwd = the round dir, so scripts open('output/results.json') as before
        t = time.monotonic()
        try:
            res = subprocess.run(cmd, cwd=self.root / "rounds" / self.round_dir, capture_output=True,
                                 text=True, timeout=min(timeout_s, ANALYSIS_TIMEOUT_S))
            out, err, rc = res.stdout, res.stderr, res.returncode
        except subprocess.TimeoutExpired as exc:
            out, err, rc = exc.stdout or "", (exc.stderr or "") + "\n[timed out]", -1
            out = out.decode() if isinstance(out, bytes) else out
            err = err.decode() if isinstance(err, bytes) else err
        log = f"# exit={rc} wall={time.monotonic() - t:.1f}s sandbox={kind} (writable: analysis/ only, no network)\n## stdout\n{out}\n## stderr\n{err}"
        (adir / f"a{n:02d}.log").write_text(log, encoding="utf-8")
        return f"saved {script.relative_to(self.root / 'rounds' / self.round_dir).as_posix()} (cwd was rounds/{self.round_dir})\n" + log[-12_000:]

    def _need(self, tool: str) -> None:
        if tool not in roles.tools(self.role):
            raise Denied(f"role {self.role} does not have {tool}")


def main() -> None:
    from mcp.server.fastmcp import FastMCP

    door = Door.from_env()
    app = FastMCP("study")

    def guard(fn, *a, **kw) -> str:  # type: ignore[no-untyped-def]
        try:
            return fn(*a, **kw)
        except (Denied, FileNotFoundError, UnicodeDecodeError) as exc:
            return f"ERROR: {exc}"

    @app.tool()
    def study_brief() -> str:
        """The study question, current round/phase, your role, what you may write, and the hypothesis ledger. Call this first."""
        return guard(door.brief)

    @app.tool()
    def read_file(path: str, offset: int = 0) -> str:
        """Read a file in the study, path relative to the study root (e.g. 'rounds/R01/plan.md')."""
        return guard(door.read, path, offset)

    @app.tool()
    def list_files(path: str = ".") -> str:
        """Recursively list files under a study directory (relative to the study root)."""
        return guard(door.list, path)

    @app.tool()
    def write_file(path: str, content: str) -> str:
        """Create or overwrite a file in the study. Only paths your role may write in this phase are accepted; .json must be valid JSON."""
        return guard(door.write, path, content)

    if "check_syntax" in roles.tools(door.role):
        @app.tool()
        def check_syntax(path: str) -> str:
            """Compile a Python file to catch syntax errors. Does NOT run it."""
            return guard(door.check_syntax, path)

    if "run_analysis" in roles.tools(door.role) and door.phase == "analyze":
        @app.tool()
        def run_analysis(code: str, timeout_s: int = 300) -> str:
            """Run a Python analysis script with cwd = the current round dir (so 'output/results.json' etc. resolve). Saved under analysis/. Use it to recompute numbers from raw outputs; do not rerun the experiment."""
            return guard(door.run_analysis, code, timeout_s)

    app.run()


if __name__ == "__main__":
    main()
