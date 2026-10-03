"""Role identities and the separation rule.

Whoever proposes something never scores it:

    proposer  writes hypotheses (H) and candidate tests (T)
    selector  scores proposals, picks one          (judges T  -> not proposer)
    runner    executes the spec, writes raw data   (R)
    skeptic   audits a run against its raw data    (judges R  -> not runner)
    judge     updates beliefs, decides next/stop   (judges H, R -> not proposer, not runner)
"""

from __future__ import annotations

from lab.store import Store

PROPOSER, SELECTOR, RUNNER, SKEPTIC, JUDGE = "proposer", "selector", "runner", "skeptic", "judge"


class SeparationError(RuntimeError):
    pass


def assert_independent(store: Store, judged_ids: list[str], judge: str) -> None:
    for aid in judged_ids:
        author = store.get(aid)["author"]
        if author == judge:
            raise SeparationError(f"{judge} may not score {aid}: it authored it")
