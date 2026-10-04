"""Offline tests for the cachew harness protocol and Cachew Studio (no API calls, no Omnigent run).

Run:  .venv/Scripts/python.exe -m unittest discover -s tests -v
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cachew import harness  # noqa: E402
from cachew.compact import subagent_turns  # noqa: E402
from cachew.pricing import cost_usd, uncached_usd  # noqa: E402
from lab.studio import runs, team  # noqa: E402

MODEL = "claude-sonnet-5-5"


class TmpDir(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)


class Protocol(TmpDir):
    def test_tag_round_trip(self) -> None:
        tag = harness.dispatch_tag(brief="A-0123456789", agent="scout-1", out="literature/scout-1.md")
        attrs, rest = harness.parse_tag(tag + "Your task.")
        self.assertEqual(attrs, {"brief": "A-0123456789", "agent": "scout-1", "out": "literature/scout-1.md"})
        self.assertEqual(rest, "Your task.")

    def test_untagged_input_passes_through(self) -> None:
        self.assertEqual(harness.parse_tag("hello"), ({}, "hello"))
        self.assertEqual(harness.parse_tag("x <<cachew brief=A>>\n")[0], {})

    def test_brief_id_is_content_addressed(self) -> None:
        a = harness.publish_brief(self.tmp, "A", "same text")
        b = harness.publish_brief(self.tmp, "A", "same text")
        c = harness.publish_brief(self.tmp, "A", "other text")
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertEqual(harness.load_brief(self.tmp, a), "same text")

    def test_members_share_the_brief_prefix(self) -> None:
        one, two = subagent_turns("BRIEF", "task one"), subagent_turns("BRIEF", "task two")
        self.assertEqual(one[:-1], two[:-1])
        self.assertNotEqual(one[-1], two[-1])

    def test_record_call_prices_usage(self) -> None:
        usage = {"input_tokens": 100, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 4000, "output_tokens": 500}
        rec = harness.record_call(self.tmp, "scout-1", model=MODEL, status="thinking")
        self.assertNotIn("usd", rec)
        rec = harness.record_call(self.tmp, "scout-1", status="done", usage=usage)
        self.assertEqual(rec["status"], "done")
        self.assertEqual(rec["model"], MODEL)  # merged with the earlier record
        self.assertAlmostEqual(rec["usd"], cost_usd(usage, MODEL), places=6)
        self.assertAlmostEqual(rec["usd_uncached"], uncached_usd(usage, MODEL), places=6)
        self.assertLess(rec["usd"], rec["usd_uncached"])

    def test_prewarm_has_no_uncached_counterpart(self) -> None:
        usage = {"input_tokens": 3, "cache_creation_input_tokens": 4000, "cache_read_input_tokens": 0, "output_tokens": 0}
        rec = harness.record_call(self.tmp, "prewarm-A", kind="prewarm", model=MODEL, usage=usage)
        self.assertGreater(rec["usd"], 0)
        self.assertEqual(rec["usd_uncached"], 0.0)


class Savings(TmpDir):
    """A pre-warm then two members reading the brief: the net saving counts the pre-warm as spend."""

    def make_run(self) -> Path:
        study = self.tmp / "001-test"
        with mock.patch.object(team, "write_bundle"):
            team.make_study(study, "A test question?", "", MODEL, "low", 2, 1.0)
        run = harness.read_json(study / "run.json")
        bid = harness.publish_brief(study, "A", "brief")
        run["kind"] = runs.KIND
        run["briefs"] = {"A": {"id": bid, "chars": 5, "prewarm": "prewarm-A", "system_sha": "s", "prefix_tokens": 4000}}
        harness.write_json(study / "run.json", run)
        warm = {"input_tokens": 3, "cache_creation_input_tokens": 4000, "cache_read_input_tokens": 0, "output_tokens": 0}
        harness.record_call(study, "prewarm-A", kind="prewarm", brief=bid, model=MODEL, status="done", usage=warm)
        for i in (1, 2):
            read = {"input_tokens": 50, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 4000, "output_tokens": 800}
            harness.record_call(study, f"scout-{i}", brief=bid, model=MODEL, status="done", usage=read, system_sha="s")
        return study

    def test_net_savings(self) -> None:
        study = self.make_run()
        s = runs.savings(study)
        recs = runs.calls(study).values()
        self.assertAlmostEqual(s["usd"], sum(r["usd"] for r in recs), places=5)
        self.assertAlmostEqual(s["saved_usd"], s["usd_uncached"] - s["usd"], places=5)
        self.assertGreater(s["saved_usd"], 0)
        self.assertEqual(s["tokens"]["cache_read_input_tokens"], 8000)

    def test_stage_row_counts_one_write_and_n_reads(self) -> None:
        study = self.make_run()
        (row,) = runs.stages(study, harness.read_json(study / "run.json"), runs.calls(study))
        self.assertEqual(row["agents"], ["scout-1", "scout-2"])
        self.assertEqual((row["writes"], row["reads"]), (1, 2))
        self.assertTrue(row["prefix_match"])
        self.assertAlmostEqual(row["saved_usd"], row["usd_uncached"] - row["usd"], places=5)

    def test_view_marks_layout_and_status(self) -> None:
        study = self.make_run()
        v = runs.view(study)
        self.assertEqual(v["agents"]["scout-1"]["status"], "done")
        self.assertEqual(v["agents"]["judge"]["status"], "idle")
        self.assertEqual(sum(1 for a in v["agents"].values() if a["role"] == "skeptic"), 2)
        self.assertIn("prewarm-A", v["prewarms"])

    def test_read_file_stays_in_study(self) -> None:
        study = self.make_run()
        (study / "plan.md").write_text("plan", encoding="utf-8")
        self.assertEqual(runs.read_file(study, "plan.md"), "plan")
        with self.assertRaises(FileNotFoundError):
            runs.read_file(study, "../../.env")


class Server(TmpDir):
    def setUp(self) -> None:
        super().setUp()
        from fastapi.testclient import TestClient

        from lab.studio import server
        self.patches = [mock.patch.object(runs, "STUDIES", self.tmp), mock.patch.object(server, "has_shared_key", return_value=True)]
        for p in self.patches:
            p.start()
        self.client = TestClient(server.app)

    def tearDown(self) -> None:
        for p in self.patches:
            p.stop()
        super().tearDown()

    def test_config_lists_models(self) -> None:
        cfg = self.client.get("/api/config").json()
        self.assertIn(MODEL, [m["id"] for m in cfg["models"]])

    def post(self, **body: object) -> object:
        return self.client.post("/api/runs", json={"question": "A long enough question?", **body})

    def test_start_validates_and_launches(self) -> None:
        self.assertEqual(self.client.post("/api/runs", json={"question": "short"}).status_code, 422)
        with mock.patch.object(runs, "start", return_value=self.tmp / "001-x") as start:
            r = self.post(model=MODEL, width=2)
        self.assertEqual(r.json(), {"id": "001-x"})
        self.assertEqual(start.call_args.args[:2], ("A long enough question?", ""))
        self.assertEqual(start.call_args.kwargs["api_key"], "")

    def test_shared_key_tier(self) -> None:
        with mock.patch.object(runs, "start", return_value=self.tmp / "001-x") as start:
            for body in ({"model": "claude-opus-5-5"}, {"model": "claude-fable-5-1"}, {"model": MODEL, "effort": "high"},
                         {"model": MODEL, "width": 9}, {"model": MODEL, "budget_usd": 10}):
                r = self.post(**body)
                self.assertEqual(r.status_code, 422, body)
                self.assertIn("own Anthropic API key", r.json()["detail"])
            self.assertEqual(self.post(model="claude-haiku-4-5", effort="high").status_code, 200)
            start.assert_called_once()

    def test_own_key_unlocks_every_model_and_any_team_size(self) -> None:
        with mock.patch.object(runs, "start", return_value=self.tmp / "001-x") as start, \
                mock.patch.object(runs, "check_key") as check:
            r = self.post(model="claude-fable-5-1", effort="high", width=40, budget_usd=50, api_key=" sk-ant-test ")
        self.assertEqual(r.status_code, 200)
        check.assert_called_once_with("sk-ant-test")
        self.assertEqual(start.call_args.kwargs["api_key"], "sk-ant-test")
        self.assertEqual(start.call_args.args[4], 40)

    def test_rejected_key_starts_nothing(self) -> None:
        with mock.patch.object(runs, "start") as start, \
                mock.patch.object(runs, "check_key", side_effect=ValueError("Anthropic rejected this API key")):
            r = self.post(model=MODEL, api_key="sk-ant-bad")
        self.assertEqual((r.status_code, r.json()["detail"]), (422, "Anthropic rejected this API key"))
        start.assert_not_called()

    def test_no_shared_key_needs_own_key(self) -> None:
        from lab.studio import server
        with mock.patch.object(server, "has_shared_key", return_value=False):
            self.assertFalse(self.client.get("/api/config").json()["shared_key"])
            self.assertEqual(self.post(model=MODEL).status_code, 422)

    def test_approval_only_while_waiting(self) -> None:
        study = self.tmp / "001-x"
        study.mkdir()
        harness.write_json(study / "run.json", {"status": "running"})
        self.assertEqual(self.client.post("/api/runs/001-x/approval", json={"decision": "approve"}).status_code, 409)
        harness.write_json(study / "run.json", {"status": "awaiting_approval"})
        harness.write_json(study / "selection.json", {"tests": ["A: one", "B: two"], "chosen": "A"})
        r = self.client.post("/api/runs/001-x/approval", json={"decision": "approve", "test": "B: two", "note": "cheaper"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["changed"])
        self.assertEqual(harness.read_json(study / "approval.json")["test"], "B: two")
        self.assertEqual(self.client.post("/api/runs/001-x/approval", json={"decision": "maybe"}).status_code, 422)

    def test_unknown_run_is_404(self) -> None:
        self.assertEqual(self.client.get("/api/runs/999-nope").status_code, 404)
        self.assertEqual(self.client.get("/api/runs/..%2F..").status_code, 404)


class OwnKey(TmpDir):
    def run_with(self, key: str) -> Path:
        harness.write_json(self.tmp / "run.json", {"key": key})
        return self.tmp

    def test_own_key_run_reads_its_key_and_never_falls_back(self) -> None:
        study = self.run_with("own")
        harness.store_key(study, "sk-ant-own")
        self.assertEqual(harness.api_key(study), "sk-ant-own")
        harness.forget_key(study)
        with self.assertRaises(RuntimeError):
            harness.api_key(study)

    def test_key_file_is_never_served(self) -> None:
        study = self.run_with("own")
        harness.store_key(study, "sk-ant-own")
        for rel in (harness.KEY_FILE, "./" + harness.KEY_FILE):
            with self.assertRaises(FileNotFoundError):
                runs.read_file(study, rel)

    def test_stop_and_finished_runs_drop_the_key(self) -> None:
        study = self.run_with("own")
        harness.store_key(study, "sk-ant-own")
        runs.stop(study)
        self.assertFalse((study / harness.KEY_FILE).exists())


class Approval(TmpDir):
    def test_gate_waits_for_the_decision(self) -> None:
        import asyncio
        run = {"status": "running"}
        harness.write_json(self.tmp / "approval.json", {"decision": "approve", "test": "B"})
        self.assertEqual(asyncio.run(team.await_approval(self.tmp, run, poll_s=0))["decision"], "approve")
        self.assertEqual(harness.read_json(self.tmp / "run.json")["status"], "awaiting_approval")

    def test_stop_ends_the_wait(self) -> None:
        import asyncio
        (self.tmp / "STOP").write_text("now")
        self.assertEqual(asyncio.run(team.await_approval(self.tmp, {}, poll_s=0))["decision"], "stop")

    def test_brief_c_carries_the_scientists_decision(self) -> None:
        harness.write_json(self.tmp / "approval.json", {"decision": "approve", "test": "B: two", "changed": True, "note": "cheaper"})
        note = team.approval_note(self.tmp)
        self.assertIn("B: two", note)
        self.assertIn("cheaper", note)
        harness.write_json(self.tmp / "approval.json", {"decision": "reject"})
        self.assertEqual(team.approval_note(self.tmp), "")


class Science(TmpDir):
    def test_decision_falls_back_to_the_verdict_table(self) -> None:
        md = ("Verdict: Unresolved, more data needed.\n\n## 2. Probabilities\n\n| Hypothesis | Prior | Posterior |\n"
              "|---|---|---|\n| H1 direct | 0.22 | 0.30 |\n| H2 other | 20% | 15% |\n\n## 4. Single next experiment\n\nRun the crossover.\n")
        d = runs._decision_from_verdict(md)
        self.assertEqual(d["posterior"], [{"id": "H1", "p": 0.30}, {"id": "H2", "p": 0.15}])
        self.assertEqual(d["status"], "unresolved")
        self.assertEqual(d["next_experiment"], "Run the crossover.")

    def test_selection_matches_the_chosen_test(self) -> None:
        s = runs._selection({"tests": ["A: crossover", "B: meta-regression"], "chosen": "B (meta-regression)", "why": "cheap"})
        self.assertEqual(s["chosen_index"], 1)
        self.assertEqual(runs._selection({"tests": [{"name": "x"}], "chosen": "x"})["chosen_index"], 0)
        self.assertIsNone(runs._selection(["not", "a", "dict"]))

    def test_parallel_speedup(self) -> None:
        run = {"steps": [{"id": "scouts", "fan": True, "started": 0}], "status": "done", "finished": 20,
               "agents": {"scout-1": {"step": "scouts"}, "scout-2": {"step": "scouts"}}}
        recs = {"scout-1": {"started": 0, "finished": 10}, "scout-2": {"started": 1, "finished": 11}}
        acc = runs.acceleration(run, recs, {"usd": 1.0, "usd_uncached": 2.5}, now=30)
        self.assertEqual(acc["fanouts"][0]["speedup"], round(20 / 11, 2))
        self.assertEqual(acc["cost_ratio"], 2.5)
        self.assertEqual(acc["run_s"], 20)


if __name__ == "__main__":
    unittest.main()
