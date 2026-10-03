"""Tests for the research loop (lab/).

Run:  .venv/Scripts/python.exe -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import random
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from cachew import fake_api  # noqa: E402
from lab import infogain  # noqa: E402
from lab.loop import Study  # noqa: E402
from lab.roles import PROPOSER, RUNNER, SELECTOR, SKEPTIC, SeparationError, assert_independent  # noqa: E402
from lab.store import Store, TooLarge  # noqa: E402


def coin_domain(true_p: float, rules: list[float]) -> SimpleNamespace:
    """A toy domain: which bias does the coin have? Shows the loop is domain-agnostic."""

    def run(store: Store, rid: str, spec: dict) -> dict:
        rng = random.Random(spec["seed"])
        outcome = "heads" if rng.random() < true_p else "tails"
        return {"spec": spec["id"], "outcome": outcome, "data_version": "v", "raw": {"flip": store.put_raw(rid, "flip", {"points": [{"usage": {}, "o": outcome}]})}}

    return SimpleNamespace(
        name="coin", question="Coin bias?", outcomes=["heads", "tails"], sources={}, code_paths=["lab"],
        hypotheses=[{"key": f"p{p}", "claim": f"p={p}", "sources": [], "rule": {"p": p}} for p in rules]
        + [{"key": "none", "claim": "none", "sources": [], "rule": {"p": 0.5}}],
        predict=lambda rule, params: {"heads": rule["p"], "tails": 1 - rule["p"]},
        feasible=lambda params: (True, "ok"), estimate_usd=lambda params: 0.0,
        propose_tests=lambda tried, r: [{"params": {"flip": len(tried) + i}, "why": "next flip"} for i in range(2)],
        refine=lambda store, runs: None, input_version=lambda params, seed: "v",
        run=run, audit=lambda store, spec, result: [{"check": "ok", "ok": True, "detail": ""}],
        short=lambda params: f"flip {params['flip']}", point_kind=lambda p: "full", glyph=lambda p: "●", legend={"full": "flip"},
    )


class TempDir(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)


class TestInfoGain(unittest.TestCase):
    def test_agreeing_hypotheses_teach_nothing(self) -> None:
        w = {"a": 0.5, "b": 0.5}
        same = {"a": {"x": 0.9, "y": 0.1}, "b": {"x": 0.9, "y": 0.1}}
        self.assertAlmostEqual(infogain.expected_info_gain(w, same), 0.0)

    def test_disagreeing_hypotheses_are_worth_testing(self) -> None:
        w = {"a": 0.5, "b": 0.5}
        split = {"a": {"x": 0.9, "y": 0.1}, "b": {"x": 0.1, "y": 0.9}}
        self.assertGreater(infogain.expected_info_gain(w, split), 0.5)


class TestStoreAndRoles(TempDir):
    def test_ids_are_sequential_and_folders_lazy(self) -> None:
        s = Store(self.dir)
        self.assertFalse((self.dir / "hypotheses").exists())
        self.assertEqual([s.put("H", {}, PROPOSER) for _ in range(2)], ["H-001", "H-002"])

    def test_oversized_files_are_refused(self) -> None:
        with self.assertRaises(TooLarge):
            Store(self.dir).put("R", {"blob": "x" * 70_000}, RUNNER)

    def test_proposer_cannot_score_own_proposal(self) -> None:
        s = Store(self.dir)
        t = s.put("T", {}, PROPOSER)
        with self.assertRaises(SeparationError):
            assert_independent(s, [t], PROPOSER)
        assert_independent(s, [t], SELECTOR)

    def test_runner_cannot_review_own_run(self) -> None:
        s = Store(self.dir)
        r = s.put("R", {}, RUNNER)
        with self.assertRaises(SeparationError):
            assert_independent(s, [r], RUNNER)
        assert_independent(s, [r], SKEPTIC)


class TestLoop(TempDir):
    def test_results_resolve_and_refute(self) -> None:
        last = Study(coin_domain(0.9, [0.9, 0.1]), self.dir / "s", max_rounds=20).go()
        self.assertEqual(last["stop"], "resolved")
        h = {x["key"]: x for x in Study(coin_domain(0.9, []), self.dir / "s").store.all("H")}
        self.assertEqual(h["p0.1"]["status"], "refuted")
        for d in Store(self.dir / "s").all("D"):
            self.assertTrue(d["cites"], f"{d['id']} cites nothing")

    def test_stops_unresolved_when_budget_runs_out(self) -> None:
        last = Study(coin_domain(0.5, [0.6, 0.4]), self.dir / "s", max_rounds=2).go()
        self.assertEqual(last["stop"], "unresolved")

    def test_stops_unresolved_when_no_test_discriminates(self) -> None:
        last = Study(coin_domain(0.5, [0.5]), self.dir / "s").go()  # 'p0.5' and 'none' predict the same
        self.assertEqual((last["kind"], last["stop"]), ("stop", "unresolved"))

    def test_handoffs_carry_ids_not_prose(self) -> None:
        Study(coin_domain(0.9, [0.9, 0.1]), self.dir / "s", max_rounds=3).go()
        to_judge = [h for h in Store(self.dir / "s").all("HO") if h["to"] == "judge"]
        for h in to_judge:
            for key in ("hypotheses", "sources", "spec", "code_version", "data_version", "result_file", "run", "review"):
                self.assertIn(key, h)

    def test_refuses_to_overwrite_a_study(self) -> None:
        Study(coin_domain(0.9, [0.9]), self.dir / "s", max_rounds=1).go()
        with self.assertRaises(FileExistsError):
            Study(coin_domain(0.9, [0.9]), self.dir / "s").go()


class TestFanoutSkeptic(TempDir):
    """One real fan-out run (fake API), then tamper with it and see the skeptic object."""

    @classmethod
    def setUpClass(cls) -> None:
        import lab.domains.fanout as fanout

        cls.d = fanout
        cls.root = Path(tempfile.mkdtemp())
        study = Study(fanout, cls.root)
        study.setup()
        cls.store = study.store
        ho = study.propose(1, None)
        _, cls.x = study.select(ho)
        study.run(cls.x)
        cls.rid = "R-001"

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.root, True)

    def audit(self, result: dict) -> dict[str, bool]:
        return {c["check"]: c["ok"] for c in self.d.audit(self.store, self.store.get(self.x), result)}

    def test_clean_run_passes(self) -> None:
        self.assertTrue(all(self.audit(self.store.get(self.rid)).values()))

    def test_inflated_claim_is_caught(self) -> None:
        r = self.store.get(self.rid)
        r["arms"]["compact_cache"]["usd"] *= 0.5
        self.assertFalse(self.audit(r)["claims_match_raw"])

    def test_cache_leak_is_caught(self) -> None:
        r = self.store.get(self.rid)
        raw = self.store.raw(r["raw"]["naive"])
        raw["points"][0]["cc_on_task"] = True
        r["raw"] = {**r["raw"], "naive": self.store.put_raw(self.rid, "naive_tampered", raw)}
        self.assertFalse(self.audit(r)["no_leakage"])

    def test_intervention_not_running_is_caught(self) -> None:
        r = self.store.get(self.rid)
        raw = self.store.raw(r["raw"]["compact_cache"])
        for p in raw["points"]:
            p["usage"]["cache_creation_input_tokens"] += p["usage"]["cache_read_input_tokens"]
            p["usage"]["cache_read_input_tokens"] = 0
        r["raw"] = {**r["raw"], "compact_cache": self.store.put_raw(self.rid, "cc_tampered", raw)}
        self.assertFalse(self.audit(r)["intervention_ran"])


class TestFakeClock(unittest.IsolatedAsyncioTestCase):
    async def test_entry_readable_once_prewarm_returns_even_if_sleep_wakes_early(self) -> None:
        # Regression for study 001 (R-003, R-005): asyncio.sleep woke a clock tick early on
        # Windows, so a pre-warm returned before its own cache entry was readable.
        async def no_sleep(_: float) -> None:
            return None

        fake = fake_api.FakeAnthropic(prefill_s=0.05)
        body = {"model": "claude-opus-5-5", "system": [{"type": "text", "text": "s " * 2000, "cache_control": {"type": "ephemeral"}}],
                "messages": [{"role": "user", "content": "go"}]}
        with mock.patch.object(fake_api.asyncio, "sleep", no_sleep):
            await fake.handle(httpx.Request("POST", "http://x/v1/messages", content=json.dumps({**body, "max_tokens": 0})))
            await fake.handle(httpx.Request("POST", "http://x/v1/messages", content=json.dumps({**body, "max_tokens": 10})))
        self.assertGreater(fake.requests[1]["usage"]["cache_read_input_tokens"], 0)


if __name__ == "__main__":
    unittest.main()
