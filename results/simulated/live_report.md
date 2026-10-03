# Fan-out cache experiment — claude-opus-5-5, 8 sub-agents

Run at 2026-10-03 22:54:46. All token counts are the API's own `usage` fields.

| Arm | uncached input | cache write | cache read | output | cost (USD) | vs naive |
|---|---:|---:|---:|---:|---:|---:|
| naive | 140,817 | 0 | 0 | 12,000 | $0.8033 | +0.0% saved |
| cache_only | 237 | 35,172 | 123,102 | 12,000 | $0.4414 | +45.0% saved |
| compact_only | 30,481 | 0 | 0 | 13,500 | $0.3919 | +51.2% saved |
| compact_cache | 17,917 | 3,166 | 11,081 | 13,500 | $0.3597 | +55.2% saved |

## Cache verification

- PASS: every patched arm read the shared prefix from cache with at most 2 writes.

## Per-call usage (compact_cache)

| call | input | write | read | output |
|---|---:|---:|---:|---:|
| sub00 | 35 | 1,583 | 0 | 1,500 |
| sub01 | 29 | 0 | 1,583 | 1,500 |
| sub02 | 26 | 0 | 1,583 | 1,500 |
| sub03 | 27 | 0 | 1,583 | 1,500 |
| sub04 | 28 | 0 | 1,583 | 1,500 |
| sub05 | 27 | 0 | 1,583 | 1,500 |
| sub06 | 31 | 0 | 1,583 | 1,500 |
| sub07 | 30 | 0 | 1,583 | 1,500 |
| prewarm | 4 | 1,583 | 0 | 0 |
| compaction | 17,680 | 0 | 0 | 1,500 |
