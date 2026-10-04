"""Code version, data version and source passages, so every result is traceable."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def code_version(paths: list[str]) -> dict[str, str]:
    """Git HEAD plus a hash of the files that actually ran (catches uncommitted edits)."""
    try:
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip()
    except OSError:
        head = ""
    files: list[Path] = []
    for p in paths:
        q = REPO / p
        files += sorted(q.rglob("*.py")) if q.is_dir() else [q]
    h = hashlib.sha256()
    for f in files:
        h.update(f.relative_to(REPO).as_posix().encode())
        h.update(f.read_bytes())
    return {"git": head or "none", "tree": h.hexdigest()[:12]}


def data_version(obj: Any) -> str:
    return _sha(json.dumps(obj, sort_keys=True, default=str).encode())


def passage(path: str, start: int, end: int) -> dict[str, Any]:
    """A citable source passage: file, line range, hash of exactly those lines, short quote."""
    lines = (REPO / path).read_text(encoding="utf-8").splitlines()[start - 1 : end]
    text = "\n".join(lines)
    quote = " ".join(text.split())
    return {"file": path, "lines": [start, end], "sha": _sha(text.encode()), "quote": quote[:160]}


def passage_still_matches(src: dict[str, Any]) -> bool:
    return passage(src["file"], *src["lines"])["sha"] == src["sha"]
