"""Integration: the availability feature is real, not inert, when data has signal.

The unit tests prove ``availability_diff`` is *neutral* with no data. This proves
the other half of the contract: feed a complete synthetic season whose outcomes
genuinely depend on availability, train the real model end-to-end, and show that

  1. availability adds out-of-sample lift (lower holdout log-loss than without it),
  2. the learned coefficient has the right sign, and
  3. it flows through the serve path — a more-available home team is favored.

If a future change silently dropped availability from train or serve, this fails.
"""
import sys
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from sportsball.pipelines._elo import walk_forward
from sportsball.quant import features as feat
from sportsball.quant.features import TeamSnapshot, neutral_snapshot
from sportsball.quant.models import ModelBundle

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import train_eval_duckdb as ted  # noqa: E402

from synth import make_season  # noqa: E402

K, HFA = 20.0, 50.0
AVAIL_IDX = feat.FEATURE_ORDER.index("availability_diff")


def _walk(availability_pit):
    rng = np.random.default_rng(7)
    results, avail = make_season(rng)
    rows, snaps = walk_forward(results, K, HFA, mov_enabled=True, carry=0.75,
                               gap_days=90, form_window=10,
                               availability_pit=availability_pit and avail)
    X = np.array([r.features for r in rows])
    y = np.array([1 if r.actual >= 1.0 else 0 for r in rows])
    return X, y, snaps


def test_availability_adds_holdout_lift():
    # Same games, same features — only difference is whether availability is known.
    X, y, _ = _walk(availability_pit=True)
    full = ted.holdout_metrics(X, y, cols=list(range(feat.N_FEATURES)), split=0.8)
    without = ted.holdout_metrics(X, y, cols=list(range(AVAIL_IDX)), split=0.8)
    # Knowing tonight's availability should reduce out-of-sample log-loss.
    assert full["log_loss"] < without["log_loss"]


def test_learned_coefficient_is_positive():
    X, y, _ = _walk(availability_pit=True)
    model = Pipeline([("s", StandardScaler()),
                      ("lr", LogisticRegression(max_iter=1000))]).fit(X, y)
    coef = model.named_steps["lr"].coef_[0]
    # More home availability advantage -> higher home win prob.
    assert coef[AVAIL_IDX] > 0
    # ...and it's a non-trivial contributor, not numerical dust.
    assert abs(coef[AVAIL_IDX]) > 0.05


def test_serve_path_favors_the_more_available_team():
    X, y, _ = _walk(availability_pit=True)
    model = Pipeline([("s", StandardScaler()),
                      ("lr", LogisticRegression(max_iter=1000))]).fit(X, y)
    meta = {"schema_version": feat.SCHEMA_VERSION, "feature_order": feat.FEATURE_ORDER,
            "n_features": feat.N_FEATURES, "hfa": HFA, "temperature": 1.0}
    # Two unknown (neutral, equal-Elo) teams: only availability differs.
    bundle = ModelBundle(model=model, snapshots={}, meta=meta)
    healthy = bundle.predict_home_prob("Home", "Away",
                                       home_availability=0.95, away_availability=0.55)
    depleted = bundle.predict_home_prob("Home", "Away",
                                        home_availability=0.55, away_availability=0.95)
    assert healthy > depleted


def test_inert_without_data_matches_neutral():
    # With no availability_pit the column is all-zero; the feature must not move
    # predictions at all (sanity that "inert" really means inert).
    X, _, _ = _walk(availability_pit=False)
    assert np.allclose(X[:, AVAIL_IDX], 0.0)
