"""Run code with the filesystem read-only except for named directories (bubblewrap).

Used for approved experiments (writable: output dir, dataset cache) and for
the skeptic's analysis scripts (writable: analysis dir, no network). Without
bwrap the command runs unsandboxed and callers record that; the driver's hash
checks on experiment/ and predictions.json still catch tampering.
"""

from __future__ import annotations

import shutil
from pathlib import Path


def available() -> bool:
    return shutil.which("bwrap") is not None


def wrap(cmd: list[str], writable: list[Path], readonly: list[Path] = (), network: bool = True) -> tuple[list[str], str]:
    """Return (command, sandbox_kind).

    /tmp is a fresh tmpfs; ``readonly`` paths are re-bound after it so a study
    that lives under /tmp is still visible (read-only) instead of hidden.
    """
    bwrap = shutil.which("bwrap")
    if bwrap is None:
        return cmd, "none"
    args = [bwrap, "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp",
            "--die-with-parent", "--new-session"]
    for r in readonly:
        args += ["--ro-bind", str(r), str(r)]
    for w in writable:
        w.mkdir(parents=True, exist_ok=True)
        args += ["--bind", str(w), str(w)]
    if not network:
        args.append("--unshare-net")
    return args + ["--"] + cmd, "bwrap"
