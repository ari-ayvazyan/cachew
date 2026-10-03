"""Belief over competing hypotheses, and how much a test is expected to move it.

Each hypothesis predicts a probability for every outcome of a test. A test is
worth running when the hypotheses disagree about it: expected information gain
(bits) = H(beliefs) - E_outcome[H(beliefs | outcome)]. A test that every
hypothesis predicts the same way scores 0, however "confirming" it would feel.
"""

from __future__ import annotations

import math

Dist = dict[str, float]


def entropy(p: Dist) -> float:
    return -sum(v * math.log2(v) for v in p.values() if v > 0)


def normalize(p: Dist) -> Dist:
    s = sum(p.values())
    return {k: v / s for k, v in p.items()} if s > 0 else {k: 1 / len(p) for k in p}


def posterior(weights: Dist, preds: dict[str, Dist], outcome: str) -> Dist:
    return normalize({h: w * preds[h].get(outcome, 0.0) for h, w in weights.items()})


def expected_info_gain(weights: Dist, preds: dict[str, Dist]) -> float:
    outcomes = {o for p in preds.values() for o in p}
    h0 = entropy(weights)
    eig = 0.0
    for o in outcomes:
        p_o = sum(w * preds[h].get(o, 0.0) for h, w in weights.items())
        if p_o > 0:
            eig += p_o * (h0 - entropy(posterior(weights, preds, o)))
    return max(eig, 0.0)  # float noise can dip just below zero
