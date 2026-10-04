"""Run a study:  .venv/Scripts/python.exe -m lab --topic <name or folder> --study studies/<name>"""

from __future__ import annotations

import argparse
from pathlib import Path

from lab import topic
from lab.loop import Study


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", "--domain", dest="topic", required=True, help="folder with __init__.py, or a name under topics/")
    ap.add_argument("--study", required=True, help="new folder, e.g. studies/001-my-question")
    ap.add_argument("--fresh", action="store_true", help="overwrite an existing study folder")
    ap.add_argument("--max-rounds", type=int, default=8)
    ap.add_argument("--resolve-at", type=float, default=0.95)
    ap.add_argument("--pace", type=float, default=0.0, help="pause (s) after each step to watch the board live")
    args = ap.parse_args()
    domain = topic.load(args.topic)
    last = Study(domain, Path(args.study), max_rounds=args.max_rounds, resolve_at=args.resolve_at, pace=args.pace).go(args.fresh)
    print(f"{last.get('id')}: {last.get('stop') or 'unresolved'} · {last.get('why')}")
    print(f"board: {Path(args.study) / 'board.md'}")


if __name__ == "__main__":
    main()
