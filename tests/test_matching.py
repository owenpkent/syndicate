"""Canonical event identity: team normalization + cross-venue id alignment."""
from datetime import date

from sportsball.matching import canonical_event_id, normalize_team, parse_event_date


class TestNormalizeTeam:
    def test_extracts_mascot(self):
        assert normalize_team("Los Angeles Lakers") == "lakers"
        assert normalize_team("Lakers") == "lakers"

    def test_two_word_mascot(self):
        assert normalize_team("Portland Trail Blazers") == "trailblazers"
        assert normalize_team("Boston Red Sox") == "redsox"

    def test_alias(self):
        assert normalize_team("LA Clippers") == "clippers"
        assert normalize_team("Los Angeles Clippers") == "clippers"


class TestCanonicalEventId:
    def test_deterministic_and_hyphen_free(self):
        eid = canonical_event_id("nba", "2024-01-15", "Lakers", "Celtics")
        assert eid == "nba_20240115_lakers_at_celtics"
        assert "-" not in eid  # safe as market_id EVENTID segment

    def test_different_venue_spellings_align(self):
        # nba_api full names vs Rundown short names -> same id for the same game.
        a = canonical_event_id("nba", "2024-01-15", "Los Angeles Lakers", "Boston Celtics")
        b = canonical_event_id("nba", "2024-01-15T19:30:00Z", "Lakers", "Celtics")
        assert a == b

    def test_home_away_order_matters(self):
        ha = canonical_event_id("nba", "2024-01-15", "Lakers", "Celtics")
        ah = canonical_event_id("nba", "2024-01-15", "Celtics", "Lakers")
        assert ha != ah


class TestParseEventDate:
    def test_roundtrips_canonical_id(self):
        eid = canonical_event_id("nba", "2024-01-15", "Lakers", "Celtics")
        assert parse_event_date(eid) == date(2024, 1, 15)

    def test_malformed_returns_none(self):
        assert parse_event_date("") is None
        assert parse_event_date("not_a_date_here") is None
        assert parse_event_date("nba_20241332_x_at_y") is None  # invalid month/day

