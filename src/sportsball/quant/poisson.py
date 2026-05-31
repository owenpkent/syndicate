"""Poisson scoring models for discrete-event markets (totals, spreads)."""
from __future__ import annotations

import numpy as np
from scipy.stats import poisson


def poisson_probability(k: int, lam: float) -> float:
    """P(X = k) for a Poisson process with rate ``lam``."""
    return float(poisson.pmf(k, lam))


def joint_poisson_matrix(lam_a: float, lam_b: float, max_k: int = 50) -> np.ndarray:
    """Joint probability matrix for two independent Poisson scorers.

    Element ``[i, j]`` is P(A scores i) * P(B scores j); used to price spreads
    and totals by summing the relevant cells.
    """
    prob_a = poisson.pmf(np.arange(max_k), lam_a)
    prob_b = poisson.pmf(np.arange(max_k), lam_b)
    return np.outer(prob_a, prob_b)
