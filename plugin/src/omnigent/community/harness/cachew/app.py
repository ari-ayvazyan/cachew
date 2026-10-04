"""Harness app: Omnigent's runner imports this module and serves ``create_app()``."""

from __future__ import annotations

from fastapi import FastAPI
from omnigent.runtime.harnesses._executor_adapter import ExecutorAdapter


def _build_executor():  # type: ignore[no-untyped-def]
    from cachew.harness import CachewExecutor

    return CachewExecutor.from_env()


def create_app() -> FastAPI:
    return ExecutorAdapter(executor_factory=_build_executor).build()
