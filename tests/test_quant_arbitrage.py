"""Cross-venue arbitrage detection + best-line book."""
import pytest

from sportsball.quant.arbitrage import ArbitrageEngine
from sportsball.matching import canonical_event_id


def _seed(engine, event_id, home_odds, away_odds):
    """Two venues quoting opposite sides of one game (HOME/AWAY participants)."""
    engine.update_odds(f"BOOKA-{event_id}-HOME", home_odds, "BookA", "HOME")
    engine.update_odds(f"BOOKB-{event_id}-AWAY", away_odds, "BookB", "AWAY")


def test_detects_guaranteed_profit():
    e = ArbitrageEngine()
    _seed(e, "E1", 2.10, 2.10)
    opp = e.check_arbitrage("E1")
    assert opp is not None
    assert opp["profit_margin"] == pytest.approx(1 - (1 / 2.10 + 1 / 2.10))
    assert sum(leg["allocation"] for leg in opp["legs"]) == pytest.approx(1.0)
    assert {leg["side"] for leg in opp["legs"]} == {"HOME", "AWAY"}


def test_no_arb_with_overround():
    e = ArbitrageEngine()
    _seed(e, "E2", 1.90, 1.90)
    assert e.check_arbitrage("E2") is None


def test_keeps_best_price_per_team():
    e = ArbitrageEngine()
    e.update_odds("BOOKA-E3-Lakers", 1.95, "BookA", "AWAY")
    e.update_odds("BOOKC-E3-Lakers", 2.20, "BookC", "AWAY")
    e.update_odds("BOOKA-E3-Lakers", 1.80, "BookA", "AWAY")
    best = e.best("E3", "lakers")
    assert best["odds"] == 2.20
    assert best["source"] == "BookC"


def test_incomplete_book_returns_none():
    e = ArbitrageEngine()
    e.update_odds("BOOKA-E4-Lakers", 2.10, "BookA", "AWAY")
    assert e.check_arbitrage("E4") is None


def test_malformed_market_id_ignored():
    e = ArbitrageEngine()
    assert e.update_odds("BADID", 2.0, "BookA", "HOME") is None
    assert e.update_odds("SRC-EVT-TEAM", 2.0, "BookA", "HOME") == "EVT"


def test_aligns_reversed_orientation_across_venues():
    """Polymarket exposes no home/away, so the same game can arrive with the
    teams in either order. The order-independent key must still let the two
    venues' prices meet in one book and trigger arbitrage."""
    e = ArbitrageEngine()
    # Oracle: Lakers @ Celtics (away_at_home).
    eid_oracle = canonical_event_id("nba", "2024-01-15", "Lakers", "Celtics")
    # Polymarket: outcomes in the other order -> Celtics @ Lakers event_id.
    eid_poly = canonical_event_id("nba", "2024-01-15", "Celtics", "Lakers")
    assert eid_oracle != eid_poly  # oriented ids genuinely differ

    k1 = e.update_odds(f"RUNDOWN-{eid_oracle}-Celtics", 2.10, "Rundown", "HOME")
    k2 = e.update_odds(f"POLY-{eid_poly}-Lakers", 2.10, "Polymarket", "AWAY")
    assert k1 == k2  # collapsed onto one order-independent matchup key

    opp = e.check_arbitrage(k1)
    assert opp is not None
    assert {leg["source"] for leg in opp["legs"]} == {"Rundown", "Polymarket"}
