# Cachew: a token-saving research agent

Cachew makes multi-agent research cheap: it researches once and briefs every
sub-agent from the same cached copy.

When an [Omnigent](https://omnigent.ai) agent finishes a research phase and
fans out to N sub-agents (`type: agent` tools with `pass_history: true`), each
sub-agent re-sends the whole parent history at full input price. Cachew
compacts that research once, caches it as a shared prefix, and adds only each
sub-agent's own instructions on top.

On the real Anthropic API, fan-outs cost 58% less and input spend fell 77%.
Seven of the eight sub-agents read the shared research from cache
([results](#live-run-on-the-anthropic-api-2026-10-03)).

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

The last step writes `cachew.pth` into the venv, so every Omnigent process
(server and runners) loads the patch. `--disable` removes it. Set `CACHEW=0`
to turn it off for one process, or `CACHEW_TTL=1h` to use the 1-hour cache
for slow fan-outs.

## What Omnigent 0.16.0 gets wrong

In `omnigent/llms/adapters/anthropic.py`, the adapter:

- joins all system messages into one plain string and never sets
  `cache_control`, so nothing is ever cached;
- drops `cache_read_input_tokens` and `cache_creation_input_tokens` from the
  usage it returns, so cost tracking couldn't see caching even if it happened.

## What the patch does ([cachew/patch.py](cachew/patch.py))

1. It sets cache breakpoints on the system prompt and on the last block before
   the final user turn, which is where the inherited or compacted context ends.
2. It pre-warms the cache with a single request. A cache entry becomes
   readable only once the request that writes it starts responding, so N
   siblings sent at the same moment would each pay the 1.25× write price.
   Instead, the first request goes ahead and the rest wait for it, then read
   from cache. When two or more are waiting on a non-streaming leader, one of
   them sends a `max_tokens: 0` pre-warm. That is a second write, but the
   fan-out still costs less than with no cache. Marker files coordinate this
   across processes, and sequential calls never pre-warm.
3. It passes the cache fields through in the usage it returns, and adds the
   OpenAI-style `prompt_tokens_details.cached_tokens`.

[cachew/compact.py](cachew/compact.py) builds the one-off compaction call and
the sub-agent message layout shown above.

## Verification

### Offline tests

```bash
.venv/Scripts/python.exe -m unittest discover -s tests -v
```

The patch tests run against [cachew/fake_api.py](cachew/fake_api.py), a
stand-in for `/v1/messages` that follows the documented caching rules:
exact-prefix keys, an entry that is readable only after prefill starts, and a
minimum cacheable size. They check where the breakpoints go and that usage is
passed through. They also check that 8 concurrent siblings cause at most 2
cache writes (8 without the pre-warm), that sequential fan-outs and normal
agent loops never pre-warm, and that compacting plus caching is the cheapest
option.

The simulated end-to-end run is in
[results/simulated/live_report.md](results/simulated/live_report.md). Input
cost drops 84%, from $0.56 to $0.09. Total cost drops 55%, a figure that
includes the simulation's inflated 1,500-token outputs.

The dry run also found a real bug. A 1.5K-token brief was cacheable but fell
below the original pre-warm threshold, so all 8 siblings wrote the cache,
which costs more than not caching at all. It is fixed and has a regression
test.

### Projected savings

These use published prices, a 60K-token research history, a 4K-token brief
and 800-token answers.

| model | N | naive | cache only | compact only | compact+cache | saved vs naive |
|---|---:|---:|---:|---:|---:|---:|
| claude-opus-5-5 | 4 | $1.027 | $0.403 | $0.451 | $0.410 | 60% |
| claude-opus-5-5 | 8 | $2.054 | $0.518 | $0.582 | $0.480 | 77% |
| claude-opus-5-5 | 16 | $4.109 | $0.749 | $0.845 | $0.621 | 85% |
| claude-sonnet-5-5 | 4 | $0.514 | $0.220 | $0.226 | $0.206 | 60% |
| claude-sonnet-5-5 | 8 | $1.027 | $0.301 | $0.291 | $0.243 | 76% |
| claude-sonnet-5-5 | 16 | $2.054 | $0.464 | $0.422 | $0.316 | 85% |

Compaction costs one extra call. On Opus 5.5, where cache reads cost 0.05× the
input price, caching the raw history alone is about as cheap at small N.
Compacting starts to pay off at around 8 sub-agents, and it keeps each
sub-agent's context small.

### Live run on the Anthropic API (2026-10-03)

The run used Claude Haiku 4.5, 8 sub-agents and a research history of about
18K tokens built from real Omnigent source files. Every request went through
Omnigent's own adapter. Token counts are the API's `usage` fields (summary in
[results/live_report.md](results/live_report.md), raw per-request data in
[results/live_results.json](results/live_results.json)):

| Arm | uncached input | cache write | cache read | output | cost (USD) | vs naive |
|---|---:|---:|---:|---:|---:|---:|
| naive (Omnigent today) | 143,834 | 0 | 0 | 2,024 | $0.1540 | baseline |
| cache_only | 265 | 35,894 | 125,629 | 2,143 | $0.0684 | 55.6% saved |
| compact_only | 54,758 | 0 | 0 | 6,379 | $0.0867 | 43.7% saved |
| **compact_cache** | 18,317 | 9,112 | 31,892 | 6,371 | **$0.0648** | **57.9% saved** |

In the compact_cache arm, sub-agents 2 to 8 each read the 4,556-token shared
prefix from cache. The only writes came from the first sub-agent and the one
pre-warm, and each sub-agent paid full price for only the ~30 tokens of its
own task. Input cost alone fell 77%, from $0.144 to $0.033. Output for the
compact arms includes the one-off 4.5K-token compaction.

With a history this small, caching alone does nearly as well. Compaction
gains more as the history and N grow (see the projection above). The whole
run cost about $0.33.

To reproduce it:

```bash
.venv/Scripts/python.exe -m cachew.experiment --model claude-haiku-4-5 --subagents 8 --effort "" --max-tokens 600 --brief-target 6000
```

You need `ANTHROPIC_API_KEY`, and also `ANTHROPIC_WORKSPACE_ID` if your key
isn't scoped to a workspace. Haiku 4.5 caches only prefixes of 4,096 tokens or
more, which is why `--brief-target` is set, and it doesn't support `effort`,
which is why `--effort ""` is passed.

The script runs the four arms through Omnigent's adapter against the API and
writes `results/live_report.md` and `results/live_results.json`, with the
`usage` of every request. It exits non-zero if any cached arm failed to read
from cache. A run costs roughly $1 to $3 on Opus 5.5, and about half that with
`--model claude-sonnet-5-5`.

## Research loop (`lab/`)

`lab/` is a research loop built on Cachew that works for any domain. It keeps
several competing hypotheses open, including "none of these is right", and
picks the next test by expected information gain per dollar. A skeptic
audits every run from its raw data. A judge, separate from the agent that
proposes hypotheses, makes the decisions and cites run IDs for each one. A
progress board shows where the time and tokens go.

The design is in [docs/research-loop.md](docs/research-loop.md); studies are
described in [studies/](studies/README.md).

```bash
.venv/Scripts/python.exe -m lab --study studies/003-my-run
```

## Cachew Studio (`lab/studio/`)

Cachew Studio is a web UI for starting a research run and following the team
while it works. Each run is an Omnigent session. A PI agent plans the work and
hands it out through `sys_session_send` to scouts, theorists, a lead theorist,
an experimenter, skeptics and a judge. Every agent runs on the cachew harness,
an Omnigent community harness plugin in [plugin/](plugin/). Its model calls go
through Omnigent's `AnthropicAdapter` with the Cachew patch applied, so no
request reaches Anthropic by any other route.

The UI follows the discovery loop: question, evidence, hypotheses,
experiment, approval, review and decision. Each stage has its own tab, and a
research record collects the results. The Team tab shows the agent graph.

Before each fan-out, the PI publishes a knowledge brief and pre-warms it with
one call (`cachew.patch.prewarm`). Every sub-agent then reads that brief from
the cache. Each run page opens with what the shared cache saved the agent
swarm: real spend compared with the same calls made without a cache, after
paying for the pre-warm calls. It also shows one write and N reads per brief,
and whether the team members' system prefixes were byte-identical.

The scientist can approve, change or reject the experiment before it goes to
review. The PI program enforces this gate and waits up to 45 minutes for an
answer.

Runs use the Studio's shared key by default. That key allows Sonnet 5.5 at
low effort or Haiku 4.5, swarms of up to 8 agents, and a budget of up to $3
per run. Scientists who enter their own Anthropic API key can use every model
and effort level with any swarm size. The Studio stores that key in a file
only the owner can read and deletes it when the run ends. A run started with
its own key never falls back to the shared key, so its calls can't be billed
to someone else.

```bash
uv pip install --python .venv/Scripts/python.exe -e plugin   # registers the cachew harness with Omnigent
.venv/Scripts/python.exe -m lab.studio                         # http://127.0.0.1:8787 (builds the UI with bun on first start)
```

The UI is built with [bun](https://bun.sh)
(`cd lab/studio/web && bun install && bun run build`). Runs are written to
`studies/`, which git ignores. On Windows the Studio starts Omnigent with
`PYTHONUTF8=1`, because Omnigent's host daemon otherwise crashes on non-ASCII
console output.
