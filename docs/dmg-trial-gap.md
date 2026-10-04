# Study 004: targetable subgroups of H3K27M DMG without matched trials

Question: which targetable subgroup of H3K27M diffuse midline glioma (DMG,
including DIPG) is common but has the fewest trials aimed at it?

This analysis generates hypotheses from public data. It is not a treatment
recommendation and not medical advice.

```bash
.venv/Scripts/python.exe -m lab --domain dmg --study studies/004-dmg-trial-gap --max-rounds 6
```

## Data

- **Patients:** 72 H3K27M tumours (61 H3.3, 11 H3.1) from 6 public cBioPortal
  cohorts. 53 come from DKFZ (Gröbner 2018), and all 11 H3.1 tumours are from
  that cohort. Each tumour is checked for alterations on 6 targetable axes
  ([data.py](../lab/domains/dmg/data.py)).
- **Trials:** from ClinicalTrials.gov:
  - 82 open DMG/DIPG trials;
  - 137 DMG/DIPG trials started since 2015;
  - 777 open high-grade glioma (HGG) trials, adult and paediatric.

Gap per axis = prevalence ÷ (1 + matched trials).

## How each run labels trials: a cached fan-out

Each run fans out to 12 Claude Haiku 4.5 sub-agents through `cachew`
([classify.py](../lab/domains/dmg/classify.py)):

- **Shared prefix:** every sub-agent gets the same knowledge brief, the run's
  full trial list. That's about 8K tokens for the 82 open DMG trials and about
  90K for the 777 HGG trials.
- **Small task:** each sub-agent's own task names its slice of trials by NCT
  ID, so only that short part is uncached.
- **Caching:** the patched adapter writes the prefix once, pre-warms it for
  concurrent siblings, and the rest read it from cache.

The skeptic adds a `cache_used` check: at least N−2 sub-agents must read from
cache, with at most 2 writes and every trial answered. It also checks Haiku's
labels against a drug dictionary, including recall on known targets (at least
70%).

| Run | Trials | Sub-agents reading cache | Cost | Uncached |
|---|---|---|---|---|
| R-001 | 82 | 12/12 | $0.017 | $0.113 |
| R-002 | 82 | 12/12 | $0.017 | $0.113 |
| R-003 | 777 | 12/12 | $0.116 | $0.757 |
| R-004 | 777 | 12/12 | $0.117 | $0.757 |
| **Study** | | 48/48 | **$0.27** | **$1.74 (84% saved)** |

All 48 calls read from cache and none wrote. The same prefixes had been
written minutes earlier by test runs and were still within the 5-minute cache
window.

## Result: resolved, H-002 at 0.99 after 4 pre-registered tests

**The gap depends on the H3 variant: ACVR1 in H3.1, p53 loss in H3.3.**

| Axis | Patients altered | Open DMG trials | Open HGG trials (777) |
|---|---|---|---|
| p53 loss (TP53, PPM1D, MDM2/4) | 42/72 (58%); 38/61 of H3.3 | 0 | 1 (SGT-53, TP53 gene therapy) |
| ACVR1 | 9/72; 7/11 of H3.1 | 0 | 0 |
| RTK (PDGFRA, EGFR, MET…) | 17/72 | 1 | 14–18 |
| MAPK (BRAF, NF1…) | 11/72 | 1 | 9 |
| PI3K/mTOR | 7/72 | 3 | 11 |
| Cell cycle (CDK4/6) | 3/72 | 3 | 8 |

Ruled out, each by a cited run:

- p53 is the gap everywhere (R-003: in H3.1 it is ACVR1);
- PI3K;
- RTK once copy number is seen;
- the gap is an artifact of the narrow trial search (R-004: p53 stays the biggest gap across all 777 open HGG trials);
- "none of these";
- a post-hoc rule proposed by Haiku.

## What it suggests

- **p53 loss in H3.3 DMG is a structural gap.** 38 of the 42 p53-axis tumours
  are TP53-mutant, so MDM2 inhibitors, the main clinical p53 strategy, would
  not apply to them. The only open trial on this axis restores TP53 directly
  (SGT-53).
- **ACVR1-mutant H3.1 DMG has no open trial targeting it.** Our search found
  none among DMG or all HGG trials, despite preclinical ACVR1/ALK2 inhibitor
  activity. Most H3.1 patients carry the mutation.

## Limits

- **Small H3.1 sample:** 11 tumours, all from one cohort. The H3.1 result
  rests on that cohort alone.
- **Haiku labels are not exact:**
  - Recall on the 777 trials was 79%.
  - The same 777 trials received 14 RTK labels in one run and 18 in the other.
  - The conclusion holds in both runs, but individual counts can shift.
  - The skeptic lists the 24 labels the dictionary cannot verify (K-003, K-004), for checking by hand.
- **Cohort differences:**
  - Two cohorts have no copy-number data, so amplifications are undercounted there.
  - Panel and exome cohorts differ in which genes they cover.
- **Trial coverage:**
  - Only ClinicalTrials.gov was searched.
  - Basket trials registered under other conditions are missed.
- **"Matched" means the trial's drug targets the axis.** It does not mean the
  trial enrols by genotype.
- **Survival is not modelled:** public cohorts hold too few H3K27M patients
  with survival data, about 20.

## How we got here

These earlier attempts were discarded:

1. **Batched labelling, 20 trials per call.** There was no shared prefix, so
   the prompt cache was never used. This is replaced.
2. **One sub-agent per axis × evidence type, each searching the whole list.**
   Caching worked, but recall on the 777 trials collapsed: Haiku found 0 of the
   RTK trials. That flipped results to RTK.
3. **Slices given by index ("#22 to #28").** Haiku miscounted the list and
   labelled neighbouring trials. The skeptic rejected 7 of 8 runs.
4. **Slices given by NCT ID (current).** All audits pass.

Total real API spend across all attempts: $1.96 of the $3 cap.
