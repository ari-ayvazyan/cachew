# Fan-out cache experiment — claude-haiku-4-5, 8 sub-agents

Run at 2026-10-03 23:04:23. All token counts are the API's own `usage` fields.

| Arm | uncached input | cache write | cache read | output | cost (USD) | vs naive |
|---|---:|---:|---:|---:|---:|---:|
| naive | 143,834 | 0 | 0 | 2,024 | $0.1540 | +0.0% saved |
| cache_only | 265 | 35,894 | 125,629 | 2,143 | $0.0684 | +55.6% saved |
| compact_only | 54,758 | 0 | 0 | 6,379 | $0.0867 | +43.7% saved |
| compact_cache | 18,317 | 9,112 | 31,892 | 6,371 | $0.0648 | +57.9% saved |

## Cache verification

- PASS: every patched arm read the shared prefix from cache with at most 2 writes.

## Per-call usage (compact_cache)

| call | input | write | read | output |
|---|---:|---:|---:|---:|
| sub00 | 36 | 4,556 | 0 | 274 |
| sub01 | 32 | 0 | 4,556 | 226 |
| sub02 | 30 | 0 | 4,556 | 225 |
| sub03 | 29 | 0 | 4,556 | 251 |
| sub04 | 31 | 0 | 4,556 | 190 |
| sub05 | 33 | 0 | 4,556 | 232 |
| sub06 | 34 | 0 | 4,556 | 234 |
| sub07 | 33 | 0 | 4,556 | 240 |
| prewarm | 7 | 4,556 | 0 | 0 |
| compaction | 18,052 | 0 | 0 | 4,499 |
