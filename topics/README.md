# Topics

One folder per research topic. Everything in here except this file is
git-ignored: a topic's code, sources, cached data and findings stay local, and
the repo only holds the generic loop (`lab/`) and the caching core (`cachew/`).

```bash
.venv/Scripts/python.exe -m lab --topic <name> --study studies/001-<name>
```

`--topic` takes a name under `topics/` or a path to any folder, so a topic can
also live outside this repo.

## Minimal layout

```
topics/<name>/
  __init__.py      the domain interface (see docs/research-loop.md, "Adding a topic")
  sources.md       passages the hypotheses cite (file, lines, sha)
  ...              any other modules; use relative imports (from . import x)
```

## Fanning out with prompt caching

When a run needs many model calls over the same material, use `lab.fanout`
instead of calling the API directly:

```python
from lab import fanout

LEDGER = fanout.Ledger(CACHE / "spend.json", budget_usd=3.0)

out = fanout.run(SYSTEM, brief, {"sub:01": "task 1", "sub:02": "task 2"}, ledger=LEDGER)
# out["calls"]: one record per sub-agent (plus the pre-warm), with usage and $
# out["summary"]: subagents, readers, writes, usd, usd_if_uncached
```

- **Shared prefix:** put everything the sub-agents share in `brief` (the cached
  prefix). Keep each task small. The model's minimum cacheable prefix applies:
  Haiku 4.5 needs 4096 tokens.
- **Task references:** name each sub-agent's share of the work by stable IDs,
  not positions.
- **Skeptic check:** add `fanout.cache_check(calls, n)` to the topic's audit.
  A run whose sub-agents did not read the cache is then rejected.
