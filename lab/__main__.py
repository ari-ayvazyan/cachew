"""Run a study:  .venv/Scripts/python.exe -m lab --domain fanout --study studies/002-fanout-crossover"""

from __future__ import annotations

import argparse
import importlib
from pathlib import Path

from lab.loop import Study


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", default="fanout")
    ap.add_argument("--study", required=True, help="new folder, e.g. studies/002-fanout-crossover")
    ap.add_argument("--fresh", action="store_true", help="overwrite an existing study folder")
    ap.add_argument("--max-rounds", type=int, default=8)
    ap.add_argument("--resolve-at", type=float, default=0.95)
    args = ap.parse_args()
    domain = importlib.import_module(f"lab.domains.{args.domain}")
    last = Study(domain, Path(args.study), max_rounds=args.max_rounds, resolve_at=args.resolve_at).go(args.fresh)
    print(f"{last.get('id')}: {last.get('stop') or 'unresolved'} · {last.get('why')}")
    print(f"board: {Path(args.study) / 'board.md'}")


if __name__ == "__main__":
    main()
