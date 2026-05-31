"""Analytics Engine signal processing — abstain/EV/risk/arb on the store contract."""
import pytest

from sportsball.agents.engine import process_signal
from sportsball.broker import EXECUTION_SIGNALS
from sportsball.config import StrategyConfig
from sportsball.quant.arbitrage import ArbitrageEngine
from sportsball.store import Store

from fakes import FakeBroker, FakeBundle, FakeDB

STRAT = StrategyConfig(safety_buffer_ev=0.02, kelly_multiplier=0.25, require_model=True)


def _signal(market_id="MOCK-E1-Celtics", odds=2.0, participant="Celtics"):
    return {
        "market_id": market_id,
        "odds": odds,
        "metadata": {"source": "MOCK", "matchup": "Lakers @ Celtics", "participant": participant},
    }


def _run(data, bundle, broker=None, db=None, arb=None, strat=STRAT):
    broker = broker or FakeBroker()
    store = Store(db or FakeDB(available=False))
    process_signal(data, bundle=bundle, store=store, broker=broker,
                   arb=arb or ArbitrageEngine(), strategy=strat)
    return broker, store


class TestAbstain:
    def test_no_model_no_trade(self):
        broker, _ = _run(_signal(), bundle=None)
        assert broker.pushed[EXECUTION_SIGNALS] == []

    def test_no_model_persists_nothing(self):
        _, store = _run(_signal(), bundle=None, db=FakeDB(available=True))
        assert store.db.executed == []  # abstains before any write


class TestValueSignal:
    def test_positive_edge_emits_enriched_execution(self):
        broker, _ = _run(_signal(odds=2.0), bundle=FakeBundle(0.6))  # EV = 0.2
        pushed = broker.pushed[EXECUTION_SIGNALS]
        assert len(pushed) == 1
        msg = pushed[0]
        assert msg["market_id"] == "MOCK-E1-Celtics"
        assert msg["event_id"] == "E1"
        assert msg["side"] == "HOME"          # Celtics is home in "Lakers @ Celtics"
        assert msg["fraction"] == pytest.approx(0.05)

    def test_records_event_and_signal(self):
        _, store = _run(_signal(odds=2.0), bundle=FakeBundle(0.6), db=FakeDB(available=True))
        sql = " ".join(s for s, _ in store.db.executed)
        assert "INSERT INTO events" in sql
        assert "INSERT INTO signals" in sql

    def test_below_buffer_logs_but_no_execution(self):
        broker, store = _run(_signal(odds=2.0), bundle=FakeBundle(0.5), db=FakeDB(available=True))
        assert broker.pushed[EXECUTION_SIGNALS] == []
        assert any("INSERT INTO signals" in s for s, _ in store.db.executed)


class TestArbitrage:
    def test_two_sided_overlay_emits_arbitrage(self):
        arb, broker = ArbitrageEngine(), FakeBroker()
        _run(_signal("MOCK-E1-Celtics", 2.10, "Celtics"), bundle=None, broker=broker, arb=arb)
        _run(_signal("MOCK-E1-Lakers", 2.10, "Lakers"), bundle=None, broker=broker, arb=arb)
        arbs = [m for m in broker.pushed[EXECUTION_SIGNALS] if m.get("type") == "ARBITRAGE"]
        assert len(arbs) == 1
        assert arbs[0]["event_id"] == "E1"
        assert {leg["side"] for leg in arbs[0]["legs"]} == {"HOME", "AWAY"}
