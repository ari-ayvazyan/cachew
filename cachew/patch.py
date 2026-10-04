"""Prompt caching for Omnigent's Anthropic adapter.

Omnigent's ``AnthropicAdapter`` flattens the system prompt to a plain string
and never sets ``cache_control``, so a fan-out of N sub-agents that all inherit
the same parent history (``pass_history: true``) pays full input price N times.
It also drops ``cache_read_input_tokens`` / ``cache_creation_input_tokens``
from the usage it reports, so the savings would be invisible anyway.

``install()`` patches the adapter module in place (no files in site-packages
are modified) to:

1. Mark the shared prefix as cacheable: one breakpoint on the system prompt
   and one on the last block *before* the final user turn. For a sub-agent
   that is the end of the inherited / compacted parent context; the
   per-subagent instruction after it is the only uncached part.
2. Single-flight the cache write. An entry is readable only once the request
   writing it has started responding, so siblings fired concurrently would
   each write their own copy. The first request for a prefix (the leader)
   goes straight through; siblings that arrive while it is in flight wait
   until its entry is readable, then read from cache. Waiting on a
   non-streaming leader means waiting for its whole response, so once enough
   siblings are waiting one of them buys latency with a ``max_tokens: 0``
   pre-warm (prefill only, no output billed) that the rest wait on. That is a
   second cache write, so it is only sent when the fan-out still costs no
   more than sending every sibling uncached (see ``min_prewarm_waiters``).
   Coordination is via marker files so it also works across runner processes.
   An orchestrator that knows the shared prefix up front calls ``prewarm()``
   before dispatching its sub-agents, so every sibling reads.
3. Pass the cache usage fields through to the Chat Completions response, and
   to any ``capture_usage()`` sink (Omnigent's ``Response.usage`` drops them).

Every request, pre-warms included, is sent by the adapter's own
``_send_request``; the patch never opens its own connection.

Disable with ``CACHEW=0``. ``CACHEW_TTL=1h`` selects the
1-hour TTL for fan-outs whose siblings start more than 5 minutes apart.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import uuid
import tempfile
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

import httpx

_logger = logging.getLogger(__name__)
# Raw Anthropic ``usage`` of every response in the current context (see capture_usage).
_usage_sink: ContextVar[list[dict[str, Any]] | None] = ContextVar("cachew_usage_sink", default=None)

# Below this prefix size no model caches (the smallest minimum is 512 tokens,
# ~4 chars/token), so a pre-warm would be wasted. Keep it at that floor: any
# prefix that does get cached must be single-flighted, or N concurrent siblings
# each pay the 1.25x write and the fan-out costs more than with no caching.
_MIN_PREWARM_CHARS = int(os.environ.get("CACHEW_MIN_CHARS", "2048"))
# A 5-minute entry is measured from the start of the writing request; stay
# well inside it before trusting a warm marker.
_WARM_FRESH_S = 240.0
_INFLIGHT_FRESH_S = 300.0
_WAIT_FOR_WARM_S = 60.0
_POLL_S = 0.05
_CACHEABLE_BLOCKS = {"text", "image", "document", "tool_use", "tool_result"}

_installed = False
_originals: dict[str, Any] = {}


def _enabled() -> bool:
    return os.environ.get("CACHEW", "1") != "0"


def _cache_control() -> dict[str, str]:
    cc = {"type": "ephemeral"}
    if os.environ.get("CACHEW_TTL") == "1h":
        cc["ttl"] = "1h"
    return cc


def _state_dir() -> Path:
    d = Path(os.environ.get("CACHEW_DIR") or Path(tempfile.gettempdir()) / "cachew")
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── 1. Breakpoints ────────────────────────────────────────


def _mark_last_block(content: Any, cc: dict[str, str]) -> Any:
    if isinstance(content, str):
        return [{"type": "text", "text": content, "cache_control": cc}] if content else content
    if isinstance(content, list):
        for block in reversed(content):
            if isinstance(block, dict) and block.get("type") in _CACHEABLE_BLOCKS:
                block["cache_control"] = cc
                break
    return content


def apply_cache_breakpoints(payload: dict[str, Any]) -> dict[str, Any]:
    """Add cache breakpoints to an Anthropic Messages payload, in place."""
    cc = _cache_control()
    system = payload.get("system")
    if system:
        payload["system"] = _mark_last_block(system, cc)
    elif payload.get("tools"):
        # Render order is tools -> system -> messages; with no system prompt
        # the tool list is the stable head of the prefix.
        payload["tools"][-1]["cache_control"] = cc
    messages = payload.get("messages") or []
    if len(messages) >= 2:
        messages[-2]["content"] = _mark_last_block(messages[-2]["content"], cc)
    return payload


# ── 2. Single-flight pre-warm ─────────────────────────────


def prefix_key(payload: dict[str, Any]) -> str:
    """Hash of everything that must match for two requests to share a cache entry."""
    shared = {
        k: payload.get(k)
        for k in ("model", "tools", "system", "thinking", "output_config")
    }
    shared["messages"] = (payload.get("messages") or [])[:-1]
    blob = json.dumps(shared, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:32]


def _prefix_chars(payload: dict[str, Any]) -> int:
    shared = {k: payload.get(k) for k in ("tools", "system")}
    shared["messages"] = (payload.get("messages") or [])[:-1]
    return len(json.dumps(shared, default=str))


def _prewarm_eligible(payload: dict[str, Any]) -> bool:
    # max_tokens: 0 is rejected together with a fixed thinking budget.
    thinking = payload.get("thinking") or {}
    return thinking.get("type") != "enabled" and _prefix_chars(payload) >= _MIN_PREWARM_CHARS


def _fresh(path: Path, max_age: float) -> bool:
    try:
        return time.time() - path.stat().st_mtime < max_age
    except FileNotFoundError:
        return False


def _touch(path: Path, body: str = "") -> None:
    path.write_text(body)


def _prewarm_payload(payload: dict[str, Any]) -> dict[str, Any]:
    warm = {k: v for k, v in payload.items() if k not in ("stream", "messages", "max_tokens")}
    warm["max_tokens"] = 0
    # Keep the cached prefix byte-identical; replace only the final turn.
    warm["messages"] = list((payload.get("messages") or [])[:-1]) + [
        {"role": "user", "content": "warm"}
    ]
    return warm


def _adapter_send() -> Any:
    """The adapter's unpatched ``_send_request`` (no single-flight around a pre-warm)."""
    from omnigent.llms.adapters import anthropic as mod

    return _originals.get("_send_request") or mod._send_request


