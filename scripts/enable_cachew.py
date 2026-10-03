"""Enable (or with --disable, remove) the fan-out cache patch in this venv.

Writes ``cachew.pth`` into the interpreter's site-packages. Run it
with the venv's python:  .venv/Scripts/python.exe scripts/enable_cachew.py
"""

import argparse
import sysconfig
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PTH = Path(sysconfig.get_paths()["purelib"]) / "cachew.pth"

parser = argparse.ArgumentParser()
parser.add_argument("--disable", action="store_true")
args = parser.parse_args()

if args.disable:
    PTH.unlink(missing_ok=True)
    print(f"removed {PTH}")
else:
    PTH.write_text(f"{REPO}\nimport cachew.autoload\n")
    print(f"wrote {PTH}")
