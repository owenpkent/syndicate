"""Closing-odds ingestion: pure parsers + the store UPDATE path."""
import pytest

from sportsball.matching import canonical_event_id
from sportsball.pipelines.ingest_odds import (
    VIG_MAX,
    VIG_MIN,
    _to_decimal,
    apply_closing_odds,
    parse_file_feed,
    parse_odds_api,
    passes_vig_guard,
)
from sportsball.store import Store

from fakes import FakeDB


class TestToDecimal:
    def test_passthrough_decimal(self):
        assert _to_decimal(2.10) == 2.10
        assert _to_decimal("1.91") == 1.91

    def test_american_positive(self):
        assert _to_decimal(150) == pytest.approx(2.50)
        assert _to_decimal(100) == pytest.approx(2.00)

    def test_american_negative(self):
        assert _to_decimal(-110) == pytest.approx(1.9091, abs=1e-4)

    def test_garbage_is_none(self):
        assert _to_decimal(None) is None
        assert _to_decimal("x") is None
        assert _to_decimal(0) is None


class TestParseFileFeed:
    def test_canonical_keying_and_pairs(self):
        rows = parse_file_feed([
            {"sport": "nba", "date": "2024-01-15", "home_team": "Celtics",
             "away_team": "Lakers", "home_close": 1.80, "away_close": 2.10},
        ])
        assert rows == [(canonical_event_id("nba", "2024-01-15", "Lakers", "Celtics"), 1.80, 2.10)]

    def test_short_team_keys_and_american_prices(self):
        rows = parse_file_feed([
            {"sport": "nba", "date": "2024-01-15", "home": "Celtics",
             "away": "Lakers", "home_close": -150, "away_close": 130},
        ])
        eid, hc, ac = rows[0]
        assert hc == pytest.approx(1.6667, abs=1e-4)
        assert ac == pytest.approx(2.30)

    def test_incomplete_records_skipped(self):
        rows = parse_file_feed([
            {"sport": "nba", "date": "2024-01-15", "home_team": "Celtics"},  # no away/odds
            {"home_team": "A", "away_team": "B", "home_close": 2.0, "away_close": 2.0},  # no date
        ])
        assert rows == []

    def test_implausible_vig_rejected(self):
        # Two near-evens both ~2.0 imply a ~1.0 overround (a mis-pair / arb), and
        # two short favorites (1.5/1.5 -> 1.33) imply impossible juice. Both drop.
        rows = parse_file_feed([
            {"sport": "nba", "date": "2024-01-15", "home_team": "Celtics",
             "away_team": "Lakers", "home_close": 2.0, "away_close": 2.0},
            {"sport": "nba", "date": "2024-01-16", "home_team": "Heat",
             "away_team": "Bulls", "home_close": 1.5, "away_close": 1.5},
        ])
        assert rows == []


class TestVigGuard:
    def test_typical_juice_passes(self):
        assert passes_vig_guard(1.91, 1.91)        # -110/-110, overround ~1.048
        assert passes_vig_guard(1.6667, 2.30)      # -150/+130, ~1.035

    def test_arb_and_overjuice_rejected(self):
        assert not passes_vig_guard(2.05, 2.05)    # ~0.976: below 1.0, mis-paired
        assert not passes_vig_guard(1.5, 1.5)      # ~1.333: impossible vig

    def test_just_inside_and_outside_band(self):
        # A hair inside each bound passes; a hair outside fails.
        assert VIG_MIN < 1.02 < VIG_MAX           # sanity: real juice lives inside
        assert passes_vig_guard(2 / 1.05, 2 / 1.05)   # sum ~1.05, mid-band
        assert not passes_vig_guard(2 / 1.005, 2 / 1.005)  # sum ~1.005, below floor
        assert not passes_vig_guard(2 / 1.15, 2 / 1.15)    # sum ~1.15, above ceiling


class TestParseOddsApi:
    def test_median_across_books(self):
        raw = [{
            "home_team": "Boston Celtics", "away_team": "Los Angeles Lakers",
            "commence_time": "2024-01-15T23:30:00Z",
            "bookmakers": [
                {"key": "a", "markets": [{"key": "h2h", "outcomes": [
                    {"name": "Boston Celtics", "price": 1.80},
                    {"name": "Los Angeles Lakers", "price": 2.05}]}]},
                {"key": "b", "markets": [{"key": "h2h", "outcomes": [
                    {"name": "Boston Celtics", "price": 1.90},
                    {"name": "Los Angeles Lakers", "price": 2.15}]}]},
                {"key": "c", "markets": [{"key": "h2h", "outcomes": [
                    {"name": "Boston Celtics", "price": 1.85},
                    {"name": "Los Angeles Lakers", "price": 2.10}]}]},
            ],
        }]
        rows = parse_odds_api(raw, "nba")
        eid = canonical_event_id("nba", "2024-01-15", "Los Angeles Lakers", "Boston Celtics")
        assert rows == [(eid, 1.85, 2.10)]  # medians

    def test_skips_event_missing_a_side(self):
        raw = [{"home_team": "C", "away_team": "L", "commence_time": "2024-01-15T00:00:00Z",
                "bookmakers": [{"key": "a", "markets": [{"key": "h2h", "outcomes": [
                    {"name": "C", "price": 1.8}]}]}]}]  # only home priced
        assert parse_odds_api(raw) == []


def test_apply_closing_odds_issues_updates():
    store = Store(FakeDB(available=True))
    n = apply_closing_odds(store, [("nba_20240115_lakers_at_celtics", 1.80, 2.10)])
    assert n == 1
    sql = " ".join(s for s, _ in store.db.executed)
    assert "UPDATE events SET home_close" in sql


def test_apply_closing_odds_duckdb_only_touches_known_events(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    from sportsball.pipelines.ingest_odds import apply_closing_odds_duckdb

    db = tmp_path / "t.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE events (event_id VARCHAR, home_close DOUBLE, away_close DOUBLE)")
    con.execute("INSERT INTO events VALUES ('nba_20240115_lakers_at_celtics', NULL, NULL)")
    con.close()

    matched, total = apply_closing_odds_duckdb(str(db), [
        ("nba_20240115_lakers_at_celtics", 1.80, 2.10),   # known -> updated
        ("nba_20240115_heat_at_bulls", 1.95, 1.95),       # unknown -> skipped
    ])
    assert (matched, total) == (1, 2)

    con = duckdb.connect(str(db), read_only=True)
    row = con.execute("SELECT home_close, away_close FROM events "
                      "WHERE event_id = 'nba_20240115_lakers_at_celtics'").fetchone()
    con.close()
    assert row == (1.80, 2.10)