async def _send_prewarm(headers: dict[str, str], payload: dict[str, Any], base_url: str) -> dict[str, Any]:
    with capture_usage() as sink:
        await _adapter_send()(headers, _prewarm_payload(payload), base_url, 120)
    return sink[-1] if sink else {}


def min_prewarm_waiters(model: str | None) -> int:
    """Fewest waiting siblings for which a pre-warm keeps the fan-out no
    dearer than sending every request uncached.

    In units of the prefix's base input price, leader + pre-warm + k readers
    cost ``2w + k*r`` against ``k + 1`` uncached, so ``k >= (2w - 1) / (1 - r)``:
    2 for the 5-minute TTL on every current model, 4 for the 1-hour TTL.
    Waiting on the leader instead costs ``w + k*r``, which never loses.
    """
    from cachew.pricing import PRICES

    w = 2.0 if os.environ.get("CACHEW_TTL") == "1h" else 1.25
    p = PRICES.get(model or "")
    r = p.cache_read / p.input if p else 0.1  # 0.1: the highest read ratio priced
    return max(1, math.ceil((2 * w - 1) / (1 - r) - 1e-9))


def _waiters(d: Path, key: str) -> int:
    return sum(1 for f in d.glob(f"{key}.wait.*") if _fresh(f, _WAIT_FOR_WARM_S + 5))


