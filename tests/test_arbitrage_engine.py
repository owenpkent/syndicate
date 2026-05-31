"""Unit tests for the cross-venue arbitrage detector."""
import pytest

from arbitrage_engine import ArbitrageEngine


def _seed_two_sided(engine, event_id, home_odds, away_odds):
    engine.update_odds(f"BOOKA-{event_id}-Home", home_odds, "BookA", "Home")
    engine.update_odds(f"BOOKB-{event_id}-Away", away_odds, "BookB", "Away")


class TestArbitrageDetection:
    def test_detects_guaranteed_profit(self):
        # 1/2.10 + 1/2.10 = 0.952 < 1  -> ~4.76% locked margin.
        engine = ArbitrageEngine()
        _seed_two_sided(engine, "E1", 2.10, 2.10)
        opp = engine.check_arbitrage("E1")
        assert opp is not None
        assert opp["profit_margin"] == pytest.approx(1 - (1 / 2.10 + 1 / 2.10))
        # Allocations across legs always sum to 1 (full stake distributed).
        assert sum(leg["allocation"] for leg in opp["legs"]) == pytest.approx(1.0)

    def test_no_arbitrage_when_overround_present(self):
        # 1/1.90 + 1/1.90 = 1.05 > 1  -> the book has a margin, no arb.
        engine = ArbitrageEngine()
        _seed_two_sided(engine, "E2", 1.90, 1.90)
        assert engine.check_arbitrage("E2") is None

    def test_keeps_best_price_per_side(self):
        engine = ArbitrageEngine()
        engine.update_odds("BOOKA-E3-Home", 1.95, "BookA", "Home")
        engine.update_odds("BOOKC-E3-Home", 2.20, "BookC", "Home")  # better
        engine.update_odds("BOOKA-E3-Home", 1.80, "BookA", "Home")  # worse, ignored
        assert engine.order_book["E3"]["Home"]["odds"] == 2.20
        assert engine.order_book["E3"]["Home"]["source"] == "BookC"

    def test_incomplete_book_returns_none(self):
        engine = ArbitrageEngine()
        engine.update_odds("BOOKA-E4-Home", 2.10, "BookA", "Home")
        assert engine.check_arbitrage("E4") is None  # no Away side yet

    def test_malformed_market_id_is_ignored(self):
        engine = ArbitrageEngine()
        # Fewer than 3 "-" parts: update_odds bails out without recording.
        assert engine.update_odds("BADID", 2.0, "BookA", "Home") is None
