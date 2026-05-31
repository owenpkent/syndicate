"""nba_api game-log pairing into events (pure; no nba_api/network)."""
from sportsball.pipelines.ingest_nba import build_games


def _rows():
    # Two rows per GAME_ID: home has "vs.", away has "@".
    return [
        {"GAME_ID": "1", "GAME_DATE": "2024-01-15", "TEAM_NAME": "Boston Celtics",
         "MATCHUP": "BOS vs. LAL", "PTS": 110},
        {"GAME_ID": "1", "GAME_DATE": "2024-01-15", "TEAM_NAME": "Los Angeles Lakers",
         "MATCHUP": "LAL @ BOS", "PTS": 100},
    ]


def test_pairs_rows_into_one_game():
    games = build_games(_rows())
    assert len(games) == 1
    g = games[0]
    assert g.home_team == "Boston Celtics" and g.away_team == "Los Angeles Lakers"
    assert g.home_score == 110 and g.away_score == 100
    assert g.event_id == "nba_20240115_lakers_at_celtics"


def test_drops_unpaired_or_scoreless_games():
    rows = _rows()
    rows.append({"GAME_ID": "2", "GAME_DATE": "2024-01-16", "TEAM_NAME": "Heat",
                 "MATCHUP": "MIA vs. NYK", "PTS": None})  # only one row, no score
    games = build_games(rows)
    assert {g.event_id for g in games} == {"nba_20240115_lakers_at_celtics"}
