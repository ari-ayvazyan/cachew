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

## Skeptic checks (fan-out domain)

Every check is recomputed from `runs/R-xxx/raw/*.json`:

- **intervention_ran:** cached arms sent `cache_control` and at least N−2 sub-agents read; uncached arms never did.
- **controls:** the naive arm is present, and every arm uses the same task set and the same N.
- **no_leakage:** the per-agent task is never inside the cached prefix, and each arm starts cold. The inputs must hash to the spec's data version, and predictions must have been registered before the run. The cited source passages must be unchanged, and the predictions must not have drifted.
- **claims_match_raw:** dollars per arm and the winner, recomputed from raw usage.

A failed review means the run is discarded (`D:discard`) and never counted.

## Board

`board.md` is the terse version: status, time per role, tokens by type, one
bar per hypothesis, one glyph per request, one line per step.

`board.html` is self-contained (no server) and interactive:

- **KPI strip:**
  - verdict, or the leading hypothesis while unresolved;
  - runs accepted and discarded;
  - time split by role;
  - tokens split by uncached, cache write, cache read and output;
  - what the chosen tests would cost on the real API.
- **Where time goes:** one stacked bar per round, by role (proposer, selector, runner, skeptic, judge, board rendering). Click a bar to open that round's run.
- **Beliefs over runs:** one line per hypothesis. Refuted hypotheses are dashed, and post-hoc ones start where they were proposed. Hover to highlight.
- **Where tokens go:** a run × arm matrix of stacked token bars on one shared scale; switch between tokens and $. ★ marks the cheapest arm.
- **Fan-out:**
  - the tests that were compared, with information gain and $;
  - a timeline with one bar per request in each arm, colored by uncached, write, read or pre-warm.
  - Click a bar to see its raw data point.
- **Steps:** the step log, filterable by role.

Every ID opens its artifact in an inspector. The selected run is kept in the
URL hash.

## Folder layout

```
studies/<NNN-name>/
  study.json  ledger.jsonl  board.md  board.html
  sources/ hypotheses/ proposals/ specs/ decisions/ reviews/ handoffs/
  runs/R-001/result.json
  runs/R-001/raw/{naive,cache_only,compact_only,compact_cache,compaction}.json
```

## Adding a domain

A domain is a module with `question, outcomes, sources, hypotheses, predict,
feasible, estimate_usd, code_paths, propose_tests, refine, input_version, run,
audit, short, point_kind, glyph, legend`. See
[lab/domains/fanout](../lab/domains/fanout/) and the coin-flip toy domain in
[tests/test_lab.py](../tests/test_lab.py).

## Case study: when does compaction beat caching?

```bash
.venv/Scripts/python.exe -m lab --study studies/003-my-run
```

All runs use the real patched Omnigent adapter against
[cachew/fake_api.py](../cachew/fake_api.py), so they are free but simulated.
`estimate_usd` is what the same test would cost on the real API, and the
selector pays it as if it were real.

- **`studies/001-fanout-crossover`: stopped unresolved.**
  - The skeptic failed R-003 and R-005 on `intervention_ran`: siblings wrote the cache instead of reading it.
  - Root cause: the fake marked a pre-warm entry readable only after `start + prefill`, and `asyncio.sleep` can wake one clock tick early on Windows. The fake was fixed, with a regression test (`TestFakeClock`).
  - Without the skeptic, those runs would have counted as evidence.
- **`studies/002-fanout-crossover`: resolved in 6 rounds.**
  - Refuted: "compaction always wins", "raw cache always wins", "compaction wins iff N ≥ 8", and the textbook formula in `pricing.py`, which assumes 1 cache write.
  - R-001 surprised the leading hypothesis, so the proposer read the raw data, saw 2 writes per cached arm (leader + pre-warm), and proposed the formula with 2 writes (H-006).
  - H-006 was then confirmed on 5 tests it had not seen (belief 0.99).
  - Product finding: at N=2, caching costs more than not caching (R-006), because the leader and the pre-warm both write the cache.
