# autolab: an Omnigent research team with a human approval gate

autolab runs empirical ML research (small, CPU-scale experiments) with a team
of Omnigent agents. The agents read the literature, propose competing
hypotheses, design an experiment, and pre-register predictions. You approve
the plan. The driver then runs the experiment in a sandbox, and the agents
audit the raw outputs and update their credence in each hypothesis. Each
round ends in a verdict that cites files, and the study ends when one
hypothesis wins or the round budget runs out.

```
                 ┌──────────────────────── Omnigent: PHASE plan ─────────────────────────┐
autolab plan ──▶ │ PI ─▶ scout_1..N ─▶ theorist_1..N ─▶ lead theorist ─▶ PI selects       │
                 │      (parallel)      (parallel)       (merge)                          │
                 │                    ─▶ experimenter ─▶ lead theorist (predictions)      │──▶ awaiting approval
                 └────────────────────────────────────────────────────────────────────────┘      │ revise "..."
autolab approve ──▶ hashes of experiment/ + predictions.json frozen ◀──────────────────────────────┘
        --run   ──▶ driver runs run.py in bwrap (only output/ + data cache writable, time-capped)
                 ┌──────────────── Omnigent: PHASE analyze ────────────────┐
                 │ PI ─▶ skeptic_1..N (parallel, one focus each) ─▶ judge   │──▶ ready | concluded
                 └──────────────────────────────────────────────────────────┘
```

## Quick start

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e '.[experiments]' \
  --index-url https://download.pytorch.org/whl/cpu --extra-index-url https://pypi.org/simple
.venv/bin/python -m unittest discover -s tests            # offline, no API calls

# credentials: Omnigent's claude-sdk harness uses your Claude login or ANTHROPIC_API_KEY
.venv/bin/autolab new my-study "Does X improve Y for small Z on CPU?" \
  --context "what you already know, constraints" --max-run-minutes 20