async def ensure_warm(headers: dict[str, str], payload: dict[str, Any], base_url: str, *, streaming: bool = False) -> str | None:
    """Coordinate sibling requests that share a prefix. Returns the prefix key
    when this request is the first (leader) so the caller can mark it warm."""
    if not _prewarm_eligible(payload):
        return None
    d = _state_dir()
    key = prefix_key(payload)
    warm, inflight, lock = d / f"{key}.warm", d / f"{key}.inflight", d / f"{key}.lock"
    attempted = d / f"{key}.prewarm.json"  # kept apart from .warm, which the leader re-touches

    if _fresh(warm, _WARM_FRESH_S):
        return None
    if not _fresh(inflight, _INFLIGHT_FRESH_S):
        _touch(inflight, "stream" if streaming else "send")
        return key  # leader: no one to share with yet, go straight through

    # A sibling with the same prefix is in flight and the entry isn't readable
    # yet. A streaming leader marks it warm at its first chunk, before a
    # pre-warm started now could finish, so only a non-streaming one is worth
    # racing.
    try:
        leader_streams = inflight.read_text() == "stream"
    except FileNotFoundError:
        leader_streams = False
    need = min_prewarm_waiters(payload.get("model"))
    me = d / f"{key}.wait.{uuid.uuid4().hex}"
    _touch(me)
    try:
        deadline = time.monotonic() + _WAIT_FOR_WARM_S
        while time.monotonic() < deadline and not _fresh(warm, _WARM_FRESH_S):
            if not leader_streams and not _fresh(attempted, _WARM_FRESH_S) and _waiters(d, key) >= need:
                try:
                    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                except FileExistsError:
                    pass
                else:
                    os.close(fd)
                    me.unlink(missing_ok=True)
                    await _prewarm_once(headers, payload, base_url, key, warm, attempted, lock)
                    return None
            await asyncio.sleep(_POLL_S)
    finally:
        me.unlink(missing_ok=True)
    return None


async def _prewarm_once(headers: dict[str, str], payload: dict[str, Any], base_url: str, key: str, warm: Path, attempted: Path, lock: Path) -> None:
    try:
        usage = await _send_prewarm(headers, payload, base_url)
        _touch(attempted, json.dumps(usage))
        _touch(warm)
        _logger.info("cachew: pre-warmed prefix %s (%s)", key, usage)
    except httpx.HTTPError as e:
        # Don't let the other waiters retry; they keep waiting on the leader.
        _touch(attempted, json.dumps({"error": str(e)}))
        _logger.warning("cachew: pre-warm failed for %s; continuing uncached", key, exc_info=True)
    finally:
        lock.unlink(missing_ok=True)


def mark_warm(key: str | None) -> None:
    if key:
        _touch(_state_dir() / f"{key}.warm")


