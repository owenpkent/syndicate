"""Cross-venue arbitrage detection."""
import pytest

from sportsball.quant.arbitrage import ArbitrageEngine


def _seed(engine, event_id, home_odds, away_odds):
    engine.update_odds(f"BOOKA-{event_id}-Home", home_odds, "BookA", "Home")
    engine.update_odds(f"BOOKB-{event_id}-Away", away_odds, "BookB", "Away")


def test_detects_guaranteed_profit():
    e = ArbitrageEngine()
    _seed(e, "E1", 2.10, 2.10)
    opp = e.check_arbitrage("E1")
    assert opp is not None
    assert opp["profit_margin"] == pytest.approx(1 - (1 / 2.10 + 1 / 2.10))
    assert sum(leg["allocation"] for leg in opp["legs"]) == pytest.approx(1.0)


def test_no_arb_with_overround():
    e = ArbitrageEngine()
    _seed(e, "E2", 1.90, 1.90)
    assert e.check_arbitrage("E2") is None


def test_keeps_best_price_per_side():
    e = ArbitrageEngine()
    e.update_odds("BOOKA-E3-Home", 1.95, "BookA", "Home")
    e.update_odds("BOOKC-E3-Home", 2.20, "BookC", "Home")
    e.update_odds("BOOKA-E3-Home", 1.80, "BookA", "Home")
    assert e.order_book["E3"]["Home"]["odds"] == 2.20
    assert e.order_book["E3"]["Home"]["source"] == "BookC"


def test_incomplete_book_returns_none():
    e = ArbitrageEngine()
    e.update_odds("BOOKA-E4-Home", 2.10, "BookA", "Home")
    assert e.check_arbitrage("E4") is None


def test_malformed_market_id_ignored():
    e = ArbitrageEngine()
    assert e.update_odds("BADID", 2.0, "BookA", "Home") is None
    assert e.update_odds("SRC-EVT-TEAM", 2.0, "BookA", "Home") == "EVT"
