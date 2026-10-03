"""Offline tests for the fan-out cache patch against a caching-aware fake API.

Run:  .venv/Scripts/python.exe -m unittest discover -s tests -v
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_anthropic import FakeAnthropic  # noqa: E402
from omnigent.llms.adapters import anthropic as adapter_mod  # noqa: E402

from cachew import patch  # noqa: E402
from cachew.compact import pass_history_messages, subagent_messages  # noqa: E402
from cachew.pricing import add_usage, cost_usd  # noqa: E402

MODEL = "claude-opus-5-5"
CONN = {"api_key": "test-key"}
SYSTEM = "You are a sub-agent of a coding orchestrator."
BRIEF = "\n".join(f"- finding {i}: module_{i % 37}.py handles case {i * 7} via path /src/pkg{i % 11}" for i in range(600))
TASKS = {f"sub{i}": f"Task {i}: write tests for module_{i}.py only." for i in range(8)}


def research_history(turns: int = 40) -> list[dict]:
    history = [{"role": "system", "content": SYSTEM}]
    for i in range(turns):
        history.append({"role": "user", "content": f"Investigate area {i}."})
        history.append({"role": "assistant", "content": f"Area {i} notes: " + "lorem ipsum detail " * 120})
    return history


class PatchTestBase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.state_dir = tempfile.mkdtemp()
        os.environ["CACHEW_DIR"] = self.state_dir
        self.fake = FakeAnthropic().install()
        self.adapter = adapter_mod.AnthropicAdapter()

    async def asyncTearDown(self) -> None:
        self.fake.uninstall()
        patch.uninstall()
        shutil.rmtree(self.state_dir, ignore_errors=True)

    async def call(self, messages: list[dict]) -> dict:
        return await self.adapter.chat_completions(messages, MODEL, None, False, {}, connection_params=CONN)

    async def fanout(self, build) -> list[dict]:  # type: ignore[no-untyped-def]
        return await asyncio.gather(*(self.call(build(task)) for task in TASKS.values()))


class TestBreakpoints(PatchTestBase):
    async def test_unpatched_adapter_sends_no_cache_control(self) -> None:
        await self.call(subagent_messages(SYSTEM, BRIEF, "task"))
        self.assertNotIn("cache_control", str(self.fake.requests[0]["payload"]))

    async def test_breakpoints_on_system_and_end_of_shared_prefix(self) -> None:
        patch.install()
        await self.call(subagent_messages(SYSTEM, BRIEF, "task"))
        payload = self.fake.requests[0]["payload"]
        self.assertEqual(payload["system"][-1]["cache_control"], {"type": "ephemeral"})
        self.assertIn("cache_control", payload["messages"][-2]["content"][-1])  # the ack after the brief
        self.assertNotIn("cache_control", str(payload["messages"][-1]))  # per-subagent task stays uncached

    async def test_one_hour_ttl(self) -> None:
        os.environ["CACHEW_TTL"] = "1h"
        self.addCleanup(os.environ.pop, "CACHEW_TTL")
        patch.install()
        await self.call(subagent_messages(SYSTEM, BRIEF, "task"))
        self.assertEqual(self.fake.requests[0]["payload"]["system"][-1]["cache_control"]["ttl"], "1h")

    async def test_usage_passthrough(self) -> None:
        patch.install()
        await self.call(subagent_messages(SYSTEM, BRIEF, "a"))
        out = await self.call(subagent_messages(SYSTEM, BRIEF, "b"))
        self.assertGreater(out["usage"]["cache_read_input_tokens"], 0)
        self.assertEqual(out["usage"]["prompt_tokens_details"]["cached_tokens"], out["usage"]["cache_read_input_tokens"])


class TestFanout(PatchTestBase):
    async def test_concurrent_fanout_reads_cache_instead_of_n_writes(self) -> None:
        patch.install()
        await self.fanout(lambda t: subagent_messages(SYSTEM, BRIEF, t))
        real = [r for r in self.fake.requests if not r["prewarm"]]
        prewarms = [r for r in self.fake.requests if r["prewarm"]]
        self.assertEqual(len(real), len(TASKS))
        self.assertEqual(len(prewarms), 1, "exactly one sibling pre-warms")
        writers = [r for r in self.fake.requests if r["usage"]["cache_creation_input_tokens"] > 0]
        readers = [r for r in real if r["usage"]["cache_read_input_tokens"] > 0]
        self.assertLessEqual(len(writers), 2, "leader + one pre-warm, not N writes")
        self.assertGreaterEqual(len(readers), len(TASKS) - 2)

    async def test_concurrent_fanout_small_brief_still_single_flights(self) -> None:
        # Regression: a ~1.5K-token brief fell under the old pre-warm threshold
        # while still being cacheable, so all N siblings wrote the cache.
        small_brief = "note " * 1250
        patch.install()
        await self.fanout(lambda t: subagent_messages(SYSTEM, small_brief, t))
        writers = [r for r in self.fake.requests if r["usage"]["cache_creation_input_tokens"] > 0]
        self.assertLessEqual(len(writers), 2)

    async def test_concurrent_fanout_without_prewarm_would_write_n_times(self) -> None:
        # Breakpoints alone, no single-flight: shows why the pre-warm exists.
        patch.install()
        adapter_mod._send_request = patch._originals["_send_request"]
        await self.fanout(lambda t: subagent_messages(SYSTEM, BRIEF, t))
        writers = [r for r in self.fake.requests if r["usage"]["cache_creation_input_tokens"] > 0]
        self.assertEqual(len(writers), len(TASKS))

    async def test_sequential_fanout_needs_no_prewarm(self) -> None:
        patch.install()
        for task in TASKS.values():
            await self.call(subagent_messages(SYSTEM, BRIEF, task))
        self.assertFalse(any(r["prewarm"] for r in self.fake.requests))
        self.assertTrue(all(r["usage"]["cache_read_input_tokens"] > 0 for r in self.fake.requests[1:]))

    async def test_single_agent_loop_never_prewarms(self) -> None:
        patch.install()
        history = research_history(6)
        for i in range(4):
            history.append({"role": "user", "content": f"next step {i}"})
            await self.call(history)
            history.append({"role": "assistant", "content": f"done {i}"})
        self.assertFalse(any(r["prewarm"] for r in self.fake.requests))

    async def test_compact_and_cache_is_cheapest(self) -> None:
        history = research_history()

        async def arm(build, patched: bool) -> float:  # type: ignore[no-untyped-def]
            self.fake.requests.clear()
            self.fake.entries.clear()
            shutil.rmtree(self.state_dir, ignore_errors=True)
            if patched:
                patch.install()
            await self.fanout(build)
            patch.uninstall()
            return cost_usd(add_usage(*(r["usage"] for r in self.fake.requests)), MODEL)

        naive = await arm(lambda t: pass_history_messages(history, t), patched=False)
        cache_only = await arm(lambda t: pass_history_messages(history, t), patched=True)
        compact_only = await arm(lambda t: subagent_messages(SYSTEM, BRIEF, t), patched=False)
        compact_cache = await arm(lambda t: subagent_messages(SYSTEM, BRIEF, t), patched=True)
        self.assertLess(cache_only, naive)
        self.assertLess(compact_cache, compact_only)
        self.assertLess(compact_cache, cache_only)


if __name__ == "__main__":
    unittest.main()
