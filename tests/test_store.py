"""Repository layer: id parsing, side resolution, and query/DML mapping."""
import pytest

from sportsball.store import AWAY, HOME, PendingTrade, Store, parse_market_id, side_for

from fakes import FakeDB


class TestParsing:
    def test_basic(self):
        assert parse_market_id("RUNDOWN-123-Celtics") == ("RUNDOWN", "123", "Celtics")

    def test_participant_may_contain_hyphens(self):
        assert parse_market_id("POLY-99-Red-Sox") == ("POLY", "99", "Red-Sox")

    def test_malformed_raises(self):
        with pytest.raises(ValueError):
            parse_market_id("NOPE")

    def test_side_resolution(self):
        assert side_for("Celtics", "Celtics") == HOME
        assert side_for("Lakers", "Celtics") == AWAY


class TestStoreDML:
    def test_record_signal_targets_signals_table(self):
        store = Store(FakeDB(available=True))
        store.record_signal("E1", HOME, "MOCK", 2.0, 0.55, 0.1)
        sql, params = store.db.executed[0]
        assert "INSERT INTO signals" in sql
        assert params[:3] == ("E1", "HOME", "MOCK")

    def test_record_trade_includes_market_id(self):
        store = Store(FakeDB(available=True))
        store.record_trade("E1", HOME, "MOCK", 1.95, 0.05, "OPEN", market_id="MOCK-E1-X")
        sql, params = store.db.executed[0]
        assert "INSERT INTO trades" in sql
        assert "MOCK-E1-X" in params

    def test_pending_settlements_maps_rows(self):
        row = (7, "HOME", 2.0, 0.05, "MOCK-E1-X", 110, 100)
        store = Store(FakeDB(available=True, rows=[row]))
        pending = store.pending_settlements()
        assert pending == [PendingTrade(*row)]
        assert pending[0].market_id == "MOCK-E1-X"
