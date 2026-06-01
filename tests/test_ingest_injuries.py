"""Point-in-time availability: pure, leakage-free roster scoring."""
import pytest

from sportsball.pipelines.ingest_injuries import available_strength, availability_rows


def _p(team, date, player, minutes, pm, season="2024-25"):
    return {"team_name": team, "season": season, "game_date": date,
            "player_name": player, "minutes": minutes, "plus_minus": pm}


def test_available_strength_empty_is_zero():
    assert available_strength([]) == 0.0
    assert available_strength([{"minutes": 0, "plus_minus": 5}]) == 0.0


def test_available_strength_minutes_weighted():
    # 36 min @ +10 cumulative: per-min 10/36, *36/100 = 0.10
    val = available_strength([{"minutes": 36, "plus_minus": 10}])
    assert val == pytest.approx(0.10, abs=1e-4)


def test_first_game_has_no_prior_so_zero():
    rows = availability_rows([_p("LAL", "2024-10-01", "A", 36, 10)])
    # one team-game, scored from prior (none) -> 0.0
    assert rows == [("LAL", "2024-10-01", "2024-25", 0.0)]


def test_uses_only_prior_games_no_leakage():
    rows = availability_rows([
        _p("LAL", "2024-10-01", "A", 36, 12),  # game 1: builds A's prior
        _p("LAL", "2024-10-03", "A", 36, -4),  # game 2: scored from game-1 prior only
    ])
    by_date = {d: v for _, d, _, v in rows}
    assert by_date["2024-10-01"] == 0.0                      # no prior
    assert by_date["2024-10-03"] == pytest.approx(0.12)      # 12/36*36/100, game-2 result ignored


def test_absent_star_lowers_availability():
    # Star A (huge prior value) plays game 2 but sits game 3; availability drops.
    rows = availability_rows([
        _p("LAL", "2024-10-01", "A", 36, 30), _p("LAL", "2024-10-01", "B", 36, 2),
        _p("LAL", "2024-10-03", "A", 36, 30), _p("LAL", "2024-10-03", "B", 36, 2),
        # game 3: only B suits up (A is out)
        _p("LAL", "2024-10-05", "B", 36, 2),
    ])
    by_date = {d: v for _, d, _, v in rows}
    # With A present (game 3 counterfactual) availability would be much higher;
    # with only B, it reflects B's modest prior value.
    assert by_date["2024-10-05"] < by_date["2024-10-03"]
