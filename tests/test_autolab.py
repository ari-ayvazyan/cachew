"""Offline tests for autolab: permissions, approval gate, full round with a fake omni."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from autolab import bundle, cli, execute, roles
from autolab.mcp_server import Denied, Door
from autolab.study import Study, StudyError

FAKE = f"{sys.executable} {Path(__file__).with_name('fake_omni.py')}"


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.env = mock.patch.dict(os.environ, {"AUTOLAB_OMNI": FAKE})
        self.env.start()
        self.data = mock.patch.object(execute, "DATA_DIR", self.dir / "data")
        self.data.start()
        self.root = self.dir / "s1"
        self.study = Study.create(self.root, "Does B beat A?", max_run_minutes=1)

    def tearDown(self) -> None:
        self.env.stop()
        self.data.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def cli(self, *args: str) -> None:
        with mock.patch("builtins.print"):
            cli.main([args[0], str(self.root), *args[1:]])


class TestDoor(Base):
    def test_role_and_phase_permissions(self) -> None:
        Door(self.root, "theorist", "plan", "R01").write("rounds/R01/candidates.json", "{}")
        with self.assertRaises(Denied):  # predictions frozen after planning
            Door(self.root, "theorist", "analyze", "R01").write("rounds/R01/predictions.json", "{}")
        with self.assertRaises(Denied):  # experimenter can't touch another round
            Door(self.root, "experimenter", "plan", "R02").write("rounds/R01/experiment/run.py", "")
        with self.assertRaises(Denied):  # judge can't write in plan phase
            Door(self.root, "judge", "plan", "R01").write("rounds/R01/verdict.json", "{}")
        with self.assertRaises(Denied):  # no escaping the study
            Door(self.root, "scout_1", "plan", "R01").write("../evil.md", "x")
        with self.assertRaises(Denied):  # invalid JSON rejected
            Door(self.root, "pi", "plan", "R01").write("rounds/R01/selection.json", "{nope")

    def test_parallel_instances_are_isolated(self) -> None:
        Door(self.root, "scout_2", "plan", "R01").write("literature/R01-scout_2.md", "notes")
        with self.assertRaises(Denied):  # another scout's notes
            Door(self.root, "scout_2", "plan", "R01").write("literature/R01-scout_1.md", "x")
        with self.assertRaises(Denied):  # only the lead writes the merged candidates
            Door(self.root, "theorist_1", "plan", "R01").write("rounds/R01/candidates.json", "{}")
        Door(self.root, "theorist_1", "plan", "R01").write("rounds/R01/proposals/theorist_1.json", "{}")
        with self.assertRaises(Denied):
            Door(self.root, "skeptic_1", "analyze", "R01").write("rounds/R01/reviews/skeptic_2.json", "{}")
        with self.assertRaises(Denied):  # the merged review is the driver's
            Door(self.root, "skeptic_1", "analyze", "R01").write("rounds/R01/review.json", "{}")
        with self.assertRaises(ValueError):
            Door(self.root, "scout", "plan", "R01")  # only numbered instances exist
        self.assertIn("COUNTER-EVIDENCE", Door(self.root, "scout_3", "plan", "R01").brief())

    def test_extra_tools_by_role(self) -> None:
        with self.assertRaises(Denied):
            Door(self.root, "experimenter", "plan", "R01").run_analysis("print(1)")
        with self.assertRaises(Denied):  # skeptic can't run code while planning
            Door(self.root, "skeptic_1", "plan", "R01").run_analysis("print(1)")
        d = Door(self.root, "experimenter", "plan", "R01")
        d.write("rounds/R01/experiment/run.py", "def f(:\n")
        self.assertIn("SyntaxError", d.check_syntax("rounds/R01/experiment/run.py"))

    def test_analysis_sandbox_is_read_only(self) -> None:
        from autolab import sandbox
        if not sandbox.available():
            self.skipTest("bwrap not installed")
        target = self.root / "study.json"
        before = target.read_text()
        out = Door(self.root, "skeptic_1", "analyze", "R01").run_analysis(
            f"open({str(target)!r}, 'w').write('hacked')")
        self.assertIn("exit=1", out)
        self.assertEqual(target.read_text(), before)

    def test_bundle_has_no_shell(self) -> None:
        self.study.update(round=1)
        b = bundle.generate(self.study, "plan")
        import yaml
        for cfg in [b / "config.yaml", *(b / "agents").glob("*/config.yaml")]:
            spec = yaml.safe_load(cfg.read_text())
            self.assertNotIn("os_env", spec, cfg)
            self.assertEqual(spec["skills"], "none")
        scout = yaml.safe_load((b / "agents" / "scout_1" / "config.yaml").read_text())
        self.assertEqual(scout["tools"]["builtins"], [{"name": "web_search", "search_provider": "keenable"}])
        mcp = yaml.safe_load((b / "agents" / "skeptic_2" / "tools" / "mcp" / "study.yaml").read_text())
        self.assertEqual(mcp["env"]["AUTOLAB_ROLE"], "skeptic_2")
        self.assertEqual(sorted(p.name for p in (b / "agents").iterdir()), sorted(roles.team({})))


class TestLoop(Base):
    def test_full_round(self) -> None:
        self.cli("plan")
        self.assertEqual(self.study.status, "awaiting_approval")
        with self.assertRaises(StudyError):  # nothing runs without approval
            cli.cmd_run(type("A", (), {"study": str(self.root)})())
        self.cli("approve", "--run")
        s = self.study
        self.assertEqual(s.status, "ready")
        run = json.loads((s.rdir() / "run.json").read_text())
        self.assertEqual((run["exit_code"], run["results_json"]), (0, "present"))
        h = {x["id"]: x for x in s.hypotheses()}
        self.assertEqual(h["H1"]["credence"]["R01"], 0.8)
        self.assertEqual(h["H2"]["status"], "refuted")
        self.assertTrue((self.root / "board.html").exists() and (self.root / "REPORT.md").exists())
        self.assertIn("B beats A", (self.root / "REPORT.md").read_text())
        rev = json.loads((s.rdir() / "review.json").read_text())  # merged by the driver, strictest wins
        self.assertEqual(rev["verdict"], "pass_with_caveats")
        self.assertEqual(set(rev["by"]), {"skeptic_1", "skeptic_2", "skeptic_3"})
        lit = (self.root / "literature.md").read_text()
        self.assertTrue(all(f"scout_{i}" in lit for i in (1, 2, 3)))
        self.assertTrue((s.rdir() / "analysis" / "skeptic_2" / "a01.py").exists())
        self.cli("plan")  # second round starts
        self.assertEqual(s.round, 2)

    def test_tampering_after_approval_blocks_run(self) -> None:
        self.cli("plan")
        self.cli("approve")
        (self.study.rdir() / "experiment" / "run.py").write_text("print('changed')")
        with self.assertRaises(StudyError):
            execute.run(self.study, echo=False)

    def test_validation_problems_go_back_to_pi(self) -> None:
        with mock.patch.dict(os.environ, {"FAKE_OMNI_BAD_PLAN": "1"}):
            self.cli("plan")
        calls = [json.loads(x) for x in (self.root / "fake_omni_calls.jsonl").read_text().splitlines()]
        self.assertEqual([c["fix"] for c in calls], [False, True])
        self.assertEqual(self.study.meta["plan_problems"], [])

    def test_revise_archives_previous_plan(self) -> None:
        self.cli("plan")
        self.cli("revise", "use 5 seeds")
        self.assertTrue((self.study.rdir() / "revisions" / "v1" / "plan.md").exists())
        self.assertEqual(self.study.status, "awaiting_approval")

    def test_conclude(self) -> None:
        with mock.patch.dict(os.environ, {"FAKE_OMNI_DECISION": "conclude"}):
            self.cli("plan")
            self.cli("approve", "--run")
        self.assertEqual(self.study.status, "concluded")

    def test_timeout_is_enforced(self) -> None:
        self.cli("plan")
        self.study.update(config={**self.study.config, "max_run_minutes": 0.02})
        (self.study.rdir() / "experiment" / "run.py").write_text("import time\ntime.sleep(30)  # --out\n")
        self.study.update(status="awaiting_approval")
        execute.approve(self.study)
        rec = execute.run(self.study, echo=False)
        self.assertTrue(rec["timed_out"])
        self.assertLess(rec["wall_seconds"], 10)


if __name__ == "__main__":
    unittest.main()
