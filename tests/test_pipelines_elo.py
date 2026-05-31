"""Shared Elo walk-forward used by the optimizer and trainer."""
import pytest

from sportsball.pipelines._elo import _mov_multiplier, mean_log_loss, walk_forward

# (date, home, away, home_score, away_score)
RESULTS = [
    ("d1", "A", "B", 110, 100),  # A wins
    ("d2", "B", "A", 90, 95),    # A wins (away)
    ("d3", "A", "C", 105, 100),  # A wins
]


def test_walk_forward_updates_ratings():
    rows, snaps = walk_forward(RESULTS, k_factor=20, hfa=50)
    assert len(rows) == len(RESULTS)
    # A keeps winning -> rating should climb above the 1500 start.
    assert snaps["A"].elo > 1500
    assert snaps["B"].elo < 1500


def test_first_game_feature_is_hfa_only():
    rows, _ = walk_forward(RESULTS, k_factor=20, hfa=50)
    # both teams start at 1500, so elo_diff_hfa == HFA and other features are neutral 0.
    assert rows[0].features[0] == pytest.approx(50)
    assert rows[0].actual == 1.0
    assert rows[0].features[1:] == [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]


def test_snapshots_have_full_shape():
    _, snaps = walk_forward(RESULTS, k_factor=20, hfa=50)
    a = snaps["A"]
    assert a.games_played == 3
    assert 0.0 <= a.form <= 1.0
    assert a.last_game_date is not None
    assert a.season is not None


def test_point_in_time_net_eff_accumulates():
    # A wins every game by a margin -> its season-to-date net_eff is positive.
    _, snaps = walk_forward(RESULTS, k_factor=20, hfa=50)
    assert snaps["A"].net_eff > 0
    assert snaps["B"].net_eff < 0


def test_first_game_net_eff_is_zero_no_leakage():
    # The first feature row's net_rating_diff (index 1) must be 0 — neither team
    # has a prior game this season, so no point-in-time margin is available.
    rows, _ = walk_forward(RESULTS, k_factor=20, hfa=50)
    assert rows[0].features[1] == 0.0


def test_net_eff_resets_across_seasons():
    games = [("2022-01-01", "A", "B", 120, 100),   # season 2021
             ("2023-01-01", "A", "B", 120, 100)]   # season 2022 (new) -> reset
    rows, snaps = walk_forward(games, 20, 50)
    # Game 2 is the first of season 2022 for both teams -> net_rating_diff 0.
    assert rows[1].features[1] == 0.0
    assert snaps["A"].season == 2022


def test_mov_multiplier_dampens_favorite_blowout():
    # Same margin: a heavily favored winner moves less than an even matchup.
    assert _mov_multiplier(20, 800) < _mov_multiplier(20, 0)
    # Bigger margin moves more.
    assert _mov_multiplier(30, 0) > _mov_multiplier(5, 0)


def test_carryover_regresses_after_offseason():
    # A beats B twice; one pair is months apart (offseason), one is days apart.
    gap = [("2023-01-01", "A", "B", 120, 100), ("2023-06-01", "A", "B", 120, 100)]
    near = [("2023-01-01", "A", "B", 120, 100), ("2023-01-03", "A", "B", 120, 100)]
    rows_gap, _ = walk_forward(gap, 20, 50, gap_days=90, carry=0.75)
    rows_near, _ = walk_forward(near, 20, 50, gap_days=90, carry=0.75)
    # Carryover pulls both ratings back toward 1500, shrinking game-2's differential.
    assert rows_gap[1].features[0] < rows_near[1].features[0]


def test_mean_log_loss_is_positive_finite():
    ll = mean_log_loss(RESULTS, k_factor=20, hfa=50)
    assert 0 < ll < 5
    assert mean_log_loss([], 20, 50) == 1.0
