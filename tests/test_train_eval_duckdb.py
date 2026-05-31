"""Pure holdout-metric logic for the DuckDB train/eval script (no DuckDB needed)."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import train_eval_duckdb as ted  # noqa: E402


def _separable(n=400):
    rng = np.random.default_rng(0)
    X, y = [], []
    for _ in range(n):
        elo = rng.uniform(-400, 400)
        noise = rng.uniform(-1, 1, size=6)
        X.append([elo, *noise])
        y.append(1 if rng.uniform() < 1 / (1 + np.exp(-elo / 120)) else 0)
    return np.array(X), np.array(y)


def test_metrics_shape_and_bounds():
    X, y = _separable()
    m = ted.holdout_metrics(X, y, cols=[0], split=0.8)
    assert m["n_train"] == 320 and m["n_test"] == 80
    assert 0.0 <= m["brier"] <= 1.0
    assert 0.0 <= m["accuracy"] <= 1.0
    assert m["log_loss"] > 0


def test_signal_feature_beats_noise_only():
    # A model given the real Elo feature should out-predict one given pure noise.
    X, y = _separable()
    good = ted.holdout_metrics(X, y, cols=[0], split=0.8)
    noise = ted.holdout_metrics(X, y, cols=[1, 2, 3], split=0.8)
    assert good["log_loss"] < noise["log_loss"]
