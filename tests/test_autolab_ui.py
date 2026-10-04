"""The browser UI's API: every action runs the real CLI as a background job (with the fake omni)."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from autolab import execute
from autolab.server import create_app

FAKE = f"{sys.executable} {Path(__file__).with_name('fake_omni.py')}"
H = {"X-Autolab": "1"}


class TestUI(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.env = mock.patch.dict(os.environ, {"AUTOLAB_OMNI": FAKE, "AUTOLAB_DATA": str(self.dir / "data")})
        self.env.start()
        self.c = TestClient(create_app(self.dir / "studies"))

    def tearDown(self) -> None:
        self.env.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def wait(self, name: str) -> dict:
        for _ in range(300):
            s = self.c.get(f"/api/studies/{name}").json()
            if not (s["job"] and s["job"]["state"] == "running"):
                return s
            time.sleep(0.2)
        self.fail("job did not finish")

    def new(self, name: str = "s1") -> None:
        r = self.c.post("/api/studies", json={"name": name, "question": "Does B beat A on this task?",
                                              "max_run_minutes": 1}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)

    def test_index_and_env(self) -> None:
        self.assertIn("Autolab", self.c.get("/").text)
        self.assertIn("sandbox", self.c.get("/api/env").json())

    def test_full_round_through_the_api(self) -> None:
        self.new()
        s = self.c.get("/api/studies/s1").json()
        self.assertEqual((s["status"], s["actions"]), ("ready", ["plan"]))
        job = self.c.post("/api/studies/s1/plan", json={}, headers=H).json()["job"]
        self.assertEqual(job["state"], "running")
        s = self.wait("s1")
        self.assertEqual(s["status"], "awaiting_approval")
        self.assertEqual(s["actions"], ["approve_run", "approve", "revise"])
        self.assertIn("run.py", "".join(s["pending"]["code"]))
        act = self.c.get("/api/studies/s1/activity").json()
        self.assertEqual(act["round"], "R01")
        self.assertEqual(set(act["agents"]), {"pi", "scout_1", "scout_2", "scout_3", "theorist", "theorist_1", "theorist_2",
                                              "theorist_3", "experimenter", "skeptic_1", "skeptic_2", "skeptic_3", "judge"})
        log = self.c.get(f"/api/jobs/{s['job']['id']}").json()
        self.assertIn("awaiting your approval", log["text"])
        # not allowed in this state
        self.assertEqual(self.c.post("/api/studies/s1/run", json={}, headers=H).status_code, 409)
        self.c.post("/api/studies/s1/approve_run", json={"note": "lgtm"}, headers=H)
        s = self.wait("s1")
        self.assertEqual(s["status"], "ready")
        self.assertEqual(s["pending"]["approval"]["note"], "lgtm")
        self.assertTrue(s["pending"]["verdict"])
        self.assertIn("REPORT.md", [f["path"] for f in self.c.get("/api/studies/s1/tree").json()])
        self.assertIn("B beats A", self.c.get("/files/s1/REPORT.md").text)
        self.assertEqual(self.c.get("/files/s1/board.html").headers["content-type"].split(";")[0], "text/html")

    def test_revise_needs_feedback(self) -> None:
        self.new()
        self.c.post("/api/studies/s1/plan", json={}, headers=H)
        self.wait("s1")
        self.assertEqual(self.c.post("/api/studies/s1/revise", json={"feedback": " "}, headers=H).status_code, 400)
        self.c.post("/api/studies/s1/revise", json={"feedback": "use 5 seeds"}, headers=H)
        s = self.wait("s1")
        self.assertEqual(s["status"], "awaiting_approval")
        self.assertIn("rounds/R01/revisions/v1/plan.md", [f["path"] for f in self.c.get("/api/studies/s1/tree").json()])

    def test_cancel_returns_to_a_safe_state(self) -> None:
        self.new()
        with mock.patch.dict(os.environ, {"FAKE_OMNI_SLEEP": "30"}):
            job = self.c.post("/api/studies/s1/plan", json={}, headers=H).json()["job"]
        time.sleep(1.5)
        self.assertEqual(self.c.post("/api/studies/s1/plan", json={}, headers=H).status_code, 409)  # one job per study
        self.c.post(f"/api/jobs/{job['id']}/cancel", headers=H)
        s = self.wait("s1")
        self.assertEqual(s["job"]["state"], "cancelled")
        self.assertEqual(s["status"], "plan_failed")
        self.assertEqual(s["actions"], ["plan"])

    def test_sees_and_stops_a_run_started_outside_the_ui(self) -> None:
        """Regression: runs started from a terminal (or an earlier server) were invisible, so the UI never synced."""
        import subprocess
        self.new()
        root = self.dir / "studies" / "s1"
        env = {**os.environ, "FAKE_OMNI_SLEEP": "30"}
        proc = subprocess.Popen([sys.executable, "-m", "autolab", "plan", str(root)], env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(50):
                s = self.c.get("/api/studies/s1").json()
                if s["status"] == "planning":
                    break
                time.sleep(0.1)
            self.assertTrue(s["busy"])
            self.assertEqual(s["actions"], [])  # no "recover" offered while it really runs
            self.assertTrue(self.c.get("/api/studies").json()[0]["busy"])
            self.assertEqual(self.c.post("/api/studies/s1/plan", json={}, headers=H).status_code, 409)
            self.assertEqual(self.c.post("/api/studies/s1/stop", json={}, headers=H).status_code, 200)
            proc.wait(timeout=20)
        finally:
            proc.kill()
        s = self.c.get("/api/studies/s1").json()
        self.assertFalse(s["busy"])
        self.assertEqual(s["status"], "plan_failed")

    def test_recover_unsticks_a_dead_phase(self) -> None:
        self.new()
        from autolab.study import Study
        Study(self.dir / "studies" / "s1").update(status="running", round=1)
        s = self.c.get("/api/studies/s1").json()
        self.assertEqual(s["actions"], ["recover"])
        self.c.post("/api/studies/s1/recover", json={}, headers=H)
        self.assertEqual(self.c.get("/api/studies/s1").json()["status"], "approved")

    def test_guards(self) -> None:
        self.assertEqual(self.c.post("/api/studies", json={"name": "x", "question": "q" * 20}).status_code, 403)
        self.assertEqual(self.c.post("/api/studies", json={"name": "../x", "question": "q" * 20}, headers=H).status_code, 400)
        self.new()
        self.assertEqual(self.c.post("/api/studies", json={"name": "s1", "question": "q" * 20}, headers=H).status_code, 409)
        self.assertEqual(self.c.get("/files/../study.json").status_code, 404)
        self.assertEqual(self.c.get("/files/s1/../../etc/passwd").status_code, 404)
        self.assertEqual(self.c.get("/api/studies/nope").status_code, 404)

    def test_cachew_needs_api_key(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            self.assertFalse(self.c.get("/api/cachew").json()["api_key"])
            self.assertEqual(self.c.post("/api/cachew", json={}, headers=H).status_code, 409)

    def test_lab_loop_runs_as_job(self) -> None:
        job = self.c.post("/api/lab", json={"name": "fan1", "max_rounds": 1}, headers=H).json()["job"]
        for _ in range(300):
            j = self.c.get(f"/api/jobs/{job['id']}").json()
            if j["state"] != "running":
                break
            time.sleep(0.2)
        self.assertEqual(j["state"], "done", j["text"][-2000:])
        studies = self.c.get("/api/lab").json()["studies"]
        self.assertEqual([s["name"] for s in studies], ["fan1"])
        self.assertTrue(studies[0]["board"])


if __name__ == "__main__":
    unittest.main()
