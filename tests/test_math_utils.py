"""Unit tests for the quantitative core (src/analytics_engine/math_utils.py)."""
import numpy as np
import pytest

from math_utils import (
    calculate_ev,
    calculate_kelly_fraction,
    poisson_probability,
    calculate_joint_poisson_prob,
)


class TestExpectedValue:
    def test_fair_coin_at_even_odds_has_zero_ev(self):
        # P=0.5 at decimal odds 2.0 is a break-even proposition.
        assert calculate_ev(0.5, 2.0) == pytest.approx(0.0)

    def test_positive_edge(self):
        # We think it's a 60% shot but the market prices it at 2.0 (50%).
        assert calculate_ev(0.6, 2.0) == pytest.approx(0.2)

    def test_negative_edge(self):
        assert calculate_ev(0.4, 2.0) == pytest.approx(-0.2)


class TestKellyFraction:
    def test_quarter_kelly_scales_full_kelly(self):
        ev = calculate_ev(0.6, 2.0)  # 0.2
        full = calculate_kelly_fraction(ev, 2.0, multiplier=1.0)
        quarter = calculate_kelly_fraction(ev, 2.0, multiplier=0.25)
        assert quarter == pytest.approx(full * 0.25)

    def test_full_kelly_value(self):
        # f* = EV / (odds - 1) = 0.2 / 1.0 = 0.2
        assert calculate_kelly_fraction(0.2, 2.0, multiplier=1.0) == pytest.approx(0.2)

    def test_negative_ev_never_stakes(self):
        assert calculate_kelly_fraction(-0.1, 2.0) == 0

    def test_odds_at_or_below_one_returns_zero(self):
        assert calculate_kelly_fraction(0.5, 1.0) == 0
        assert calculate_kelly_fraction(0.5, 0.9) == 0


class TestPoisson:
    def test_pmf_matches_known_value(self):
        # P(X=0 | lambda=2) = e^-2 ≈ 0.13534
        assert poisson_probability(0, 2.0) == pytest.approx(np.exp(-2.0), rel=1e-6)

    def test_joint_matrix_shape_and_normalization(self):
        max_k = 30
        mat = calculate_joint_poisson_prob(1.5, 2.5, max_k=max_k)
        assert mat.shape == (max_k, max_k)
        # Two independent PMFs truncated at a high max_k should sum to ~1.
        assert mat.sum() == pytest.approx(1.0, abs=1e-3)
