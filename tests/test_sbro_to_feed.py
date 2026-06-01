"""SBRO export -> ingest_odds feed conversion: pure reshapers, no I/O."""
import pytest

from sportsball.matching import canonical_event_id
from sportsball.pipelines.ingest_odds import parse_file_feed
from sportsball.pipelines.sbro_to_feed import (
    SBRO_TEAMS,
    archive_date_to_iso,
    archive_json_to_records,
    sbro_date_to_iso,
    sbro_rows_to_records,
)


class TestSbroDate:
    def test_fall_months_use_start_year(self):
        assert sbro_date_to_iso("1031", 2023) == "2023-10-31"
        assert sbro_date_to_iso("1225", 2023) == "2023-12-25"

    def test_spring_months_roll_to_next_year(self):
        assert sbro_date_to_iso("103", 2023) == "2024-01-03"   # Jan 3
        assert sbro_date_to_iso("415", 2023) == "2024-04-15"   # Apr 15

    def test_garbage_is_none(self):
        assert sbro_date_to_iso("", 2023) is None
        assert sbro_date_to_iso("12", 2023) is None            # too short
        assert sbro_date_to_iso("1340", 2023) is None          # month 13


def _rows():
    # Two games, each a V row then an H row, classic SBRO column shape.
    return [
        {"Date": "1031", "VH": "V", "Team": "LALakers", "ML": "+130"},
        {"Date": "1031", "VH": "H", "Team": "Boston", "ML": "-150"},
        {"Date": "1101", "VH": "V", "Team": "GoldenState", "ML": "-110"},
        {"Date": "1101", "VH": "H", "Team": "Portland", "ML": "-110"},
    ]


class TestSbroRowsToRecords:
    def test_pairs_visitor_then_home(self):
        recs = sbro_rows_to_records(_rows(), 2023)
        assert len(recs) == 2
        g1 = recs[0]
        assert g1["home_team"] == "Boston Celtics"
        assert g1["away_team"] == "Los Angeles Lakers"
        assert g1["date"] == "2023-10-31"
        assert g1["home_close"] == "-150" and g1["away_close"] == "+130"

    def test_full_pipeline_keys_match_nba_api_events(self):
        # The converted record, run through ingest_odds, must produce the same
        # canonical_event_id an nba_api-ingested game would.
        recs = sbro_rows_to_records(_rows(), 2023)
        rows = parse_file_feed(recs)
        expected = canonical_event_id("nba", "2023-10-31",
                                      "Los Angeles Lakers", "Boston Celtics")
        assert rows[0][0] == expected
        assert "trailblazers" in rows[1][0]   # Portland two-word mascot resolved

    def test_missing_moneyline_skipped(self):
        rows = [
            {"Date": "1031", "VH": "V", "Team": "LALakers", "ML": "NL"},
            {"Date": "1031", "VH": "H", "Team": "Boston", "ML": "-150"},
        ]
        assert sbro_rows_to_records(rows, 2023) == []

    def test_neutral_or_unpaired_row_resets(self):
        rows = [
            {"Date": "1031", "VH": "V", "Team": "LALakers", "ML": "+130"},
            {"Date": "1031", "VH": "N", "Team": "Boston", "ML": "-150"},  # neutral, breaks pair
            {"Date": "1101", "VH": "H", "Team": "Portland", "ML": "-110"},  # H with no live V
        ]
        assert sbro_rows_to_records(rows, 2023) == []

    def test_every_current_team_label_maps(self):
        # All mapped labels reduce to a non-empty canonical token.
        from sportsball.matching import normalize_team
        for full in SBRO_TEAMS.values():
            assert normalize_team(full)


class TestArchiveJson:
    def test_archive_date(self):
        assert archive_date_to_iso(20111225.0) == "2011-12-25"
        assert archive_date_to_iso(20220613) == "2022-06-13"
        assert archive_date_to_iso(None) is None
        assert archive_date_to_iso("garbage") is None

    def test_reshapes_pre_joined_records(self):
        recs = archive_json_to_records([
            {"season": 2011, "date": 20111225.0, "home_team": "Knicks",
             "away_team": "Celtics", "home_close_ml": -210, "away_close_ml": 185},
        ])
        assert recs == [{"sport": "nba", "date": "2011-12-25",
                         "home_team": "Knicks", "away_team": "Celtics",
                         "home_close": -210, "away_close": 185}]

    def test_quirky_city_tokens_fixed_for_matching(self):
        from sportsball.matching import normalize_team
        recs = archive_json_to_records([
            {"date": 20120101.0, "home_team": "Golden State", "away_team": "Oklahoma City",
             "home_close_ml": -150, "away_close_ml": 130},
            {"date": 20120102.0, "home_team": "Seventysixers", "away_team": "NewJersey",
             "home_close_ml": -120, "away_close_ml": 100},
        ])
        assert normalize_team(recs[0]["home_team"]) == "warriors"
        assert normalize_team(recs[0]["away_team"]) == "thunder"
        assert normalize_team(recs[1]["home_team"]) == "76ers"
        assert normalize_team(recs[1]["away_team"]) == "nets"

    def test_junk_rows_dropped(self):
        recs = archive_json_to_records([
            {"date": 20120621.0, "home_team": 0, "away_team": "Thunder",
             "home_close_ml": 0, "away_close_ml": 140},               # 0 team + 0 ml
            {"date": 20120101.0, "home_team": "Heat", "away_team": "Bulls",
             "home_close_ml": -130, "away_close_ml": None},           # missing away ml
        ])
        assert recs == []