async def prewarm(
    messages: list[dict[str, Any]],
    model: str,
    *,
    api_key: str,
    reasoning_effort: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Write the cache entry for a fan-out's shared prefix before any sub-agent starts.

    ``messages`` are Chat Completions messages laid out exactly as each
    sub-agent will send them (``compact.subagent_messages``); the final turn is
    replaced by a placeholder, so only the prefix up to the last breakpoint
    counts. Built by the adapter's own request builder (plus breakpoints) and
    sent by its own ``_send_request`` with ``max_tokens: 0``: prefill only, no
    output billed. Marks the prefix warm so siblings skip the single-flight
    pre-warm. Returns the raw Anthropic usage (a cache write, or a read when
    the entry already exists).
    """
    from omnigent.llms.adapters import anthropic as mod

    install()
    headers = mod._build_headers(api_key_override=api_key)
    base = (base_url or mod._BASE_URL).rstrip("/")
    extra: dict[str, Any] = {"reasoning_effort": reasoning_effort} if reasoning_effort else {}
    metadata = await mod._get_anthropic_model_metadata(headers, base, model) if reasoning_effort else None
    payload = apply_cache_breakpoints(_originals.get("_chat_to_anthropic", mod._chat_to_anthropic)(
        list(messages), model, None, extra, model_metadata=metadata))
    usage = await _send_prewarm(headers, payload, base)
    mark_warm(prefix_key(payload))
    return usage


@contextmanager
def capture_usage() -> Iterator[list[dict[str, Any]]]:
    """Collect the raw Anthropic ``usage`` (cache fields included) of every response in this context."""
    sink: list[dict[str, Any]] = []
    token = _usage_sink.set(sink)
    try:
        yield sink
    finally:
        _usage_sink.reset(token)


def _record_usage(usage: dict[str, Any]) -> None:
    if (sink := _usage_sink.get()) is not None:
        sink.append(dict(usage))


# ── 3. Usage pass-through ─────────────────────────────────


def _cache_usage(usage: dict[str, Any]) -> dict[str, Any]:
    read = usage.get("cache_read_input_tokens") or 0
    return {
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens") or 0,
        "cache_read_input_tokens": read,
        "prompt_tokens_details": {"cached_tokens": read},
    }


# ── Install ───────────────────────────────────────────────


def install_workspace_header() -> None:
    """Send ``anthropic-workspace-id`` from ``ANTHROPIC_WORKSPACE_ID``.

    Keys not scoped to a workspace must name one on every request, and the
    adapter has no way to add headers. Independent of caching, so
    ``uninstall()`` leaves it in place. Idempotent.
    """
    from omnigent.llms.adapters import anthropic as mod

    orig = mod._build_headers
    if getattr(orig, "_adds_workspace", False):
        return

    def _build_headers(*args: Any, **kwargs: Any) -> dict[str, str]:
        headers = orig(*args, **kwargs)
        if workspace := os.environ.get("ANTHROPIC_WORKSPACE_ID"):
            headers["anthropic-workspace-id"] = workspace
        return headers

    _build_headers._adds_workspace = True  # type: ignore[attr-defined]
    mod._build_headers = _build_headers


def install() -> bool:
    """Patch ``omnigent.llms.adapters.anthropic``. Idempotent."""
    global _installed
    if _installed or not _enabled():
        return _installed
    try:
        from omnigent.llms.adapters import anthropic as mod
    except ImportError:
        return False
    if _installed:
        # Importing the adapter just now ran ``cachew.autoload``'s hook, which installed us.
        return True

    install_workspace_header()
    orig_chat_to_anthropic = mod._chat_to_anthropic
    orig_anthropic_to_chat = mod._anthropic_to_chat
    orig_stream_to_chat_chunks = mod._stream_to_chat_chunks
    orig_send_request = mod._send_request
    orig_stream_request = mod._stream_request

    def _chat_to_anthropic(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return apply_cache_breakpoints(orig_chat_to_anthropic(*args, **kwargs))

    def _anthropic_to_chat(resp: dict[str, Any]) -> dict[str, Any]:
        out = orig_anthropic_to_chat(resp)
        out["usage"].update(_cache_usage(resp.get("usage", {})))
        _record_usage(resp.get("usage", {}))
        return out

    async def _stream_to_chat_chunks(lines: AsyncIterator[str]) -> AsyncIterator[dict[str, Any]]:
        start_usage: dict[str, Any] = {}

        async def tap() -> AsyncIterator[str]:
            async for line in lines:
                if line.startswith("data: ") and '"message_start"' in line:
                    start_usage.update(json.loads(line[6:]).get("message", {}).get("usage", {}))
                yield line

        async for chunk in orig_stream_to_chat_chunks(tap()):
            if "usage" in chunk:
                chunk["usage"].update(_cache_usage(start_usage))
                _record_usage({**start_usage, "output_tokens": chunk["usage"].get("completion_tokens") or 0})
            yield chunk

    async def _send_request(headers: dict[str, str], payload: dict[str, Any], base_url: str, *a: Any, **kw: Any) -> dict[str, Any]:
        key = await ensure_warm(headers, payload, base_url)
        out = await orig_send_request(headers, payload, base_url, *a, **kw)
        mark_warm(key)
        return out

    async def _stream_request(headers: dict[str, str], payload: dict[str, Any], base_url: str, *a: Any, **kw: Any) -> AsyncIterator[dict[str, Any]]:
        key = await ensure_warm(headers, payload, base_url, streaming=True)
        first = True
        async for chunk in orig_stream_request(headers, payload, base_url, *a, **kw):
            if first:
                mark_warm(key)  # entry is readable once the response starts
                first = False
            yield chunk

    _originals.update(
        _chat_to_anthropic=orig_chat_to_anthropic,
        _anthropic_to_chat=orig_anthropic_to_chat,
        _stream_to_chat_chunks=orig_stream_to_chat_chunks,
        _send_request=orig_send_request,
        _stream_request=orig_stream_request,
    )
    mod._chat_to_anthropic = _chat_to_anthropic
    mod._anthropic_to_chat = _anthropic_to_chat
    mod._stream_to_chat_chunks = _stream_to_chat_chunks
    mod._send_request = _send_request
    mod._stream_request = _stream_request
    _installed = True
    _logger.info("cachew: Anthropic adapter patched for prompt caching")
    return True


def uninstall() -> None:
    """Restore the original adapter functions (used by the A/B experiment)."""
    global _installed
    if not _installed:
        return
    from omnigent.llms.adapters import anthropic as mod

    for name, fn in _originals.items():
        setattr(mod, name, fn)
    _originals.clear()
    _installed = False
