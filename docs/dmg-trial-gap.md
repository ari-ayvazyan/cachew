# Study 004: targetable subgroups of H3K27M DMG without matched trials

Question: which targetable subgroup of H3K27M diffuse midline glioma (DMG,
including DIPG) is common but has the fewest trials aimed at it?

This analysis generates hypotheses from public data. It is not a treatment
recommendation and not medical advice.

```bash
.venv/Scripts/python.exe -m lab --domain dmg --study studies/004-dmg-trial-gap
```

## Data

- **Patients:** 72 H3K27M tumours (61 H3.3, 11 H3.1) from 6 public cBioPortal
  cohorts. 53 come from DKFZ (Gröbner 2018), and all 11 H3.1 tumours are from
  that cohort. Each tumour is checked for alterations on 6 targetable axes
  ([data.py](../lab/domains/dmg/data.py)).
- **Trials:** from ClinicalTrials.gov:
  - 82 open DMG/DIPG trials;
  - 137 DMG/DIPG trials started since 2015;
  - 777 open high-grade glioma trials, adult and paediatric.
- **Trial labels:** Claude Haiku 4.5 assigns each trial the axis it targets.
  The skeptic checks these labels against a drug dictionary; agreement was
  86–100%.
- **Cost:** $0.33 of real API spend in total. The labels used in the final
  study cost $0.17. Reruns are served from cache.

Gap per axis = prevalence ÷ (1 + matched trials).

## Result: resolved, H-002 at 0.99 after 5 pre-registered tests

**The gap depends on the H3 variant: ACVR1 in H3.1, p53 loss in H3.3.**

| Axis | Patients altered | Open DMG trials | Open HGG trials (777) |
|---|---|---|---|
| p53 loss (TP53, PPM1D, MDM2/4) | 42/72 (58%); 38/61 of H3.3 | 0 | 1 (SGT-53, TP53 gene therapy) |
| ACVR1 | 9/72; 7/11 of H3.1 | 0 | 0 |
| RTK (PDGFRA, EGFR, MET…) | 17/72 | 3 | 33 |
| MAPK (BRAF, NF1…) | 11/72 | 1 | 18 |
| PI3K/mTOR | 7/72 | 3 | 10 |
| Cell cycle (CDK4/6) | 3/72 | 3 | 11 |

Ruled out, each by a cited run:

- p53 is the gap everywhere (R-003: in H3.1 it is ACVR1);
- PI3K;
- RTK once copy number is seen;
- the gap is an artifact of the narrow trial search (R-005: it persists across all 777 open HGG trials);
- "none of these";
- a post-hoc rule proposed by Haiku.

## What it suggests

- **p53 loss in H3.3 DMG is a structural gap.** 38 of the 42 p53-axis tumours
  are TP53-mutant, so MDM2 inhibitors, the main clinical p53 strategy, would
  not apply to them. The only open trial on this axis restores TP53 directly
  (SGT-53).
- **ACVR1-mutant H3.1 DMG has no open trial targeting it.** Our search found
  none among DMG or all high-grade glioma trials, despite preclinical ACVR1/ALK2
  inhibitor activity. Most H3.1 patients carry the mutation.

## Limits

- **Small H3.1 sample:** 11 tumours, all from one cohort. The H3.1 result
  rests on that cohort alone.
- **Cohort differences:**
  - Two cohorts have no copy-number data, so amplifications are undercounted there.
  - Panel and exome cohorts differ in which genes they cover.
- **Trial coverage:**
  - Only ClinicalTrials.gov was searched.
  - Basket trials registered under other conditions (for example, paediatric
    pan-tumour studies) are missed.
- **"Matched" means the trial's drug targets the axis.** It does not mean the
  trial enrols by genotype.
- **Labels the dictionary can't verify:** up to 40 Haiku labels involve drugs
  the dictionary doesn't know. The skeptic lists their trial IDs (K-004,
  K-005) for checking by hand.
- **Survival is not modelled:** public cohorts hold too few H3K27M patients
  with survival data, about 20.
