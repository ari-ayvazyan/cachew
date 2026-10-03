"""Install the patch the moment Omnigent's Anthropic adapter is imported.

Loaded from a ``.pth`` file (see ``scripts/enable_cachew.py``) so every
Omnigent process in the venv — server, runner, sub-agent runners — is covered
without importing Omnigent eagerly in unrelated Python processes.
"""

from __future__ import annotations

import importlib.abc
import importlib.util
import sys

_TARGET = "omnigent.llms.adapters.anthropic"


class _PatchOnImport(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):  # type: ignore[no-untyped-def]
        if name != _TARGET:
            return None
        sys.meta_path.remove(self)
        spec = importlib.util.find_spec(name)
        if spec is None or spec.loader is None:
            return spec
        exec_module = spec.loader.exec_module

        def exec_and_patch(module):  # type: ignore[no-untyped-def]
            exec_module(module)
            from cachew.patch import install

            install()

        spec.loader.exec_module = exec_and_patch  # type: ignore[method-assign]
        return spec


if _TARGET in sys.modules:
    from cachew.patch import install

    install()
elif not any(isinstance(f, _PatchOnImport) for f in sys.meta_path):
    sys.meta_path.insert(0, _PatchOnImport())
