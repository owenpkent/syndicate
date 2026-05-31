"""Point-in-time roster strength: leakage-free season-to-date (no DuckDB/PG)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import precompute_roster_pit as prp  # noqa: E402


TEAM_SEASON = [
    {"team_name": "A", "season": "2024-25", "game_date": "2024-10-01",
     "player_name": "p1", "minutes": 30, "plus_minus": 12},
    {"team_name": "A", "season": "2024-25", "game_date": "2024-10-01",
     "player_name": "p2", "minutes": 28, "plus_minus": 6},
    {"team_name": "A", "season": "2024-25", "game_date": "2024-10-03",
     "player_name": "p1", "minutes": 30, "plus_minus": 4},
]


def test_first_game_of_season_is_zero_no_leakage():
    out = prp.roster_pit_rows(TEAM_SEASON)
    by_date = {gd: strength for _t, gd, _s, strength in out}
    assert by_date["2024-10-01"] == 0.0       # no prior games -> no info
    assert by_date["2024-10-03"] != 0.0       # reflects the 2024-10-01 games only


def test_one_row_per_team_game():
    out = prp.roster_pit_rows(TEAM_SEASON)
    assert {(t, gd) for t, gd, _s, _v in out} == {("A", "2024-10-01"), ("A", "2024-10-03")}


def test_new_season_resets():
    rows = TEAM_SEASON + [
        {"team_name": "A", "season": "2025-26", "game_date": "2025-10-01",
         "player_name": "p1", "minutes": 30, "plus_minus": 9},
    ]
    out = prp.roster_pit_rows(rows)
    by_date = {gd: strength for _t, gd, _s, strength in out}
    assert by_date["2025-10-01"] == 0.0  # first game of the new season -> 0
