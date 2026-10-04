"""Run Cachew Studio:  .venv/Scripts/python.exe -m lab.studio [--port 8787]

Builds the UI with bun first if ``web/dist`` is missing.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

import uvicorn

WEB = Path(__file__).resolve().parent / "web"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--build", action="store_true", help="rebuild the UI with bun before starting")
    args = ap.parse_args()
    if args.build or not (WEB / "dist" / "index.html").exists():
        bun = shutil.which("bun")
        if not bun:
            raise SystemExit("bun is needed to build the UI (https://bun.sh), or build lab/studio/web yourself")
        subprocess.run([bun, "run", "build"], cwd=WEB, check=True)
    print(f"Cachew Studio: http://{args.host}:{args.port}")
    uvicorn.run("lab.studio.server:app", host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
