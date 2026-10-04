"""Live activity model built from the Omnigent trace policy's events."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from autolab import activity, bundle, trace
from autolab.study import Study

U = {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 100, "cache_creation_input_tokens": 0,
     "total_cost_usd": 0.01}


class TestActivity(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.s = Study.create(self.dir / "s", "Does B beat A?")
        self.s.update(round=1)
        self.log = self.s.rdir() / "trace.jsonl"
        self.t = 1000.0

    def tearDown(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)

    def ev(self, role: str, type_: str, *, mirror: bool = True, **kw) -> None:
        """Write an event the way Omnigent delivers it: to the agent's own policy and, for sub-agents, the root's too."""
        self.t += 1
        event = {"type": type_, "context": {"usage": kw.pop("usage", U)}, **kw}
        roles = [role] + (["pi"] if role != "pi" and mirror else [])
        for r in roles:
            trace.make(r, str(self.log))(event, {})
        with open(self.log) as f:  # stamp identical time on the mirrored copies
            lines = [json.loads(x) for x in f]
        for x in lines[-len(roles):]:
            x["t"] = self.t
        self.log.write_text("".join(json.dumps(x) + "\n" for x in lines))

    def test_round_trip(self) -> None:
        self.ev("pi", "request", data={"user_content": "PHASE plan, round R01."})
        self.ev("pi", "tool_call", target="mcp__omnigent__sys_session_send",
                data={"name": "sys_session_send", "arguments": {"agent": "scout_1", "title": "scout_1-R01", "args": "Survey it"}})
        self.ev("scout_1", "request", data={"user_content": "Survey it"})
        self.ev("scout_1", "tool_call", target="mcp__omnigent__study__read_file",
                data={"name": "read_file", "arguments": {"path": "literature.md"}})
        self.ev("scout_1", "tool_result", target="mcp__omnigent__study__read_file",
                data={"result": "ERROR: [Errno 2] No such file"}, request_data={"arguments": {"path": "literature.md"}})
        self.ev("scout_1", "tool_call", target="web_search", data={"name": "web_search", "arguments": {"query": "label smoothing"}})
        self.ev("scout_1", "tool_call", target="study__write_file",
                data={"name": "write_file", "arguments": {"path": "rounds/R01/plan.md", "content": "x"}})
        self.ev("scout_1", "tool_result", target="study__write_file", data={"result": "ERROR: role scout_1 may not write rounds/R01/plan.md"},
                request_data={"arguments": {"path": "rounds/R01/plan.md"}})
        mid = activity.build(self.s, job_running=True)
        self.assertEqual(mid["agents"]["scout_1"]["state"], "working")
        self.ev("scout_1", "tool_call", target="study__write_file",
                data={"name": "write_file", "arguments": {"path": "literature.md", "content": "notes"}})
        self.ev("scout_1", "response", data="Wrote literature.md")
        self.ev("pi", "request", data={"user_content": "[System: sub-agent scout/scout-R01 finished (completed)]"})
        self.ev("pi", "response", data="Done planning")
        a = activity.build(self.s)
        kinds = [(e["kind"], e["from"], e["to"]) for e in a["edges"]]
        self.assertIn(("dispatch", "pi", "scout_1"), kinds)
        self.assertIn(("report", "scout_1", "pi"), kinds)
        self.assertIn(("write", "scout_1", "file:literature.md"), kinds)
        self.assertIn(("read", "file:literature.md", "scout_1"), kinds)
        self.assertIn(("web", "scout_1", "web"), kinds)
        self.assertEqual([k for k in kinds if k[0] == "denied"], [("denied", "scout_1", "file:rounds/R01/plan.md")])
        self.assertEqual(sum(1 for f in a["feed"] if f["kind"] == "error"), 1)  # missing file is not a denial
        # the root policy's mirrored copies were dropped: pi made exactly one tool call
        self.assertEqual(a["agents"]["pi"]["calls"], 1)
        self.assertEqual(a["agents"]["scout_1"]["calls"], 4)
        self.assertEqual(a["agents"]["scout_1"]["state"], "done")
        self.assertEqual(a["agents"]["scout_1"]["tokens"]["cache_read"], 100)
        self.assertEqual([s["agent"] for s in a["spans"]], ["pi", "scout_1"])
        self.assertTrue(all(s["end"] for s in a["spans"]))

    def test_unfinished_phase_without_job_is_stopped(self) -> None:
        self.ev("pi", "request", data={"user_content": "PHASE plan"})
        self.ev("theorist", "request", data={"user_content": "write candidates"})
        self.assertEqual(activity.build(self.s, job_running=True)["agents"]["theorist"]["state"], "working")
        self.assertEqual(activity.build(self.s, job_running=False)["agents"]["theorist"]["state"], "stopped")

    def test_layout_stacks_parallel_instances(self) -> None:
        a = activity.build(self.s)
        pos = {r["id"]: (r["col"], r["row"]) for r in a["roles"]}
        self.assertEqual(pos["scout_1"], (0, 0))
        self.assertEqual(pos["scout_3"], (0, 2))
        self.assertEqual(pos["theorist"], (1, 0))      # lead on top of its proposers
        self.assertEqual(pos["theorist_1"], (1, 1))
        self.assertEqual(pos["judge"][0], 4)

    def test_untraced_round(self) -> None:
        a = activity.build(self.s)
        self.assertFalse(a["traced"])
        self.assertEqual(a["edges"], [])

    def test_every_agent_is_traced(self) -> None:
        b = bundle.generate(self.s, "plan")
        import yaml
        for cfg in [b / "config.yaml", *(b / "agents").glob("*/config.yaml")]:
            pol = yaml.safe_load(cfg.read_text())["guardrails"]["policies"]["trace"]
            self.assertEqual(pol["function"]["path"], "autolab.trace.make")
            self.assertEqual(pol["function"]["arguments"]["log"], str(self.log))

    def test_trace_never_blocks(self) -> None:
        f = trace.make("scout", "/proc/definitely/not/writable.jsonl")
        self.assertEqual(f({"type": "tool_call", "data": {}}, {})["result"], "ALLOW")


if __name__ == "__main__":
    unittest.main()
