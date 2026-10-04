"""Entry point: registers the ``cachew`` harness with Omnigent."""

from __future__ import annotations

from omnigent.harness_capabilities import (
    AuthModel,
    EffortFamily,
    Elicitation,
    HarnessCapabilities,
    InstructionDelivery,
    IntegrationMode,
    ModelFamily,
    Resume,
)
from omnigent.harness_plugins import HarnessContribution

_PKG = "omnigent.community.harness.cachew"


def contribution() -> HarnessContribution:
    return HarnessContribution(
        name="cachew",
        valid_harnesses=frozenset({"cachew"}),
        harness_modules={"cachew": f"{_PKG}.app"},
        spawn_env_builders={"cachew": f"{_PKG}.env:build_spawn_env"},
        model_env_keys={"cachew": "HARNESS_CACHEW_MODEL"},
        harness_labels={"cachew": "Cachew (cached fan-outs)"},
        capabilities={
            "cachew": HarnessCapabilities(
                IntegrationMode.SDK_IN_PROCESS,
                Elicitation.NONE,
                Resume.COLD_ONLY,
                EffortFamily.ANTHROPIC,
                ModelFamily.CLAUDE,
                AuthModel.OWN_AUTH,
                subagents=False,
                interrupt=False,
                streaming=False,
                instruction_delivery=InstructionDelivery.COMPOSED_PER_TURN,
            )
        },
    )
