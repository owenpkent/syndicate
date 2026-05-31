"""Canonical event identity: team normalization + cross-venue id alignment."""
from sportsball.matching import canonical_event_id, normalize_team


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
