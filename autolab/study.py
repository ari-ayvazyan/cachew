"""A study on disk: state machine, paths, event log and hypothesis ledger.

The driver (this package) is the only writer of the files below; agents write
only the per-round files their role allows (see ``roles.py``).

    studies/<name>/
      study.json        question, config, status, current round
      events.jsonl      append-only log of every driver step
      hypotheses.json   ledger: every hypothesis ever proposed + credence per round
      literature.md     scout's notes (agent-written)
      findings.md       judge's running summary (agent-written)
      board.html        progress board (rendered)
      REPORT.md         final report (rendered)
      rounds/R01/
        candidates.json   theorist: live hypotheses + >=2 candidate experiments
        selection.json    PI: which candidate and why
        plan.md           experimenter: the design
        experiment/       experimenter: run.py (+ helpers), never executed by agents
        predictions.json  theorist: pre-registered prediction per hypothesis
        approval.json     driver: who approved, hashes of experiment/ and predictions
        output/           driver: results.json written by run.py, stdout/stderr logs
        run.json          driver: exit code, wall time, hash check
        analysis/         skeptic: analysis scripts and their outputs
        review.json/.md   skeptic: audit of the raw output
        verdict.json      judge: credence updates citing evidence, next step
        logs/             driver: transcripts of each Omnigent phase
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

# status -> what the user (or driver) does next
STATUSES = {
    "ready": "autolab plan",                       # a new round can be planned
    "planning": "(agents are planning)",
    "plan_failed": "autolab plan (retries this round)",
    "awaiting_approval": "autolab show | approve | revise",
    "approved": "autolab run",
    "running": "(experiment is running)",
    "ran": "autolab analyze",
    "analyzing": "(agents are analyzing)",
    "analysis_failed": "autolab analyze (retry)",
    "concluded": "autolab report",
}

DEFAULT_CONFIG: dict[str, Any] = {
    "model": "claude-sonnet-5-5",
    "models": {},                 # per-role override, e.g. {"pi": "claude-opus-5-5"}
    "max_run_minutes": 30,        # wall-clock cap for one experiment
    "max_rounds": 10,
    "fanout": {"scout": 3, "theorist": 3, "skeptic": 3},  # parallel instances per role
    "hardware": "CPU only, no GPU",
}


class StudyError(RuntimeError):
    pass


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def round_name(n: int) -> str:
    return f"R{n:02d}"


def tree_hash(path: Path) -> str:
    """sha256 over every file's relative path and bytes; '' if the path doesn't exist."""
    if not path.exists():
        return ""
    h = hashlib.sha256()
    files = [path] if path.is_file() else sorted(p for p in path.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    for f in files:
        h.update(str(f.relative_to(path.parent if path.is_file() else path)).encode())
        h.update(b"\0")
        h.update(f.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


class Study:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    # -- creation / state ------------------------------------------------------
    @classmethod
    def create(cls, root: Path, question: str, context: str = "", **config: Any) -> "Study":
        s = cls(root)
        if (s.root / "study.json").exists():
            raise StudyError(f"{s.root} already holds a study")
        cfg = {**DEFAULT_CONFIG, **{k: v for k, v in config.items() if v is not None}}
        write_json(s.root / "study.json", {
            "question": question, "context": context, "created": now(),
            "config": cfg, "status": "ready", "round": 0,
        })
        write_json(s.root / "hypotheses.json", [])
        s.log("created", question=question)
        return s

    @property
    def meta(self) -> dict[str, Any]:
        m = read_json(self.root / "study.json")
        if m is None:
            raise StudyError(f"no study at {self.root} (run `autolab new` first)")
        return m

    def update(self, **fields: Any) -> dict[str, Any]:
        m = self.meta
        m.update(fields)
        write_json(self.root / "study.json", m)
        return m

    @property
    def status(self) -> str:
        return self.meta["status"]

    @property
    def config(self) -> dict[str, Any]:
        return self.meta["config"]

    def require(self, *statuses: str) -> None:
        if self.status not in statuses:
            raise StudyError(f"study is '{self.status}', expected {' or '.join(statuses)}; next: {STATUSES.get(self.status)}")

    def log(self, event: str, **data: Any) -> None:
        with open(self.root / "events.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": now(), "event": event, **data}, ensure_ascii=False) + "\n")

    def events(self) -> list[dict[str, Any]]:
        p = self.root / "events.jsonl"
        if not p.exists():
            return []
        return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]

    # -- rounds ----------------------------------------------------------------
    @property
    def round(self) -> int:
        return self.meta["round"]

    def rdir(self, n: int | None = None) -> Path:
        return self.root / "rounds" / round_name(self.round if n is None else n)

    def rounds(self) -> list[int]:
        return list(range(1, self.round + 1))

    # -- hypothesis ledger -----------------------------------------------------
    def hypotheses(self) -> list[dict[str, Any]]:
        return read_json(self.root / "hypotheses.json", [])

    def merge_candidates(self, n: int) -> None:
        """Add the theorist's hypotheses for round n to the ledger. Existing ones are never dropped."""
        cand = read_json(self.rdir(n) / "candidates.json", {})
        ledger = {h["id"]: h for h in self.hypotheses()}
        for h in cand.get("hypotheses", []):
            if h["id"] in ledger:
                old = ledger[h["id"]]
                if old["statement"] != h["statement"]:
                    old.setdefault("revisions", []).append({"round": n, "statement": h["statement"]})
                    old["statement"] = h["statement"]
            else:
                ledger[h["id"]] = {
                    "id": h["id"], "statement": h["statement"], "catch_all": bool(h.get("catch_all")),
                    "proposed_round": n, "status": "alive", "credence": {},
                }
        write_json(self.root / "hypotheses.json", list(ledger.values()))

    def apply_verdict(self, n: int) -> None:
        verdict = read_json(self.rdir(n) / "verdict.json", {})
        ledger = {h["id"]: h for h in self.hypotheses()}
        for u in verdict.get("updates", []):
            h = ledger.get(u["id"])
            if h is None:
                continue
            h["credence"][round_name(n)] = u["credence"]
            h["status"] = u.get("status", h["status"])
            if h["status"] == "refuted":
                h.setdefault("refuted_round", n)
        write_json(self.root / "hypotheses.json", list(ledger.values()))

    def live_hypotheses(self) -> list[dict[str, Any]]:
        """Alive or supported: still a candidate answer. Only refuted ones drop out."""
        return [h for h in self.hypotheses() if h["status"] != "refuted"]
