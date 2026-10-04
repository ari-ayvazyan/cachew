"""Load a research topic from a folder, so topic code never has to live in this repo.

A topic is a Python package (a folder with ``__init__.py``) that defines the
domain interface described in docs/research-loop.md. ``load("x")`` looks for
``topics/x`` (git-ignored) when ``x`` is not itself a path.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

TOPICS = Path(__file__).resolve().parent.parent / "topics"


def resolve(ref: str) -> Path:
    path = Path(ref)
    if not path.is_dir():
        path = TOPICS / ref
    if not (path / "__init__.py").is_file():
        have = sorted(p.name for p in TOPICS.glob("*/__init__.py")) if TOPICS.is_dir() else []
        raise SystemExit(f"no topic at {ref!r} (a folder with __init__.py); local topics: {', '.join(have) or 'none'}")
    return path.resolve()


def load(ref: str) -> ModuleType:
    path = resolve(ref)
    name = f"topic_{path.name}"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path / "__init__.py", submodule_search_locations=[str(path)])
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # before exec, so the topic's relative imports resolve
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        del sys.modules[name]
        raise
    return mod
