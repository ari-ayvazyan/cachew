# Cachew — Token Maxxing Research Agent

**Research once, brief everyone.** Cachew makes multi-agent research cheap.
When an [Omnigent](https://omnigent.ai) agent finishes a research phase and
fans out to N sub-agents (`type: agent` tools with `pass_history: true`), every
sub-agent re-sends the whole parent history at full input price. Cachew
compacts that research once, caches it as a shared prefix, and appends only
the per-subagent instructions on top.

**Measured on the real Anthropic API: 58% cheaper fan-outs, 77% less input
spend, with 7 of 8 sub-agents reading the shared research from cache**
([results](#live-proof-done-2026-10-03-real-anthropic-api)).

```
system     parent system prompt                ┐
user       <knowledge_brief> compacted </...>  │ shared, byte-identical -> cached once
assistant  acknowledgement                     ┘ <- cache breakpoint
user       per-subagent instructions             <- the only uncached part
```

## Setup

```bash
uv venv --python 3.12 .venv
```
```bash
uv pip install --python .venv/Scripts/python.exe omnigent
```
```bash
.venv/Scripts/python.exe scripts/enable_cachew.py
```

The last step writes `cachew.pth` into the venv so the patch loads
in every Omnigent process (server, runners). `--disable` removes it.
`CACHEW=0` turns it off per process; `CACHEW_TTL=1h`
uses the 1-hour cache for slow fan-outs.

## What was wrong in Omnigent 0.16.0

`omnigent/llms/adapters/anthropic.py`:

- joins all system messages into one plain string and never sets
  `cache_control`, so nothing is ever cached;
- drops `cache_read_input_tokens` / `cache_creation_input_tokens` from the
  usage it returns, so caching would be invisible to cost tracking.

## What the patch does ([cachew/patch.py](cachew/patch.py))

1. **Breakpoints** on the system prompt and on the last block before the final
   user turn (the end of the inherited or compacted context).
2. **Single-flight pre-warm.** A cache entry is readable only once the request
   writing it starts responding, so N siblings fired together would each pay
   the 1.25× write. The first request goes through; the first concurrent
   sibling sends one `max_tokens: 0` pre-warm (prefill only, no output billed)
   and the others wait for it, then read. Marker files coordinate this across
   processes. Sequential sub-agents and single-agent loops never pre-warm.
3. **Usage pass-through** of the cache fields, plus OpenAI-style
   `prompt_tokens_details.cached_tokens`.

[cachew/compact.py](cachew/compact.py) builds the one-off compaction
call and the sub-agent message layout above.

## Verification

### Offline (done)

```bash
.venv/Scripts/python.exe -m unittest discover -s tests -v
```

10 tests against [cachew/fake_api.py](cachew/fake_api.py), a stand-in
for `/v1/messages` that implements the documented caching rules (exact-prefix
keys, entry readable only after prefill starts, minimum cacheable size).
They check breakpoint placement, usage pass-through, that 8 concurrent
siblings produce at most 2 cache writes, that without the pre-warm they would
produce 8, that sequential fan-outs and normal agent loops never pre-warm, and
that compact + cache is the cheapest arm.

The simulated end-to-end run is in
[results/simulated/live_report.md](results/simulated/live_report.md): the
simulated input side drops 84% (from $0.56 to $0.09); total cost including
the inflated 1,500-token simulated outputs drops 55%.

The dry run also caught a real bug: a 1.5K-token brief fell below the
original pre-warm threshold while still being cacheable, so all 8 siblings
wrote the cache, which costs more than not caching. Fixed, with a regression test.

### Projected savings (published prices, 60K-token research, 4K brief, 800-token answers)

| model | N | naive | cache only | compact only | compact+cache | saved vs naive |
|---|---:|---:|---:|---:|---:|---:|
| claude-opus-5-5 | 4 | $1.027 | $0.403 | $0.451 | $0.410 | 60% |
| claude-opus-5-5 | 8 | $2.054 | $0.518 | $0.582 | $0.480 | 77% |
| claude-opus-5-5 | 16 | $4.109 | $0.749 | $0.845 | $0.621 | 85% |
| claude-sonnet-5-5 | 4 | $0.514 | $0.220 | $0.226 | $0.206 | 60% |
| claude-sonnet-5-5 | 8 | $1.027 | $0.301 | $0.291 | $0.243 | 76% |
| claude-sonnet-5-5 | 16 | $2.054 | $0.464 | $0.422 | $0.316 | 85% |

Compaction costs one extra call, so at small N on Opus 5.5 (cache reads at
0.05× input) caching the raw history alone is about as cheap; compacting pays
off from about 8 sub-agents up and keeps sub-agent context small.

### Live proof (done, 2026-10-03, real Anthropic API)

Claude Haiku 4.5, 8 sub-agents, an ~18K-token research history built from real
Omnigent source files, all requests sent through Omnigent's own adapter.
Token counts are the API's `usage` fields
([results/live_report.md](results/live_report.md), raw per-request data in
[results/live_results.json](results/live_results.json)):

| Arm | uncached input | cache write | cache read | output | cost (USD) | vs naive |
|---|---:|---:|---:|---:|---:|---:|
| naive (Omnigent today) | 143,834 | 0 | 0 | 2,024 | $0.1540 | — |
| cache_only | 265 | 35,894 | 125,629 | 2,143 | $0.0684 | 55.6% saved |
| compact_only | 54,758 | 0 | 0 | 6,379 | $0.0867 | 43.7% saved |
| **compact_cache** | 18,317 | 9,112 | 31,892 | 6,371 | **$0.0648** | **57.9% saved** |

Caching verified: in the compact_cache arm sub-agents 2–8 each read the
4,556-token shared prefix from cache, and the only writes were the first
sub-agent and the one pre-warm. Each sub-agent paid full price for only ~30
tokens of its own task. On the input side alone the cost fell 77%, from $0.144
to $0.033. The compact arms' output includes the one-off 4.5K-token compaction.

At this small history size caching alone is nearly as good; compaction's edge
grows with history size and N (see the projection above). The whole run cost
about $0.33.

To reproduce:

```bash
.venv/Scripts/python.exe -m cachew.experiment --model claude-haiku-4-5 --subagents 8 --effort "" --max-tokens 600 --brief-target 6000
```

Needs `ANTHROPIC_API_KEY`, plus `ANTHROPIC_WORKSPACE_ID` for keys that aren't
scoped to a workspace. Haiku 4.5 only caches prefixes of 4,096 tokens or more,
hence `--brief-target`; it doesn't support `effort`, hence `--effort ""`.

Runs the four arms through Omnigent's real adapter against the API and writes
`results/live_report.md` plus `results/live_results.json` with every request's
`usage`. Exits non-zero if any cached arm failed to read from cache. Costs
roughly $1–3 on Opus 5.5 (`--model claude-sonnet-5-5` halves it).

## Research loop (`lab/`)

A domain-agnostic research loop built on top of Cachew:

- competing hypotheses, including "none of these is right";
- tests chosen by expected information gain per dollar;
- a skeptic that audits every run from its raw data;
- a judge, separate from the proposer, whose every decision cites run IDs;
- an interactive progress board showing where time and tokens go.

Research topics live outside git, in [topics/](topics/README.md) or any other
folder. Fan-outs use `lab.fanout`: one cached prefix shared by all sub-agents,
plus a spend cap. Design: [docs/research-loop.md](docs/research-loop.md).

```bash
.venv/Scripts/python.exe -m lab --topic <name or folder> --study studies/001-my-question
```

## Cachew Studio (`lab/studio/`)

A web UI that starts a research run and shows the team while it works. Each
run is an Omnigent session. A PI agent plans the work and fans it out, through
`sys_session_send`, to scouts, theorists, a lead theorist, an experimenter,
skeptics and a judge. Every agent runs on the **cachew harness**, an Omnigent
community harness plugin in [plugin/](plugin/). Its model calls go through
Omnigent's `AnthropicAdapter` with the Cachew patch, so nothing reaches
Anthropic any other way.

Before each fan-out, the PI publishes a knowledge brief and pre-warms it with
one call (`cachew.patch.prewarm`). Every sub-agent then reads that brief from
the cache. The UI shows:

- what this saved: real spend against the same calls with no cache, net of the
  pre-warm calls;
- one write and N reads per brief;
- whether the members' system prefixes were byte-identical.

```bash
uv pip install --python .venv/Scripts/python.exe -e plugin   # registers the cachew harness with Omnigent
.venv/Scripts/python.exe -m lab.studio                         # http://127.0.0.1:8787 (builds the UI with bun on first start)
```

The UI is built with [bun](https://bun.sh) (`cd lab/studio/web && bun install && bun run build`).
Runs are written to `studies/` (git-ignored). On Windows the studio starts
Omnigent with `PYTHONUTF8=1`, because Omnigent's host daemon otherwise crashes
on non-ASCII console output.
