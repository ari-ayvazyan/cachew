# Research loop (`lab/`)

A loop in which competing hypotheses make predictions, tests are chosen
because the hypotheses disagree about them, a skeptic audits every run against
its raw data, and a judge who proposed nothing decides what happens next. Every
decision cites run IDs, and "unresolved" is a valid ending.

```
            ┌───────────── refine / continue ─────────────┐
            ▼                                             │
 proposer ──T,T,T──▶ selector ──X──▶ runner ──R──▶ skeptic ──K──▶ judge ──D──▶ stop?
 (H, T)             (EIG/$, D)      (raw data)   (4 checks)      (beliefs)    resolved
                                                                              unresolved
```

## The seven principles and where each one lives

| # | Principle | Mechanism |
|---|---|---|
| 1 | Results drive decisions | Beliefs are recomputed from accepted runs only ([loop.py](../lab/loop.py) `beliefs`). Every `D` carries `cites`. A surprise sets `next: refine`, which changes the hypothesis set. Stops: `resolved` or `unresolved` (round budget, no test discriminates, "none" leads). |
| 2 | Try to disprove | Hypotheses are machine-readable rules with soft (85%) predictions; "none of these is right" is always present. Hypotheses below 2% are marked refuted with the run that refuted them. |
| 3 | Compare before acting | The proposer must hand over at least 2 tests. The selector scores each one by expected information gain (bits), estimated live cost ($) and feasibility, and records the table and the reason in `D:select` ([infogain.py](../lab/infogain.py)). |
| 4 | Artifacts, not summaries | Roles exchange handoffs (`HO`) holding only IDs: hypotheses, source passages (file, lines, sha), spec, code version (git + tree hash), data version (input hash), result file. |
| 5 | Progress board | `board.md` / `board.html`, rebuilt after every step from artifacts alone: one line per step, `←` shows why. See [Board](#board) ([board.py](../lab/board.py)). |
| 6 | Proposing ≠ judging | Each artifact records its author role, and `assert_independent` refuses a score from the role that wrote the thing being scored ([roles.py](../lab/roles.py)). The skeptic audits from raw data only. |
| 7 | Organized, small files | One JSON file per artifact, a folder per kind, created on first use. The store refuses any file over 64 KB, so raw data is split into one part per arm ([store.py](../lab/store.py)). |

## Roles

| Role | Writes | May not score | Gets |
|---|---|---|---|
| proposer | `H` hypotheses, `T` tests | anything | last decision, accepted runs |
| selector | `D:select`, `X` spec with pre-registered predictions | its own `T` | `HO` with T IDs |
| runner | `R` result, `runs/R-xxx/raw/*.json` | its own run | `HO` with spec, versions |
| skeptic | `K` review | its own run | `HO` with result file |
| judge | `D:update` / `D:discard`, status of `H` | `H` it wrote, `R` it ran | `HO` with run + review |

Here the roles are deterministic Python. An LLM agent can fill any role
because the contract is the artifact schema, not the code. Give it the handoff
IDs and the store, and accept only artifacts that pass the same checks.

## Selection

For each candidate test, every live hypothesis predicts a distribution over
outcomes:

```
EIG(test) = H(beliefs) − Σ_o P(o) · H(beliefs | o)      (bits)
score     = EIG / (estimated live $ + 0.05)             (0 if infeasible)
```

When all hypotheses predict the same outcome, the test scores 0 however
reassuring it would be. When no candidate reaches 0.01 bits, the selector stops
the study as `unresolved`.

## Skeptic checks

Each topic writes its own audit, recomputed from `runs/R-xxx/raw/*.json` only.
Every topic should cover these four checks:

- **intervention_ran:** the thing under test actually happened. For a cached
  fan-out, include `lab.fanout.cache_check`: at least N−2 sub-agents read the
  shared prefix, and at most the leader and the pre-warm wrote it.
- **controls:** baselines are present, the comparisons are fair and the sample
  is large enough.
- **no_leakage:** the inputs hash to the spec's data version, and the
  predictions were registered before the run. The cited source passages are
  unchanged and the predictions have not drifted.
- **claims_match_raw:** every number in the result, and the outcome, is
  recomputed from the raw data.

A failed review means the run is discarded (`D:discard`) and never counted.

## Board

`board.md` is the terse version: status, time per role, tokens by type, one
bar per hypothesis, one glyph per request, one line per step.

`board.html` is self-contained (no server) and interactive. It reads from top
to bottom as overview → reasoning → detail:

- **Now:** one line saying what is happening (live phase) or how it ended.
- **KPI strip:** answer or leading hypothesis, rounds, time by role, tokens by
  type, and what the chosen tests would cost on the real API.
- **Research map:** one column per round, from the starting hypotheses to the
  answer. Each column shows the tests considered (bar = expected information),
  the one that ran with a mini fan-out (one square per request), the result and
  audit, and the decision (test again, rethink, stop; ✗ ruled out, + new).
  While a study runs, the current column pulses and pending nodes are dashed.
- **Steps:** one card per round, newest first. Each step is one plain sentence
  with a role icon (Propose, Choose, Run, Audit, Decide) and its artifact IDs.
  Only the selected round is expanded.
- **Round N result:** one card per arm (raw-data part) with its value, tokens
  and one square per request (uncached, cache write, cache read, pre-warm). The
  best arm is tagged. Below are the audit checks in plain words and, collapsed,
  the request timeline.
- **How confident are we in each hypothesis?:** a table of the probability of
  each hypothesis after every round. ✗ = ruled out, outlined = proposed then.
- **Where time goes / where tokens go:** seconds per round by role, and a
  run × arm matrix of tokens or $.

Clicking a round anywhere selects it everywhere (kept in the URL hash). Every
ID and every request square opens its raw data in an inspector. Run with
`--pace 1` to slow the loop down and watch the board (it refreshes every 2 s).

## Folder layout

```
studies/<NNN-name>/
  study.json  ledger.jsonl  board.md  board.html
  sources/ hypotheses/ proposals/ specs/ decisions/ reviews/ handoffs/
  runs/R-001/result.json
  runs/R-001/raw/<part>.json        one file per arm / raw-data part
```

## Adding a topic

A topic is a folder with `__init__.py`, kept outside git under `topics/` or
anywhere else. Run it with `python -m lab --topic <name or path>`; see
[topics/README.md](../topics/README.md).

**Required:** the module defines:

```
question, outcomes, sources, hypotheses, predict, feasible, estimate_usd, code_paths,
propose_tests, refine, input_version, run, audit, short, point_kind, glyph, legend
```

**Optional, for the board:**

- `describe`, `arm_labels`, `arm_short`, `outcome_labels`, `check_labels`, `point_labels`, `point_colors`;
- `board_text`, which overrides UI wording such as "best" or "Result:";
- `baseline`, the arm that savings are measured against;
- `hidden_parts`, the raw parts to leave off the board;
- `arm_view`, which supplies cards and sentences for results that are not arm comparisons;
- `spent_usd`, the real API spend.

Runs that fan out to many model calls should use `lab.fanout`, a cached fan-out
over one shared prefix with a spend ledger, and add `lab.fanout.cache_check` to
their audit.

The selector never re-runs a test on input data an earlier run already used
(same `input_version`), so one observation is not counted as two
confirmations. The coin-flip toy topic in
[tests/test_lab.py](../tests/test_lab.py) is a complete minimal example.
