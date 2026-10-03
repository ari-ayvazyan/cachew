"""Artifact store: one small JSON file per artifact, foldered by kind.

Every artifact gets a typed ID (``H-001``, ``R-004`` ...), its author role and a
timestamp. Roles hand each other IDs, never prose; anything a role needs it
loads from here. Folders are created lazily, the first time a kind is written.

    studies/<study>/
      study.json  ledger.jsonl  board.md  board.html
      sources/S-001.json      passages the hypotheses rest on
      hypotheses/H-001.json   claim + machine-readable prediction rule
      proposals/T-001.json    candidate tests
      specs/X-001.json        chosen test, pre-registered predictions, versions
      runs/R-001/result.json  summary; runs/R-001/raw/<part>.json data points
      reviews/K-001.json      skeptic checks
      decisions/D-001.json    select / update / stop, always citing IDs
      handoffs/HO-001.json    the ID bundle passed between roles
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

KINDS = {
    "S": "sources",
    "H": "hypotheses",
    "T": "proposals",
    "X": "specs",
    "R": "runs",
    "K": "reviews",
    "D": "decisions",
    "HO": "handoffs",
}
MAX_BYTES = 64_000  # keeps every file readable at a glance; raw data is split into parts


class TooLarge(ValueError):
    pass


class Store:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # -- paths -------------------------------------------------------------
    def folder(self, prefix: str) -> Path:
        return self.root / KINDS[prefix]

    def path(self, aid: str) -> Path:
        prefix = aid.split("-")[0]
        if prefix == "R":
            return self.folder("R") / aid / "result.json"
        return self.folder(prefix) / f"{aid}.json"

    def rel(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    # -- writes ------------------------------------------------------------
    def _write(self, path: Path, obj: Any) -> None:
        text = json.dumps(obj, indent=1, sort_keys=False)
        if len(text.encode()) > MAX_BYTES:
            raise TooLarge(f"{path.name}: {len(text)} bytes > {MAX_BYTES}; split it")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def next_id(self, prefix: str) -> str:
        folder = self.folder(prefix)
        n = len(list(folder.glob(f"{prefix}-*"))) if folder.exists() else 0
        return f"{prefix}-{n + 1:03d}"

    def put(self, prefix: str, obj: dict[str, Any], author: str, aid: str | None = None) -> str:
        """Write a new artifact. ``aid`` is for runs, whose raw parts land before the result."""
        aid = aid or self.next_id(prefix)
        if self.path(aid).exists():
            raise FileExistsError(aid)
        record = {"id": aid, "author": author, "created": time.time(), **obj}
        self._write(self.path(aid), record)
        self.log({"event": "put", "id": aid, "author": author})
        return aid

    def patch(self, aid: str, author: str, **fields: Any) -> None:
        """Change fields of an existing artifact; the change is logged with its author."""
        record = self.get(aid)
        record.update(fields)
        self._write(self.path(aid), record)
        self.log({"event": "patch", "id": aid, "author": author, "fields": sorted(fields)})

    def put_raw(self, run_id: str, part: str, obj: Any) -> str:
        path = self.folder("R") / run_id / "raw" / f"{part}.json"
        self._write(path, obj)
        return self.rel(path)

    def put_text(self, name: str, text: str) -> None:
        if len(text.encode()) > MAX_BYTES * 2:
            raise TooLarge(name)
        (self.root / name).write_text(text, encoding="utf-8")

    def meta(self) -> dict[str, Any]:
        path = self.root / "study.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def set_meta(self, **fields: Any) -> None:
        self._write(self.root / "study.json", {**self.meta(), **fields})

    def log(self, event: dict[str, Any]) -> None:
        with (self.root / "ledger.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"t": round(time.time(), 3), **event}) + "\n")

    # -- reads -------------------------------------------------------------
    def get(self, aid: str) -> dict[str, Any]:
        return json.loads(self.path(aid).read_text(encoding="utf-8"))

    def raw(self, rel_path: str) -> Any:
        return json.loads((self.root / rel_path).read_text(encoding="utf-8"))

    def all(self, prefix: str) -> list[dict[str, Any]]:
        folder = self.folder(prefix)
        if not folder.exists():
            return []
        ids = sorted(p.name if prefix == "R" else p.stem for p in folder.glob(f"{prefix}-*"))
        return [self.get(i) for i in ids]
