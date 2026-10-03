"""An in-process stand-in for POST /v1/messages that models prompt caching.

It is not the real API; it implements the documented caching rules so the
patch's request shapes and concurrency behaviour can be tested offline:

* cache keys are the exact prefix (tools -> system -> messages) up to a block
  carrying ``cache_control``; any byte change misses
* an entry is readable only ``prefill_s`` after the writing request started
  (the real API: once the response begins streaming)
* prefixes shorter than ``min_tokens`` are never cached
* usage reports ``input_tokens`` as the uncached remainder only

Token counts are estimated as len(json)/4.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import Any

import httpx


def _tokens(block: Any) -> int:
    return max(1, len(json.dumps(block, sort_keys=True)) // 4)


def _strip_cc(block: Any) -> Any:
    return {k: v for k, v in block.items() if k != "cache_control"} if isinstance(block, dict) else block


def _render(payload: dict[str, Any]) -> list[tuple[Any, bool]]:
    """Flatten a payload into (block, has_breakpoint) in render order."""
    out: list[tuple[Any, bool]] = []
    for tool in payload.get("tools") or []:
        out.append((("tool", _strip_cc(tool)), "cache_control" in tool))
    system = payload.get("system")
    if isinstance(system, str):
        out.append((("system", system), False))
    for block in system if isinstance(system, list) else []:
        out.append((("system", _strip_cc(block)), "cache_control" in block))
    for m in payload.get("messages") or []:
        content = m["content"]
        if isinstance(content, str):
            out.append(((m["role"], content), False))
        else:
            for block in content:
                out.append(((m["role"], _strip_cc(block)), "cache_control" in block))
    # thinking/effort settings are part of the cached prefix too
    out.insert(0, (("settings", payload.get("model"), payload.get("thinking"), payload.get("output_config")), False))
    return out


class FakeAnthropic:
    def __init__(self, *, min_tokens: int = 512, prefill_s: float = 0.05, latency_s: float = 0.2, output_tokens: int = 50):
        self.min_tokens = min_tokens
        self.prefill_s = prefill_s
        self.latency_s = latency_s
        self.output_tokens = output_tokens
        self.entries: dict[str, float] = {}  # prefix hash -> readable_at
        self.requests: list[dict[str, Any]] = []  # recorded payload + usage

    async def handle(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        started = time.monotonic()
        blocks = _render(payload)

        h = hashlib.sha256()
        cum = 0
        breakpoints: list[tuple[str, int]] = []
        for block, has_cc in blocks:
            h.update(json.dumps(block, sort_keys=True).encode())
            cum += _tokens(block)
            if has_cc:
                breakpoints.append((h.hexdigest(), cum))
        total = cum

        read = 0
        hit_index = -1
        for i in range(len(breakpoints) - 1, -1, -1):
            key, tokens = breakpoints[i]
            if self.entries.get(key, float("inf")) <= started:
                read, hit_index = tokens, i
                break
        created = 0
        prev = read
        written: list[str] = []
        for key, tokens in breakpoints[hit_index + 1 :]:
            if tokens < self.min_tokens:
                continue
            created += tokens - prev
            prev = tokens
            self.entries.setdefault(key, started + self.prefill_s)
            written.append(key)

        max_tokens = payload.get("max_tokens", 0)
        out_tokens = 0 if max_tokens == 0 else self.output_tokens
        usage = {
            "input_tokens": total - read - created,
            "cache_creation_input_tokens": created,
            "cache_read_input_tokens": read,
            "output_tokens": out_tokens,
        }
        record = {"payload": payload, "usage": usage, "prewarm": max_tokens == 0, "t0": started}
        self.requests.append(record)
        await asyncio.sleep(self.prefill_s if max_tokens == 0 else self.latency_s)
        # Once a response is back, its entries are readable. asyncio.sleep can
        # wake up to one clock tick early (15.6 ms on Windows), so without this
        # a pre-warm could return before its own entry became readable.
        now = record["t1"] = time.monotonic()
        for key in written:
            self.entries[key] = min(self.entries[key], now)
        return httpx.Response(
            200,
            json={
                "id": f"msg_{len(self.requests)}",
                "type": "message",
                "role": "assistant",
                "model": payload["model"],
                # ~4 chars per token, so a compaction "brief" has realistic size
                "content": [] if out_tokens == 0 else [{"type": "text", "text": "note " * (out_tokens * 4 // 5)}],
                "stop_reason": "max_tokens" if out_tokens == 0 else "end_turn",
                "usage": usage,
            },
        )

    def install(self) -> "FakeAnthropic":
        """Route every httpx.AsyncClient in this process to the fake server."""
        self._orig_client = httpx.AsyncClient
        fake = self

        class _Client(self._orig_client):  # type: ignore[misc, valid-type]
            def __init__(self, *a: Any, **kw: Any) -> None:
                kw["transport"] = httpx.MockTransport(fake.handle)
                super().__init__(*a, **kw)

        httpx.AsyncClient = _Client  # type: ignore[misc]
        return self

    def uninstall(self) -> None:
        httpx.AsyncClient = self._orig_client  # type: ignore[misc]
