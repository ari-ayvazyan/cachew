# Project instructions

## Research stays out of git

This repo tracks only the generic foundation: the research loop (`lab/`), the
caching core (`cachew/`), tests and docs. **All research-related programs and
results must never be committed.**

- Put topic code (experiment scripts, engines, runners, skeptics, sources) in
  `topics/<name>/`, which is git-ignored (only `topics/README.md` is tracked).
- Put every study's output (artifacts, raw data, boards, ledgers) in
  `studies/<NNN-name>/`, which is git-ignored.
- Put scratch scripts and intermediate results in the session scratchpad, not
  in the repo.
- Write model weights, compressed models, checkpoints, caches and reports to
  those same ignored folders. `.gitignore` also blocks `*.safetensors`,
  `*.pt`, `*.ckpt`, `*.gguf` and `compressed/` as a backstop.
- Never `git add -f` anything from these locations, and never put research
  findings in tracked docs or the README. A change to `lab/` or `cachew/` is
  generic infrastructure and may be committed, but keep topic names, data and
  conclusions out of it.
- Before any commit, run `git status` and confirm no research file is staged.

See [topics/README.md](topics/README.md) for the topic layout.
