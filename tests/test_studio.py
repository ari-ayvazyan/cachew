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
        self.patch = mock.patch.object(runs, "STUDIES", self.tmp)
        self.patch.start()
        self.client = TestClient(server.app)

    def tearDown(self) -> None:
        self.patch.stop()
        super().tearDown()

    def test_config_lists_models(self) -> None:
        cfg = self.client.get("/api/config").json()
        self.assertIn(MODEL, [m["id"] for m in cfg["models"]])

    def test_start_validates_and_launches(self) -> None:
        self.assertEqual(self.client.post("/api/runs", json={"question": "short"}).status_code, 422)
        self.assertEqual(self.client.post("/api/runs", json={"question": "A long enough question?", "width": 9}).status_code, 422)
        with mock.patch.object(runs, "start", return_value=self.tmp / "001-x") as start:
            r = self.client.post("/api/runs", json={"question": "A long enough question?", "model": MODEL, "width": 2})
        self.assertEqual(r.json(), {"id": "001-x"})
        self.assertEqual(start.call_args.args[:2], ("A long enough question?", ""))

    def test_unknown_run_is_404(self) -> None:
        self.assertEqual(self.client.get("/api/runs/999-nope").status_code, 404)
        self.assertEqual(self.client.get("/api/runs/..%2F..").status_code, 404)


if __name__ == "__main__":
    unittest.main()
