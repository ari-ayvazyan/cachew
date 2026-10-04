# Studies

One folder per study, generated locally and not tracked in git. Each folder has a `board.md` (terse) and a `board.html` (click to inspect). To regenerate:

```bash
.venv/Scripts/python.exe -m lab --study studies/002-fanout-crossover
```

| Study | Question | Ended | Key IDs |
|---|---|---|---|
| 001 | Cheapest fan-out strategy? | unresolved · skeptic failed R-003, R-005 | K-003, K-005 → fake clock bug, fixed |
| 002 | Cheapest fan-out strategy? | resolved · H-006 at 0.99 | R-001 → H-006 · D-012 |
| 003 | Cheapest fan-out strategy? (gated pre-warm) | unresolved · H-005 at 0.90, no test splits it from H-004 | R-006 → ✗H-007 · D-013 |

Backend: simulated (`cachew/fake_api.py`), not the real API. Study 001's skeptic failures came from a fake-API clock bug that has since been fixed, so rerunning it now gives a different result.
