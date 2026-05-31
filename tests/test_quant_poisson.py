"""Poisson scoring primitives."""
import numpy as np
import pytest

from sportsball.quant.poisson import joint_poisson_matrix, poisson_probability


def test_pmf_matches_closed_form():
    # P(X=0 | lambda=2) = e^-2
    assert poisson_probability(0, 2.0) == pytest.approx(np.exp(-2.0), rel=1e-6)


def test_joint_matrix_shape_and_mass():
    mat = joint_poisson_matrix(1.5, 2.5, max_k=30)
    assert mat.shape == (30, 30)
    assert mat.sum() == pytest.approx(1.0, abs=1e-3)


def test_joint_matrix_is_outer_product():
    mat = joint_poisson_matrix(1.0, 1.0, max_k=10)
    # Symmetric for equal rates.
    assert np.allclose(mat, mat.T)
