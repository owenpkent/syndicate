"""Shared Elo walk-forward used by the optimizer and trainer."""
import pytest

from sportsball.pipelines._elo import mean_log_loss, walk_forward

# (date, home, away, home_score, away_score)
RESULTS = [
    ("d1", "A", "B", 110, 100),  # A wins
    ("d2", "B", "A", 90, 95),    # A wins (away)
    ("d3", "A", "C", 105, 100),  # A wins
]


def test_walk_forward_updates_ratings():
    rows, ratings = walk_forward(RESULTS, k_factor=20, hfa=50)
    assert len(rows) == len(RESULTS)
    # A keeps winning -> rating should climb above the 1500 start.
    assert ratings["A"] > 1500
    assert ratings["B"] < 1500


def test_first_game_feature_is_hfa_only():
    rows, _ = walk_forward(RESULTS, k_factor=20, hfa=50)
    diff, _exp, actual = rows[0]
    assert diff == pytest.approx(50)  # both start at 1500, +HFA
    assert actual == 1.0


def test_mean_log_loss_is_positive_finite():
    ll = mean_log_loss(RESULTS, k_factor=20, hfa=50)
    assert 0 < ll < 5

    def _empty():
        return mean_log_loss([], 20, 50)

    assert _empty() == 1.0