.venv/bin/autolab plan    studies/my-study    # ~5-15 min of agent work
.venv/bin/autolab show    studies/my-study    # read plan.md, predictions, code path
.venv/bin/autolab revise  studies/my-study "use 5 seeds and add a no-dropout control"
.venv/bin/autolab approve studies/my-study --run
.venv/bin/autolab status  studies/my-study
.venv/bin/autolab next    studies/my-study    # plan the next round, etc.
.venv/bin/autolab report  studies/my-study    # REPORT.md
```

## Browser UI

```bash
.venv/bin/autolab ui          # http://127.0.0.1:8765 (builds autolab/web on first start; needs npm)
```

- **Team graph** (React Flow): which agent is working, what the PI hands to whom, what comes back, and which
  files each agent reads and writes. Parallel instances stack in their role's column.
- **Timeline**: when each agent worked. Click or drag to replay any moment; long waits (e.g. for approval) collapse.
- **Approval card**: the chosen experiment, its predictions, *Approve & run* or *Request changes*.
- **Results**: credence per hypothesis and the prediction scorecard.

Every button runs the same CLI command in the background, so the UI and the terminal can be mixed. The
server detects running phases from the process table, so runs started from a terminal or by an earlier
server show as live too. It listens on localhost only. Frontend source: [autolab/web](../autolab/web)
(React, shadcn/ui, React Flow).

## Live trace

Every agent carries an Omnigent function policy ([autolab/trace.py](../autolab/trace.py)) that always
allows and appends each session event (the task it was handed, every model round-trip with token usage,
tool calls and results, its final report) to `rounds/RNN/trace.jsonl`.
[autolab/activity.py](../autolab/activity.py) turns that into agent states, hand-off and file edges, and
the timeline. Omnigent applies the root's policies to child sessions too, so each sub-agent event arrives
twice; the copy logged under `pi` is dropped by matching payload and session usage.

## Roles and what enforces them

| Role (Omnigent agent) | Writes | Extra tools | Phase |
|---|---|---|---|
| `pi` (root, orchestrator) | `selection.json`, `pi_notes.md` | dispatches sub-agents | both |
| `scout_1..N`, one angle each (prior results, methods, counter-evidence, theory) | `literature/RNN-scout_k.md` | `web_search` (keenable; `search_provider` in study.json) | plan |
| `theorist_1..N`, one angle each (mechanism, null and confounds, literature, boundary conditions) | `proposals/theorist_k.json` | — | plan |
| `theorist` (lead) | `candidates.json` (merged proposals), `predictions.json` | — | plan |
| `experimenter` | `plan.md`, `experiment/*` | `check_syntax` (compile only) | plan |
| `skeptic_1..N`, one focus each (numbers, code vs plan, statistics, run integrity) | `reviews/skeptic_k.json/.md`, `analysis/skeptic_k/*` | `run_analysis` (sandboxed, no network, read-only study) | analyze |
| `judge` | `verdict.json`, `findings.md` | — | analyze |

**Fan-out.** N defaults to 3 (`--fanout`, or *Parallel* in the UI). Each instance is its own Omnigent
agent with its own door, so parallel instances can only write their own files. The PI dispatches all
instances of a group in one turn and continues once every one has reported. The driver merges
deterministically: scout notes into `literature.md`, skeptic reviews into `review.json` (the strictest
verdict wins, so any `fail` fails the round), and the judge reads every individual review.

- **Prompts don't hold the rules; code does.**
  - No agent has a shell or native file tools: the bundle has no `os_env`, and `skills: none` keeps your Claude Code skills out.
  - Your claude.ai connectors are switched off with `ENABLE_CLAUDEAI_MCP_SERVERS=false`.
  - Each agent's only access to the study is its own stdio MCP server ([autolab/mcp_server.py](../autolab/mcp_server.py)). That server checks every write against [autolab/roles.py](../autolab/roles.py).
- **Agents never run experiments.** In the plan phase no role can execute code. In the analyze phase nobody can write the experiment or the predictions, so pre-registration holds.
- **Approval** stores sha256 hashes of `experiment/` and `predictions.json`.
  - `run` refuses to start if either one changed.
  - After analysis, the driver checks them again and blocks the verdict if they changed.
- **Proposing ≠ judging.** Theorists propose and the lead predicts; a separate judge scores. The judge is told to score each hypothesis against its pre-registered falsification condition.
- **Validation** ([autolab/validate.py](../autolab/validate.py)) checks every artifact for:
  - valid JSON
  - a catch-all hypothesis H0
  - at least 2 candidate experiments
  - a prediction for each live hypothesis
  - a credence in [0, 1] for each live hypothesis (each is the probability that hypothesis is true, judged on its own, since hypotheses may overlap)
  - code that compiles

  Problems go back to the PI up to twice. Anything still unresolved shows on the board and in `autolab show`.

## How Omnigent is used

Each phase starts `omni run <study>/.bundle --no-session -p "<PHASE ...>"`.
Omnigent spins up a server and runner, and the PI (claude-sdk harness)
dispatches sub-agents with `sys_session_send` and collects their results
with `sys_read_inbox`. The run ends when every agent is idle.

[autolab/bundle.py](../autolab/bundle.py) regenerates the bundle before every
phase, so each agent's MCP server is pinned to the right round, phase and
role. Model selection:

- `--model` sets the model for every agent (default `claude-sonnet-5-5`).
- `--pi-model` overrides the PI's model.
- `config.models` in `study.json` overrides any single role.

Omnigent limits found while building this:

- Haiku 4.5 looped on deferred-tool discovery as the orchestrator; use Sonnet 5.5 or larger.
- Sub-agents' `tools/python/*.py` local tools are refused at dispatch in 0.16.0, so the study tools are stdio MCP servers instead.
- A headless `omni run` follows a session for at most 30 minutes, so experiments run in the driver and not inside an agent.
- `web_fetch` runs as a background helper agent: the calling agent ends its turn, is reported finished, and resumes later. Scouts therefore only get `web_search`.
- The `duckduckgo` search backend returned a bot challenge from this machine (every query: "No results found."), so scouts use `keenable`.

## Files

Layout of a study folder: see the docstring of [autolab/study.py](../autolab/study.py).
Transcripts of each agent phase are in `rounds/RNN/logs/`, and the driver's
event log is `events.jsonl`.

## Limits

- **CPU only, no GPU found:** the experimenter is told this, and every run has a time limit (`--max-run-minutes`).
- **Fixed environment:** experiments may import only the installed packages (torch, torchvision, numpy, scipy, scikit-learn, pandas, matplotlib).
- **Network:** datasets download into `~/.cache/autolab/data`, the only writable path besides the run's `output/`.
- **No sandbox without bubblewrap:** without `bwrap` the run isn't sandboxed. `run.json` records `sandbox: none`, and the hash checks still apply.
- **No cost tracking:** `--no-session` runs don't keep Omnigent's usage database, so autolab records only the wall-clock time of each phase.
